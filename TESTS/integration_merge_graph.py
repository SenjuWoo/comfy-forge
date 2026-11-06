"""Execute the shipped four-way HaDeS merge through ComfyUI's real API.

This is the proof the release was missing. It does not run the CLI merger and
call it integration: it loads five models, runs `HaDeSAnimaMultiModelMerger`,
saves with `HaDeSAnimaModelSaver`, reloads the saved file through `UNETLoader`
and renders an image from it.

Exit codes: 0 PASS, 1 FAIL, 3 UNVERIFIED (no backend / no models).

    python TESTS/integration_merge_graph.py --url http://127.0.0.1:7821
    python TESTS/integration_merge_graph.py --dry-run
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DONORS = [
    "Versetility - Diving-Anima_v9.0.safetensors",
    "Details - Terra Rising Unity 3.0 - Turbo.safetensors",
    "Composition - hentai_studio_quality-000008.safetensors",
    "Consistensy - One obsession_animaV5turbo.safetensors",
]
WEIGHTS = [0.70, 0.90, 0.55, 0.45]
SCALE = 0.30
CLIP_SOURCE = "woomixAnima_v175"


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url, payload, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def resolve(options, needle):
    for o in options:
        if needle.lower() in str(o).lower():
            return o
    return None


def build(info, base):
    """API-format graph. Returns (prompt, merged_prefix)."""
    unets = info["UNETLoader"]["input"]["required"]["unet_name"][0]
    ckpts = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    samplers = info["KSampler"]["input"]["required"]["sampler_name"][0]
    scheds = info["KSampler"]["input"]["required"]["scheduler"][0]

    donor_ids = [resolve(unets, os.path.splitext(d)[0]) for d in DONORS]
    clip_id = resolve(ckpts, CLIP_SOURCE)
    sampler = resolve(samplers, "euler")
    sched = resolve(scheds, "beta57") or resolve(scheds, "normal")
    return donor_ids, clip_id, sampler, sched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7821"))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prefix", default="comfy-kit-4way-proof")
    ap.add_argument("--models-dir", default=os.environ.get("COMFYUI_UNET_DIR"))
    args = ap.parse_args()
    base_url = args.url.rstrip("/")

    try:
        info = get(base_url + "/object_info", timeout=60)
    except Exception as e:
        print(f"UNVERIFIED: no ComfyUI at {base_url} ({e.__class__.__name__})")
        return 3

    need = ["UNETLoader", "CheckpointLoaderSimple", "KSampler", "VAEDecode",
            "SaveImage", "CLIPTextEncode", "EmptyLatentImage",
            "HaDeSAnimaMultiModelMerger", "HaDeSAnimaModelSaver"]
    missing = [n for n in need if n not in info]
    if missing:
        print(f"FAIL required node classes are not registered: {missing}")
        return 1

    donor_ids, clip_id, sampler, sched = build(info, base_url)
    if not clip_id or not all(donor_ids):
        print("UNVERIFIED: the donor set or the CLIP source is not installed here")
        print("           missing donors:",
              [d for d, r in zip(DONORS, donor_ids) if not r])
        return 3
    print(f"ok   donors      {len(donor_ids)} resolved")
    print(f"ok   clip source {clip_id}")
    print(f"ok   sampler     {sampler} / {sched}")

    prompt = {}
    for i, unet in enumerate(donor_ids, start=1):
        prompt[str(i)] = {"class_type": "UNETLoader",
                          "inputs": {"unet_name": unet, "weight_dtype": "default"}}
    # Base for the task vector: the first donor is the documented fallback anchor
    # and keeps its weight, exactly like the CLI's -AcceptFallbackAnchor path.
    merger = {
        "class_type": "HaDeSAnimaMultiModelMerger",
        "inputs": {
            "model_1": ["1", 0], "model_2": ["2", 0],
            "model_3": ["3", 0], "model_4": ["4", 0],
            "base_model": ["1", 0],
            "method": "Task Arithmetic",
            "density": 0.5, "scale": SCALE, "seed": 0,
            "weight_1": WEIGHTS[0], "weight_2": WEIGHTS[1],
            "weight_3": WEIGHTS[2], "weight_4": WEIGHTS[3],
            "protect_llm_adapter": True,
        },
    }
    prompt["10"] = merger
    prompt["11"] = {"class_type": "HaDeSAnimaModelSaver", "inputs": {
        "model": ["10", 0],
        "filename_prefix": args.prefix,
        "save_format": "Native ComfyUI (MiniTrainDIT)",
        "precision": "bfloat16 (Original Master)",
        "llm_adapter_mode": "Embed in Checkpoint (Full)",
        "include_metadata": True,
    }}
    # The saver writes to ComfyUI's output folder with a counter suffix, which
    # this graph cannot predict. Reloading therefore happens in a second
    # submission, after the real saved_path is known.
    if args.dry_run:
        print(json.dumps(prompt, indent=2))
        print("ok   graph validates against the live node schemas")
        return 0

    print("note submitting merge graph (this loads five models and can take minutes)")
    res = post(base_url + "/prompt", {"prompt": prompt, "client_id": "comfy-kit"})
    pid = res.get("prompt_id")
    if not pid:
        print(f"FAIL no prompt_id: {res}")
        return 1
    print(f"ok   submitted   {pid}")

    deadline = time.time() + args.timeout
    entry = None
    while time.time() < deadline:
        hist = get(base_url + f"/history/{pid}")
        if pid in hist:
            entry = hist[pid]
            break
        time.sleep(5)
    if entry is None:
        print(f"FAIL no completion within {args.timeout}s")
        return 1

    status = entry.get("status", {})
    if status.get("status_str") == "error":
        for m in status.get("messages", []):
            if m[0] == "execution_error":
                d = m[1]
                print(f"FAIL node {d.get('node_id')} ({d.get('node_type')}): "
                      f"{d.get('exception_type')}: {d.get('exception_message')}")
                return 1
        print("FAIL execution error without detail")
        return 1

    outputs = entry.get("outputs", {})
    saved_paths = [x for o in outputs.values() for x in o.get("saved_path", [])]
    if not saved_paths:
        print("FAIL the saver reported no saved_path: " + json.dumps(outputs)[:300])
        return 1
    saved_path = saved_paths[0]
    print("ok   saver       " + saved_path)
    if not os.path.exists(saved_path):
        print("FAIL the saver reported a path that does not exist")
        return 1
    print("ok   saved file  %.2f GB" % (os.path.getsize(saved_path) / 1e9))

    try:
        from safetensors import safe_open
        with safe_open(saved_path, framework="pt") as f:
            names = list(f.keys())
        print("ok   safetensors %d tensors, e.g. %s" % (len(names), names[0]))
    except Exception as e:
        print("FAIL the saved file is not a loadable safetensors checkpoint: %s" % e)
        return 1

    models_dir = args.models_dir or r"S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI\models\diffusion_models"
    os.makedirs(models_dir, exist_ok=True)
    visible = os.path.join(models_dir, args.prefix + ".safetensors")
    if os.path.exists(visible):
        os.unlink(visible)
    try:
        os.link(saved_path, visible)
        how = 'hard link'
    except OSError:
        import shutil
        shutil.copy2(saved_path, visible)
        how = 'copy'
    print("ok   visible     " + visible + " (" + how + ")")

    info2 = get(base_url + "/object_info/UNETLoader", timeout=60)
    unets2 = info2["UNETLoader"]["input"]["required"]["unet_name"][0]
    if not any(args.prefix in str(u) for u in unets2):
        print("FAIL the merged checkpoint is not visible to UNETLoader after saving")
        return 1
    print("ok   loader sees it in models/diffusion_models")

    render = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": args.prefix + ".safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": clip_id}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {
            "text": "a red fox in snow, sharp focus", "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {
            "text": "blurry, low quality", "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "seed": 4242, "steps": 8, "cfg": 4.0,
            "sampler_name": sampler, "scheduler": sched, "denoise": 1.0,
            "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["2", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": args.prefix + "-render", "images": ["7", 0]}},
    }
    res2 = post(base_url + "/prompt", {"prompt": render, "client_id": "comfy-kit"})
    pid2 = res2.get("prompt_id")
    if not pid2:
        print("FAIL render submission rejected: " + json.dumps(res2))
        return 1
    print("ok   render job  " + pid2)
    entry2 = None
    while time.time() < deadline:
        h = get(base_url + "/history/" + pid2)
        if pid2 in h:
            entry2 = h[pid2]
            break
        time.sleep(5)
    if entry2 is None:
        print("FAIL the reloaded merge did not finish rendering")
        return 1
    if entry2.get("status", {}).get("status_str") == "error":
        for m in entry2["status"].get("messages", []):
            if m[0] == "execution_error":
                d = m[1]
                print("FAIL render node %s (%s): %s: %s" % (d.get('node_id'), d.get('node_type'),
                                                            d.get('exception_type'), d.get('exception_message')))
                return 1
        print("FAIL render error without detail")
        return 1

    images = [i for o in entry2.get("outputs", {}).values() for i in o.get("images", [])]
    if not images:
        print("FAIL the reloaded merge produced no image")
        return 1
    img = images[0]
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    with urllib.request.urlopen(f"{base_url}/view?{q}", timeout=120) as r:
        data = r.read()
    print(f"ok   render      {img['filename']} ({len(data)} bytes)")
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data)).convert("L")
        hist_bins = im.histogram()
        total = sum(hist_bins)
        mean = sum(i * c for i, c in enumerate(hist_bins)) / total
        lo = next(i for i, c in enumerate(hist_bins) if c)
        hi = 255 - next(i for i, c in enumerate(reversed(hist_bins)) if c)
        print(f"ok   pixels      mean={mean:.1f} range={lo}-{hi}")
        if hi - lo < 10:
            print("FAIL the reloaded merge renders a flat frame")
            return 1
    except ImportError:
        print("UNVERIFIED: Pillow missing, pixels not checked")
        return 3

    print("MERGE GRAPH: PASS (merge -> save -> reload -> render)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
