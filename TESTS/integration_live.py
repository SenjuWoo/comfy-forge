"""End-to-end ComfyUI integration: submit a real workflow over the API.

The unit tests prove the tools behave; this proves the *installation* works.
It talks to a live backend, submits an API-format prompt (not a UI-save graph,
which /prompt rejects), waits for the job, downloads the image and checks the
pixels are not degenerate.

Exit codes:
  0  PASS      - a real image came back from a real backend
  1  FAIL      - the backend answered and something is genuinely wrong
  3  UNVERIFIED- no backend reachable from here

Usage:
    python TESTS/integration_live.py [--url http://127.0.0.1:7821]
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 30


def get(url, timeout=TIMEOUT):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url, payload, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def pick(options, *needles):
    for needle in needles:
        for o in options:
            if needle.lower() in str(o).lower():
                return o
    return options[0] if options else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7821"))
    ap.add_argument("--timeout", type=int, default=420)
    # The test contract names what must be exercised. Silently picking "the first
    # checkpoint" would report PASS for a different model than the one claimed.
    ap.add_argument("--checkpoint", default="woomixAnima_v175")
    ap.add_argument("--scheduler", default="beta57")
    ap.add_argument("--allow-any", action="store_true",
                    help="permit a fallback model/scheduler (reported as such)")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    try:
        stats = get(base + "/system_stats")
    except Exception as e:
        print(f"UNVERIFIED: no ComfyUI at {base} ({e.__class__.__name__})")
        return 3
    print(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')}")

    info = get(base + "/object_info", timeout=60)
    print(f"ok   nodes       {len(info)} registered")

    required = ["CheckpointLoaderSimple", "CLIPTextEncode", "KSampler",
                "VAEDecode", "EmptyLatentImage", "SaveImage"]
    missing = [n for n in required if n not in info]
    if missing:
        print(f"FAIL core nodes missing: {missing}")
        return 1
    hades = [n for n in info if n.startswith("HaDeS")]
    if hades:
        print(f"ok   HaDeS       {len(hades)} nodes, e.g. {hades[0]}")
    else:
        print("FAIL HaDeS nodes are not registered - the pack did not load")
        return 1

    ckpts = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    ckpt = next((c for c in ckpts if args.checkpoint.lower() in str(c).lower()), None)
    if not ckpt:
        if not args.allow_any:
            print(f"FAIL required checkpoint '{args.checkpoint}' is not visible to "
                  f"CheckpointLoaderSimple (pass --allow-any to accept a substitute)")
            return 1
        ckpt = pick(ckpts, "woomix", "Anima")
        print(f"note checkpoint  substituting {ckpt} (not the required model)")
    if not ckpt:
        print("FAIL no checkpoint is visible to CheckpointLoaderSimple")
        return 1
    print(f"ok   checkpoint  {ckpt}")

    samplers = info["KSampler"]["input"]["required"]["sampler_name"][0]
    scheds = info["KSampler"]["input"]["required"]["scheduler"][0]
    sampler = pick(samplers, "euler")
    sched = next((x for x in scheds if args.scheduler.lower() in str(x).lower()), None)
    if not sched:
        if not args.allow_any:
            print(f"FAIL required scheduler '{args.scheduler}' is not registered "
                  f"(pass --allow-any to accept a substitute)")
            return 1
        sched = pick(scheds, "normal")
        print(f"note sampler     substituting scheduler {sched}")
    print(f"ok   sampler     {sampler} / {sched}")

    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "a red fox sitting in snow, sharp focus, detailed fur",
                         "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "blurry, low quality", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": 12345, "steps": 8, "cfg": 4.0,
                         "sampler_name": sampler, "scheduler": sched,
                         "denoise": 1.0, "model": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "comfy-kit-integration", "images": ["6", 0]}},
    }
    try:
        res = post(base + "/prompt", {"prompt": prompt, "client_id": uuid.uuid4().hex})
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        print(f"FAIL /prompt rejected the graph: {body[:400]!r}")
        return 1
    pid = res.get("prompt_id")
    if not pid:
        print(f"FAIL no prompt_id in {res}")
        return 1
    print(f"ok   submitted   prompt_id={pid}")

    deadline = time.time() + args.timeout
    entry = None
    while time.time() < deadline:
        hist = get(base + f"/history/{pid}")
        if pid in hist:
            entry = hist[pid]
            break
        time.sleep(2)
    if entry is None:
        print(f"FAIL no completion within {args.timeout}s")
        return 1

    status = entry.get("status", {})
    if status.get("status_str") == "error":
        print(f"FAIL execution error: {status.get('messages', '')[:400]}")
        return 1
    images = [i for o in entry.get("outputs", {}).values() for i in o.get("images", [])]
    if not images:
        print("FAIL the run produced no image")
        return 1
    img = images[0]
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    data = fetch(f"{base}/view?{q}")
    print(f"ok   image       {img['filename']} ({len(data)} bytes)")
    if len(data) < 5000:
        print("FAIL the returned image is suspiciously small")
        return 1

    try:
        from PIL import Image
    except ImportError:
        # An image arriving proves the graph ran, not that it rendered anything.
        print("UNVERIFIED: Pillow is missing, so the pixels cannot be checked")
        return 3
    import io
    im = Image.open(io.BytesIO(data)).convert("L")
    hist = im.histogram()
    total = sum(hist)
    mean = sum(i * c for i, c in enumerate(hist)) / total
    lo = next(i for i, c in enumerate(hist) if c)
    hi = 255 - next(i for i, c in enumerate(reversed(hist)) if c)
    print(f"ok   pixels      {im.width}x{im.height} mean={mean:.1f} range={lo}-{hi}")
    if hi - lo < 10:
        print("FAIL the image is degenerate (flat frame, likely black)")
        return 1
    print("INTEGRATION: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
