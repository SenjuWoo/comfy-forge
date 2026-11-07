"""Execute the shipped four-way HaDeS workflow through ComfyUI's real API.

This runs `workflows/anima-4way-merge.json` itself - the file a user opens - not a
graph rebuilt in Python. It converts the UI workflow to the API format, submits
it, checks the saved checkpoint, makes it visible to `UNETLoader`, renders from
the reloaded file and verifies real pixels.

Nothing pre-existing is deleted. Every artifact this run creates carries a unique
name and is removed at the end unless `--keep-artifacts` is given.

Exit codes: 0 PASS, 1 FAIL, 3 UNVERIFIED (no backend / models missing).

    python TESTS/integration_merge_graph.py --url http://127.0.0.1:7821
    python TESTS/integration_merge_graph.py --dry-run
"""
import argparse
import io
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workflow_to_api import convert  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WORKFLOW = os.path.join(REPO, "workflows", "anima-4way-merge.json")
# UI-only classes that the backend has no node for.
NOT_API = {"Note"}


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url, payload, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_for(base, pid, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = get(base + "/history/" + pid)
        if pid in hist:
            return hist[pid]
        time.sleep(5)
    raise SystemExit("FAIL %s did not finish within %ds" % (what, timeout))


def check_error(entry, what):
    if entry.get("status", {}).get("status_str") != "error":
        return
    for m in entry["status"].get("messages", []):
        if m[0] == "execution_error":
            d = m[1]
            raise SystemExit("FAIL %s node %s (%s): %s: %s" %
                             (what, d.get("node_id"), d.get("node_type"),
                              d.get("exception_type"), d.get("exception_message")))
    raise SystemExit("FAIL %s reported an error without detail" % what)


RUN_STEM = "comfy-kit-4way-%s"


def _name_ok(path, stem):
    """Cheap ownership proof: the saver prefix was set by this run and nothing else uses it."""
    if not path or os.path.islink(path):
        return False
    return os.path.basename(path).startswith(stem)


def infer_output_root(path):
    """Trusted root for a backend-reported path: the output directory of a ComfyUI tree.

    Walks up from the reported file until it finds a directory named `output` that
    has a sibling `models` directory, and refuses anything else. The backend is not
    trusted to name a location: this only accepts the layout ComfyUI itself uses,
    and `--output-root` overrides it when a user runs a nonstandard tree.
    """
    current = os.path.dirname(os.path.abspath(path))
    while current and os.path.dirname(current) != current:
        if (os.path.basename(current).lower() == "output"
                and os.path.isdir(os.path.join(os.path.dirname(current), "models"))):
            # Real path, so a symlinked temp root (the common case on CI) compares
            # equal to the paths claim_saved canonicalises.
            return os.path.realpath(current)
        current = os.path.dirname(current)
    return None


def claim_saved(saved, run_id, root):
    """Pick the one saved_path that belongs to this run, or refuse.

    The backend reports whatever it wrote. Deleting that on trust means a mocked or
    buggy backend can hand us a user's model, so ownership needs two proofs: the
    unique saver prefix this run injected, and containment in a root this process
    derived itself. No root, no claim - the parameter has no default on purpose.
    """
    if not root:
        return None, "no trusted output root: refusing to claim any reported path"
    stem = RUN_STEM % run_id
    real_root = os.path.realpath(root)
    matches = []
    for path in saved:
        if not _name_ok(path, stem):
            continue
        real = os.path.realpath(path)
        if os.path.dirname(real) != real_root:
            continue
        if not os.path.isfile(real):
            continue
        matches.append(real)
    if not matches:
        return None, ("no saved_path starts with %r; refusing to delete anything (reported: %s)"
                      % (stem, ", ".join(saved) or "none"))
    if len(matches) > 1:
        return None, "ambiguous: %d saved paths match this run (%s)" % (len(matches), ", ".join(matches))
    return matches[0], None


def preview_path(img, output_root, run_id):
    """The SaveImage preview this run is about to create, before it is downloaded.

    Registered for cleanup ahead of the HTTP call: a failed download must not leave
    the preview on disk. Returns None when the image is not demonstrably ours - the
    backend supplies this filename, so a simple basename inside the trusted output
    directory is required, not merely a matching suffix.
    """
    if not img or img.get("subfolder") or img.get("type", "output") != "output":
        return None
    name = str(img.get("filename", ""))
    if not name or name != os.path.basename(name) or ".." in name.split("."):
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    candidate = os.path.join(output_root, name)
    if os.path.dirname(os.path.realpath(candidate)) != os.path.realpath(output_root):
        return None
    return candidate if _name_ok(candidate, RUN_STEM % run_id + "-render") else None


def cleanup(paths, keep):
    """Remove only the paths this run created. Returns the ones that survived, with the reason."""
    failures = []
    for path in paths:
        if keep:
            print("note kept     %s" % path)
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append((path, str(exc)))
            print("WARN could not remove %s (%s)" % (path, exc))
    return failures


def expose(saved_path, models_dir, name, same_volume=None, writer=None):
    """Make the saved merge visible to UNETLoader under a name nobody owns.

    Refuses an existing file outright. A hard link shares the bytes on the same
    volume; on another volume the copy is a full second checkpoint, so the free
    space is checked first.

    Cleanup belongs to whoever created the file. This function never unlinks the
    destination: if the exclusive open loses a race, the winner's file is not ours
    and survives. A copy that dies midway removes its own partial inside
    _copy_exclusive, which is the only code that knows it created one.

    same_volume and writer are overridden only by tests; production derives both.
    """
    visible = os.path.join(models_dir, name)
    if os.path.exists(visible):
        raise FileExistsError("refusing to overwrite an existing model: %s" % visible)
    os.makedirs(models_dir, exist_ok=True)
    if same_volume is None:
        same_volume = os.stat(saved_path).st_dev == os.stat(models_dir).st_dev
    if same_volume:
        os.link(saved_path, visible)
        return visible, "hard link"
    need = os.path.getsize(saved_path) / 1e9 * 1.1
    if shutil.disk_usage(models_dir).free / 1e9 < need:
        raise OSError("copying %.1f GB needs free space on %s" % (need, models_dir))
    (writer or _copy_exclusive)(saved_path, visible)
    return visible, "copy"


def _copy_exclusive(src, dst, chunk=4 * 1024 * 1024, fail_after=None):
    """Copy into a destination that must not exist yet.

    O_CREAT|O_EXCL is the actual guard: the earlier os.path.exists() check only
    produces a nicer message, and between the two another process can create the
    file. Exclusive creation makes the second writer lose instead of overwriting.

    If the open loses, FileExistsError propagates and nothing is deleted - the file
    belongs to the winner. A failure after we created the file removes our own
    partial, because this is the only code that knows it exists.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(dst, flags, 0o666)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            copied = 0
            while True:
                block = source.read(chunk)
                if not block:
                    break
                if fail_after is not None and copied + len(block) > fail_after:
                    raise OSError("injected copy failure after %d bytes" % copied)
                out.write(block)
                copied += len(block)
    except BaseException:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise
        shutil.copystat(src, dst)
    except BaseException:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7821"))
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--models-dir", default=os.environ.get("COMFYUI_UNET_DIR"),
                    help="backend models/diffusion_models; derived from the saved file if omitted")
    ap.add_argument("--donors", default="",
                    help="comma list overriding the donor loaders, in graph order")
    ap.add_argument("--output-root", default=os.environ.get("COMFYUI_OUTPUT_DIR"),
                    help="trusted ComfyUI output directory; derived from the tree when omitted")
    ap.add_argument("--keep-artifacts", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    run_id = uuid.uuid4().hex[:8]

    try:
        info = get(base + "/object_info", timeout=90)
    except Exception as e:
        print("UNVERIFIED: no ComfyUI at %s (%s)" % (base, e.__class__.__name__))
        return 3

    workflow = json.load(open(args.workflow, encoding="utf-8"))
    prompt, skipped = convert(workflow)
    prompt = {k: v for k, v in prompt.items() if v["class_type"] not in NOT_API}

    missing = sorted({v["class_type"] for v in prompt.values()} - set(info))
    if missing:
        print("FAIL the backend does not register: %s" % ", ".join(missing))
        return 1

    loaders = sorted((k for k, v in prompt.items() if v["class_type"] == "HaDeSAnimaModelLoader"),
                     key=int)
    donors = [d for d in args.donors.split(",") if d]
    if donors:
        if len(donors) != len(loaders):
            print("FAIL --donors needs %d names, got %d" % (len(loaders), len(donors)))
            return 1
        for nid, name in zip(loaders, donors):
            prompt[nid]["inputs"]["unet_name"] = name

    available = info["HaDeSAnimaModelLoader"]["input"]["required"]["unet_name"][0]
    for nid in loaders:
        name = prompt[nid]["inputs"]["unet_name"]
        if name not in available:
            print("UNVERIFIED: %s is not installed on this backend" % name)
            return 3

    saver_ids = [k for k, v in prompt.items() if v["class_type"] == "HaDeSAnimaModelSaver"]
    if not saver_ids:
        print("FAIL the shipped workflow has no saver node; the merge cannot be proven")
        return 1
    # Unique per run: two runs must never land on the same output file.
    for sid in saver_ids:
        prompt[sid]["inputs"]["filename_prefix"] = "comfy-kit-4way-%s" % run_id

    merger = next(v for v in prompt.values() if v["class_type"] == "HaDeSAnimaMultiModelMerger")
    sampler = next((v for v in prompt.values() if v["class_type"] == "HaDeSAnimaKSampler"), None)
    print("ok   workflow  %s (%d nodes, %d loaders)" %
          (os.path.basename(args.workflow), len(prompt), len(loaders)))
    print("ok   recipe    %s scale=%s weights=%s" %
          (merger["inputs"]["method"], merger["inputs"]["scale"],
           [merger["inputs"][k] for k in ("weight_1", "weight_2", "weight_3", "weight_4")]))
    if sampler:
        print("ok   sampler   %s / %s" % (sampler["inputs"]["sampler_name"],
                                          sampler["inputs"]["scheduler"]))

    if args.dry_run:
        print(json.dumps(prompt, indent=2))
        print("ok   graph validates against the live node schemas")
        return 0

    print("note submitting the shipped merge graph (loads five models)")
    pid = post(base + "/prompt", {"prompt": prompt, "client_id": "comfy-kit"})["prompt_id"]
    print("ok   submitted %s" % pid)
    entry = wait_for(base, pid, args.timeout, "the merge")
    check_error(entry, "merge")

    saved = [p for o in entry.get("outputs", {}).values() for p in o.get("saved_path", [])]
    if not saved:
        print("FAIL the saver reported no saved_path: %s" % json.dumps(entry.get("outputs"))[:300])
        return 1
    output_root = args.output_root or infer_output_root(saved[0])
    if not output_root:
        print("FAIL cannot establish a trusted output directory for the reported path(s); "
              "pass --output-root. Reported: %s" % ", ".join(saved))
        return 1
    saved_path, why = claim_saved(saved, run_id, output_root)
    if saved_path is None:
        print("FAIL %s" % why)
        return 1
    print("ok   saved     %s (%.2f GB)" % (saved_path, os.path.getsize(saved_path) / 1e9))

    # From here on the saved file exists and is provably this run's (unique prefix),
    # so it is ours to remove. Registered immediately: every failure below takes it too.
    created = [saved_path]
    leftovers = []
    try:
        try:
            from safetensors import safe_open
            with safe_open(saved_path, framework="pt") as f:
                names = list(f.keys())
        except Exception as e:
            print("FAIL the saved file is not a loadable safetensors checkpoint: %s" % e)
            return 1
        print("ok   tensors   %d, e.g. %s" % (len(names), names[0]))

        models_dir = args.models_dir
        if not models_dir:
            models_dir = os.path.join(os.path.dirname(os.path.dirname(saved_path)),
                                      "models", "diffusion_models")
        visible_name = "comfy-kit-test-%s.safetensors" % run_id
        try:
            visible, how = expose(saved_path, models_dir, visible_name)
        except FileExistsError as e:
            print("FAIL %s" % e)
            return 1
        except OSError as e:
            print("FAIL could not expose the merge to UNETLoader: %s" % e)
            return 1
        created.append(visible)
        print("ok   visible   %s (%s)" % (visible, how))

        info2 = get(base + "/object_info/UNETLoader", timeout=60)
        unets = info2["UNETLoader"]["input"]["required"]["unet_name"][0]
        if not any(os.path.basename(visible) in str(u) for u in unets):
            print("FAIL the merged checkpoint is not visible to UNETLoader")
            return 1
        print("ok   loader    sees the new checkpoint")

        sched_opts = info["KSampler"]["input"]["required"]["scheduler"][0]
        sched = next((s for s in sched_opts if "beta57" in str(s)), None)
        if not sched:
            # Say it out loud: a substituted scheduler is not the beta57 this
            # repository documents.
            sched = "normal"
            print("note scheduler  beta57 is not registered here; using %r" % sched)
        render = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": os.path.basename(visible), "weight_dtype": "default"}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {
                "ckpt_name": next(c for c in
                                  info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
                                  if "woomix" in str(c).lower())}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a red fox in snow, sharp focus", "clip": ["2", 1]}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry, low quality", "clip": ["2", 1]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 512, "height": 512, "batch_size": 1}},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": 4242, "steps": 8, "cfg": 4.0, "sampler_name": "euler",
                "scheduler": sched,
                "denoise": 1.0, "model": ["1", 0], "positive": ["3", 0],
                "negative": ["4", 0], "latent_image": ["5", 0]}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["2", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "comfy-kit-4way-%s-render" % run_id, "images": ["7", 0]}},
        }
        pid2 = post(base + "/prompt", {"prompt": render, "client_id": "comfy-kit"})["prompt_id"]
        entry2 = wait_for(base, pid2, args.timeout, "the reload render")
        check_error(entry2, "render")
        img = next((x for o in entry2.get("outputs", {}).values()
                    for x in o.get("images", [])), None)
        if not img:
            print("FAIL the reloaded merge produced no image")
            return 1
        # Registered before the download: an HTTP error must not leave the preview behind.
        preview = preview_path(img, output_root, run_id)
        if preview and preview not in created:
            created.append(preview)
        q = urllib.parse.urlencode({"filename": img["filename"],
                                    "subfolder": img.get("subfolder", ""),
                                    "type": img.get("type", "output")})
        try:
            with urllib.request.urlopen(base + "/view?" + q, timeout=120) as r:
                data = r.read()
        except Exception as e:
            print("FAIL the preview download failed (%s); it stays registered for cleanup: %s"
                  % (e.__class__.__name__, preview or "no preview path"))
            return 1
        print("ok   render    %s (%d bytes)" % (img["filename"], len(data)))
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("L")
        hist = im.histogram()
        total = sum(hist)
        mean = sum(i * c for i, c in enumerate(hist)) / total
        lo = next(i for i, c in enumerate(hist) if c)
        hi = 255 - next(i for i, c in enumerate(reversed(hist)) if c)
        print("ok   pixels    mean=%.1f range=%d-%d" % (mean, lo, hi))
        if hi - lo < 10 or mean < 5:
            print("FAIL the reloaded merge renders a flat frame")
            return 1
    finally:
        leftovers = cleanup(created, args.keep_artifacts)

    if leftovers:
        # A pass that leaves multi-gigabyte files on the operator's disk is not a pass.
        print("FAIL cleanup left %d artifact(s) behind: %s"
              % (len(leftovers), ", ".join(p for p, _ in leftovers)))
        return 1
    print("MERGE GRAPH: PASS (shipped workflow -> save -> reload -> render)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
