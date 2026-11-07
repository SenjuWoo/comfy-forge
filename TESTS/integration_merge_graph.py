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


def claim_saved(saved, run_id, root=None):
    """Pick the one saved_path that belongs to this run, or refuse.

    The backend reports whatever it wrote. Deleting that on trust means a mocked or
    buggy backend can hand us a user's model. Ownership is the unique filename
    prefix this run injected into every saver node, plus an optional containment
    check; anything ambiguous is refused rather than guessed.
    """
    stem = RUN_STEM % run_id
    matches = []
    for path in saved:
        if not _name_ok(path, stem) or not os.path.isfile(path):
            continue
        if root:
            try:
                inside = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
            except ValueError:
                inside = ""
            if os.path.abspath(inside) != os.path.abspath(root):
                continue
        matches.append(path)
    if not matches:
        return None, ("no saved_path starts with %r; refusing to delete anything (reported: %s)"
                      % (stem, ", ".join(saved) or "none"))
    if len(matches) > 1:
        return None, "ambiguous: %d saved paths match this run (%s)" % (len(matches), ", ".join(matches))
    return matches[0], None


def preview_path(img, saved_path, run_id):
    """The SaveImage preview this run is about to create, before it is downloaded.

    Registered for cleanup ahead of the HTTP call: a failed download must not leave
    the preview on disk. Returns None when the image is not demonstrably ours.
    """
    if not img or img.get("subfolder"):
        return None
    candidate = os.path.join(os.path.dirname(saved_path), str(img.get("filename", "")))
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


def expose(saved_path, models_dir, name, copy=shutil.copy2, same_volume=None):
    """Make the saved merge visible to UNETLoader under a name nobody owns.

    Refuses an existing file outright. A hard link shares the bytes on the same
    volume; on another volume the copy is a full second checkpoint, so the free
    space is checked first and a copy that dies midway is removed - it is a
    truncated file this run created, never a pre-existing model.

    same_volume is only overridden by tests; production derives it from the
    filesystem so the decision cannot drift from reality.
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
    try:
        if copy is shutil.copy2:
            _copy_exclusive(saved_path, visible)
        else:
            # Tests inject a writer to simulate a copy that dies midway; production never gets here.
            copy(saved_path, visible)
    except BaseException:
        if os.path.exists(visible):
            try:
                os.unlink(visible)
            except OSError:
                pass
        raise
    return visible, "copy"


def _copy_exclusive(src, dst, chunk=4 * 1024 * 1024):
    """Copy into a destination that must not exist yet.

    O_CREAT|O_EXCL is the actual guard: the earlier os.path.exists() check only
    produces a nicer message, and between the two another process can create the
    file. Exclusive creation makes the second writer lose instead of overwriting.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(dst, flags, 0o666)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out, chunk)
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
    saved_path, why = claim_saved(saved, run_id)
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
        preview = preview_path(img, saved_path, run_id)
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
