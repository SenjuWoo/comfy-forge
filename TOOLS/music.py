"""ACE-Step 1.5 music generation through a running ComfyUI backend.

    python TOOLS/music.py variants
    python TOOLS/music.py check    [--url URL] [--variant turbo]
    python TOOLS/music.py build    --tags "..." [--lyrics-file f.txt] --out graph.json
    python TOOLS/music.py generate --tags "..." --lyrics-file f.txt --duration 120

The graph is the one the Comfy-Org template under presets/music/ describes, built
directly in API format so no UI file has to be converted. TESTS/test_music.py
proves, for every variant, that this graph has the same nodes, the same wiring and
the same sampler values as the shipped template.

Nothing here downloads a model (TOOLS/Install-MusicModels.ps1 does, on request) and
nothing trains. Exit codes: 0 done, 1 failed, 2 bad arguments, 3 no backend reachable.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# One row per shipped template. Every value is read from that template, and
# TESTS/test_music.py fails if a row drifts from its file.
#   loader  "checkpoint": one all-in-one file (model, text encoder and VAE inside)
#           "split":      separate diffusion model, two text encoders and a VAE
VARIANTS = {
    "aio": {
        "template": "audio_ace_step_1_5_checkpoint.json",
        "title": "Turbo, all-in-one checkpoint",
        "loader": "checkpoint",
        "checkpoint": "ace_step_1.5_turbo_aio.safetensors",
        "steps": 8, "cfg": 1.0, "shift": 3, "default_format": "flac",
    },
    "turbo": {
        "template": "audio_ace_step_1_5_split.json",
        "title": "Turbo, split files (0.6B + 1.7B text encoders)",
        "loader": "split",
        "unet": "acestep_v1.5_turbo.safetensors",
        "clip": ["qwen_0.6b_ace15.safetensors", "qwen_1.7b_ace15.safetensors"],
        "vae": "ace_1.5_vae.safetensors",
        "steps": 8, "cfg": 1.0, "shift": 3, "default_format": "mp3",
    },
    "turbo-4b": {
        "template": "audio_ace_step_1_5_split_4b.json",
        "title": "Turbo, split files (0.6B + 4B text encoders)",
        "loader": "split",
        "unet": "acestep_v1.5_turbo.safetensors",
        "clip": ["qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"],
        "vae": "ace_1.5_vae.safetensors",
        "steps": 8, "cfg": 1.0, "shift": 3, "default_format": "mp3",
    },
    "xl-turbo": {
        "template": "audio_ace_step1_5_xl_turbo.json",
        "title": "XL turbo (4B text encoder)",
        "loader": "split",
        "unet": "acestep_v1.5_xl_turbo_bf16.safetensors",
        "clip": ["qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"],
        "vae": "ace_1.5_vae.safetensors",
        "steps": 8, "cfg": 1.0, "shift": 3, "default_format": "mp3",
    },
    "xl-sft": {
        "template": "audio_ace_step1_5_xl_sft.json",
        "title": "XL SFT, best quality (50 steps)",
        "loader": "split",
        "unet": "acestep_v1.5_xl_sft_bf16.safetensors",
        "clip": ["qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"],
        "vae": "ace_1.5_vae.safetensors",
        "steps": 50, "cfg": 7.0, "shift": 3, "default_format": "flac", "top_p": 1.0,
    },
    "xl-base": {
        "template": "audio_ace_step1_5_xl_base.json",
        "title": "XL base (50 steps)",
        "loader": "split",
        "unet": "acestep_v1.5_xl_base_bf16.safetensors",
        "clip": ["qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"],
        "vae": "ace_1.5_vae.safetensors",
        "steps": 50, "cfg": 6.0, "shift": 3, "default_format": "mp3",
    },
}
DEFAULT_VARIANT = "aio"

# Prompt-encoder values the templates ship with; --lm-* flags override them.
ENCODER_DEFAULTS = {"cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}
TIME_SIGNATURES = ("2", "3", "4", "6")
FORMATS = ("flac", "mp3", "opus")
INSTRUMENTAL_LYRICS = "[Instrumental]"
KEY_ROOTS = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]
DEFAULT_URL = "http://127.0.0.1:8188"


class UsageError(Exception):
    pass


def check_key(key):
    root, _, quality = key.partition(" ")
    if root not in KEY_ROOTS or quality not in ("major", "minor"):
        raise UsageError(f"--key must look like 'E minor' or 'Bb major' (root {', '.join(KEY_ROOTS)}), got {key!r}")
    return key


def build_graph(variant, tags, lyrics, *, duration=120.0, bpm=120, key="C major", time_signature="4",
                language="en", seed=0, steps=None, cfg=None, shift=None, audio_format=None,
                quality=None, prefix="audio/comfy-forge", lm=None, generate_audio_codes=True):
    """Return an API-format prompt (node id -> {class_type, inputs})."""
    if variant not in VARIANTS:
        raise UsageError(f"unknown variant {variant!r}; choose one of {', '.join(VARIANTS)}")
    spec = VARIANTS[variant]
    if not tags.strip():
        raise UsageError("--tags (the style description) is required")
    if not 1.0 <= duration <= 600.0:
        raise UsageError("--duration must be between 1 and 600 seconds")
    if not 10 <= bpm <= 300:
        raise UsageError("--bpm must be between 10 and 300")
    if str(time_signature) not in TIME_SIGNATURES:
        raise UsageError(f"--time-signature must be one of {', '.join(TIME_SIGNATURES)}")
    if seed < 0:
        raise UsageError("--seed must be 0 or greater")
    fmt = audio_format or spec["default_format"]
    if fmt not in FORMATS:
        raise UsageError(f"--format must be one of {', '.join(FORMATS)}")
    check_key(key)
    encoder = dict(ENCODER_DEFAULTS)
    if "top_p" in spec:
        encoder["top_p"] = spec["top_p"]
    encoder.update(lm or {})

    g = {}
    if spec["loader"] == "checkpoint":
        g["1"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": spec["checkpoint"]}}
        model, clip, vae = ["1", 0], ["1", 1], ["1", 2]
    else:
        g["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": spec["unet"], "weight_dtype": "default"}}
        g["2"] = {"class_type": "DualCLIPLoader",
                  "inputs": {"clip_name1": spec["clip"][0], "clip_name2": spec["clip"][1],
                             "type": "ace", "device": "default"}}
        g["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": spec["vae"]}}
        model, clip, vae = ["1", 0], ["2", 0], ["3", 0]
    g["10"] = {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": model, "shift": float(shift if shift is not None else spec["shift"])}}
    g["11"] = {"class_type": "TextEncodeAceStepAudio1.5",
               "inputs": {"clip": clip, "tags": tags, "lyrics": lyrics, "seed": seed, "bpm": bpm,
                          "duration": float(duration), "timesignature": str(time_signature),
                          "language": language, "keyscale": key,
                          "generate_audio_codes": bool(generate_audio_codes), **encoder}}
    g["12"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["11", 0]}}
    g["13"] = {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": float(duration), "batch_size": 1}}
    g["14"] = {"class_type": "KSampler",
               "inputs": {"model": ["10", 0], "positive": ["11", 0], "negative": ["12", 0],
                          "latent_image": ["13", 0], "seed": seed,
                          "steps": int(steps if steps is not None else spec["steps"]),
                          "cfg": float(cfg if cfg is not None else spec["cfg"]),
                          "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
    g["15"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": vae}}
    save = {"audio": ["15", 0], "filename_prefix": prefix, "format": fmt}
    if fmt == "mp3":
        save["format.quality"] = quality or "V0"
    elif fmt == "opus":
        save["format.quality"] = quality or "128k"
    g["16"] = {"class_type": "SaveAudioAdvanced", "inputs": save}
    return g


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
def http_json(url, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_bytes(url, timeout=300):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def backend_up(base):
    try:
        return http_json(base + "/system_stats", timeout=5)
    except Exception:
        return None


def required_files(spec):
    """(object_info node, input name, file) triples that must be visible to the backend."""
    if spec["loader"] == "checkpoint":
        return [("CheckpointLoaderSimple", "ckpt_name", spec["checkpoint"])]
    return [("UNETLoader", "unet_name", spec["unet"]),
            ("DualCLIPLoader", "clip_name1", spec["clip"][0]),
            ("DualCLIPLoader", "clip_name2", spec["clip"][1]),
            ("VAELoader", "vae_name", spec["vae"])]


def visible_options(info, node, field):
    entry = info.get(node, {}).get("input", {})
    for section in ("required", "optional"):
        if field in entry.get(section, {}):
            options = entry[section][field][0]
            return options if isinstance(options, list) else []
    return []


def preflight(base, graph, spec):
    """Return a list of problems that would make the backend reject this graph."""
    info = http_json(base + "/object_info", timeout=60)
    problems = []
    for node in sorted({n["class_type"] for n in graph.values()}):
        if node not in info:
            problems.append(f"node {node} is not registered; update ComfyUI (ACE-Step 1.5 nodes arrived in 0.11)")
    for node, field, name in required_files(spec):
        if node in info and name not in visible_options(info, node, field):
            problems.append(f"{name} is not visible to {node}; run TOOLS\\Install-MusicModels.ps1 -Set {spec_key(spec)}")
    return problems


def spec_key(spec):
    for key, value in VARIANTS.items():
        if value is spec:
            return key
    return DEFAULT_VARIANT


def wait_for(base, prompt_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = http_json(f"{base}/history/{prompt_id}", timeout=30)
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(1.0)
    raise TimeoutError(f"no completion within {timeout}s")


def collect_audio(entry):
    files = []
    for output in entry.get("outputs", {}).values():
        for item in output.get("audio", []):
            files.append(item)
    return files


def audio_summary(path):
    """Cheap sanity facts about the written file; None when nothing can be read."""
    size = os.path.getsize(path)
    info = {"bytes": size}
    ext = os.path.splitext(path)[1].lower()
    if ext == ".flac":
        with open(path, "rb") as fh:
            head = fh.read(42)
        if head[:4] == b"fLaC" and len(head) >= 22:
            # STREAMINFO: 20 bits sample rate, 3 bits channels-1, 5 bits depth-1, 36 bits total samples
            bits = int.from_bytes(head[18:26], "big")
            rate = bits >> 44
            total = bits & ((1 << 36) - 1)
            info["sample_rate"] = rate
            info["seconds"] = round(total / rate, 2) if rate and total else None
    return info


def run_generate(args, out_stream=sys.stdout):
    spec = VARIANTS[args.variant]
    lyrics = read_lyrics(args)
    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)
    lm = {k: v for k, v in (("cfg_scale", args.lm_cfg), ("temperature", args.lm_temperature),
                            ("top_p", args.lm_top_p), ("top_k", args.lm_top_k)) if v is not None}
    graph = build_graph(args.variant, args.tags, lyrics, duration=args.duration, bpm=args.bpm,
                        key=args.key, time_signature=args.time_signature, language=args.language,
                        seed=seed, steps=args.steps, cfg=args.cfg, shift=args.shift,
                        audio_format=args.format, prefix=args.prefix, lm=lm,
                        generate_audio_codes=not args.no_audio_codes)
    base = args.url.rstrip("/")
    if backend_up(base) is None:
        print(f"UNVERIFIED: no ComfyUI answered at {base}. Start it, or pass --url.", file=out_stream)
        return 3
    problems = preflight(base, graph, spec)
    if problems:
        for p in problems:
            print(f"FAIL {p}", file=out_stream)
        return 1
    try:
        res = http_json(base + "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})
    except urllib.error.HTTPError as e:
        print(f"FAIL /prompt rejected the graph: {e.read()[:600]!r}", file=out_stream)
        return 1
    pid = res.get("prompt_id")
    if not pid:
        print(f"FAIL no prompt_id in {res}", file=out_stream)
        return 1
    print(f"ok   submitted   {pid} (variant {args.variant}, seed {seed})", file=out_stream)
    try:
        entry = wait_for(base, pid, args.timeout)
    except TimeoutError as e:
        print(f"FAIL {e}", file=out_stream)
        return 1
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        print(f"FAIL execution error: {json.dumps(status.get('messages', ''))[:600]}", file=out_stream)
        return 1
    files = collect_audio(entry)
    if not files:
        print("FAIL the run produced no audio file", file=out_stream)
        return 1
    os.makedirs(args.out, exist_ok=True)
    written = []
    for item in files:
        query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                        "type": item.get("type", "output")})
        data = http_bytes(f"{base}/view?{query}")
        dest = os.path.join(args.out, os.path.basename(item["filename"]))
        with open(dest, "wb") as fh:
            fh.write(data)
        info = audio_summary(dest)
        print(f"ok   audio       {dest} {info}", file=out_stream)
        if info["bytes"] < 20000:
            print("FAIL the audio file is suspiciously small", file=out_stream)
            return 1
        written.append(dest)
    print(json.dumps({"seed": seed, "variant": args.variant, "files": written}), file=out_stream)
    return 0


def read_lyrics(args):
    if args.instrumental:
        return INSTRUMENTAL_LYRICS
    if args.lyrics_file:
        with open(args.lyrics_file, encoding="utf-8") as fh:
            return fh.read()
    return args.lyrics or ""


def variant_files(spec):
    """[(models subfolder, file name)] the variant loads."""
    if spec["loader"] == "checkpoint":
        return [("checkpoints", spec["checkpoint"])]
    return [("diffusion_models", spec["unet"]), ("text_encoders", spec["clip"][0]),
            ("text_encoders", spec["clip"][1]), ("vae", spec["vae"])]


def cmd_variants(args):
    if args.json:
        print(json.dumps({key: {"title": spec["title"], "files": [list(f) for f in variant_files(spec)]}
                          for key, spec in VARIANTS.items()}, indent=2))
        return 0
    for key, spec in VARIANTS.items():
        files = [spec["checkpoint"]] if spec["loader"] == "checkpoint" else [spec["unet"], *spec["clip"], spec["vae"]]
        print(f"{key:9} {spec['title']}  steps={spec['steps']} cfg={spec['cfg']}")
        print(f"          {', '.join(files)}")
    print(f"default: {DEFAULT_VARIANT}")
    return 0


def cmd_check(args):
    base = args.url.rstrip("/")
    stats = backend_up(base)
    if stats is None:
        print(f"UNVERIFIED: no ComfyUI answered at {base}")
        return 3
    print(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')}")
    spec = VARIANTS[args.variant]
    graph = build_graph(args.variant, "check", "", duration=10)
    problems = preflight(base, graph, spec)
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"ok   variant     {args.variant}: nodes registered and model files visible")
    return 1 if problems else 0


def cmd_build(args):
    graph = build_graph(args.variant, args.tags, read_lyrics(args), duration=args.duration, bpm=args.bpm,
                        key=args.key, time_signature=args.time_signature, language=args.language,
                        seed=args.seed or 0, steps=args.steps, cfg=args.cfg, shift=args.shift,
                        audio_format=args.format, prefix=args.prefix)
    text = json.dumps(graph, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def make_parser():
    p = argparse.ArgumentParser(description="ACE-Step 1.5 music generation through ComfyUI")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, with_prompt):
        sp.add_argument("--variant", default=DEFAULT_VARIANT, choices=list(VARIANTS))
        sp.add_argument("--url", default=os.environ.get("COMFYUI_URL", DEFAULT_URL))
        if not with_prompt:
            return
        sp.add_argument("--tags", required=True, help="style caption: genre, mood, instruments, production")
        grp = sp.add_mutually_exclusive_group()
        grp.add_argument("--lyrics", help="lyrics text, with [Verse] / [Chorus] section tags")
        grp.add_argument("--lyrics-file", help="UTF-8 file holding the lyrics")
        grp.add_argument("--instrumental", action="store_true", help=f"send {INSTRUMENTAL_LYRICS} instead of lyrics")
        sp.add_argument("--duration", type=float, default=120.0, help="seconds, 1 to 600 (default 120)")
        sp.add_argument("--bpm", type=int, default=120)
        sp.add_argument("--key", default="C major", help="e.g. 'E minor', 'Bb major'")
        sp.add_argument("--time-signature", default="4", choices=TIME_SIGNATURES)
        sp.add_argument("--language", default="en", help="lyrics language code (en, ja, zh, ko, es, ...)")
        sp.add_argument("--seed", type=int, default=None, help="default: random, and printed")
        sp.add_argument("--steps", type=int, default=None)
        sp.add_argument("--cfg", type=float, default=None)
        sp.add_argument("--shift", type=float, default=None)
        sp.add_argument("--format", choices=FORMATS, default=None)
        sp.add_argument("--prefix", default="audio/comfy-forge", help="ComfyUI output filename prefix")

    v = sub.add_parser("variants", help="list the model variants")
    common(v, False)
    v.add_argument("--json", action="store_true", help="machine-readable list with model subfolders")
    common(sub.add_parser("check", help="is the backend ready for this variant"), False)
    b = sub.add_parser("build", help="write the API graph without contacting a backend")
    common(b, True)
    b.add_argument("--out", default="", help="write the graph here instead of stdout")
    g = sub.add_parser("generate", help="render a song and save it")
    common(g, True)
    g.add_argument("--out", default="out/music", help="folder for the audio file")
    g.add_argument("--timeout", type=int, default=900)
    g.add_argument("--no-audio-codes", action="store_true",
                   help="skip the language-model planning pass (faster, lower quality)")
    g.add_argument("--lm-cfg", type=float, default=None)
    g.add_argument("--lm-temperature", type=float, default=None)
    g.add_argument("--lm-top-p", type=float, default=None)
    g.add_argument("--lm-top-k", type=int, default=None)
    return p


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        if args.command == "variants":
            return cmd_variants(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "build":
            return cmd_build(args)
        return run_generate(args)
    except UsageError as e:
        print(f"error  {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
