"""ACE-Step 1.5 music generation through a running ComfyUI backend.

    python TOOLS/music.py variants
    python TOOLS/music.py check    [--variant aio|all] [--url URL]
    python TOOLS/music.py generate --tags "..." --lyrics-file song.txt --duration 150 [--count 4]
    python TOOLS/music.py generate --from out/music/take.flac.json --reseed --count 3
    python TOOLS/music.py build    --tags "..." --out graph.json

The graph is the one the Comfy-Org template under presets/music/ describes, built
directly in API format so no UI file has to be converted. TESTS/test_music.py
proves, for every variant, that this graph has the same nodes, the same wiring and
the same sampler values as the shipped template.

The backend is found on its own: --url, else COMFYUI_URL, else the first of port
8188 (standalone ComfyUI) and 7820-7832 (SwarmUI's ComfyUI backend) that answers.
Every take is saved with a .json sidecar holding its full settings and seed.

Nothing here downloads a model (TOOLS/Install-MusicModels.ps1 does, on request) and
nothing trains. Exit codes: 0 done, 1 failed, 2 bad arguments, 3 no backend reachable.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
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
# The language codes TextEncodeAceStepAudio1.5 accepts (comfy_extras/nodes_ace.py).
LANGUAGES = ['ar', 'az', 'bg', 'bn', 'ca', 'cs', 'da', 'de', 'el', 'en', 'es', 'fa', 'fi', 'fr', 'he', 'hi', 'hr',
             'ht', 'hu', 'id', 'is', 'it', 'ja', 'ko', 'la', 'lt', 'ms', 'ne', 'nl', 'no', 'pa', 'pl', 'pt', 'ro',
             'ru', 'sa', 'sk', 'sr', 'sv', 'sw', 'ta', 'te', 'th', 'tl', 'tr', 'uk', 'ur', 'vi', 'yue', 'zh', 'unknown']
# ACE-Step's inference guide documents 4096 characters as the lyrics maximum.
MAX_LYRICS = 4096
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
# A standalone ComfyUI listens on 8188; SwarmUI starts its ComfyUI backend at 7820
# and takes the next free port. 7801 is SwarmUI's own web server, not ComfyUI.
PROBE_PORTS = [8188] + list(range(7820, 7833))


class BackendError(Exception):
    pass


def http_json(url, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_bytes(url, timeout=300):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def backend_up(base, timeout=5):
    try:
        stats = http_json(base + "/system_stats", timeout=timeout)
        return stats if isinstance(stats, dict) and "system" in stats else None
    except Exception:
        return None


def probe_ports():
    raw = os.environ.get("COMFY_FORGE_PROBE_PORTS")  # tests narrow the search
    if raw:
        return [int(p) for p in raw.split(",") if p.strip()]
    return PROBE_PORTS


def resolve_backend(url):
    """(base url, system_stats) for --url, COMFYUI_URL, or the first port that answers."""
    if url:
        base = url.rstrip("/")
        return base, backend_up(base)
    for port in probe_ports():
        base = f"http://127.0.0.1:{port}"
        stats = backend_up(base, timeout=2)
        if stats:
            return base, stats
    return None, None


def no_backend_message(url):
    if url:
        return f"UNVERIFIED: no ComfyUI answered at {url.rstrip('/')}. Start it, or fix --url / COMFYUI_URL."
    ports = probe_ports()
    return (f"UNVERIFIED: no ComfyUI answered on port {ports[0]} or {ports[1]}-{ports[-1]}. "
            "Start ComfyUI, or SwarmUI (its backend is ComfyUI), or pass --url.") if len(ports) > 1 else \
        f"UNVERIFIED: no ComfyUI answered on port {ports[0]}. Start ComfyUI, or pass --url."


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


def preflight(base, graph, variant):
    """Return a list of problems that would make the backend reject this graph."""
    info = http_json(base + "/object_info", timeout=60)
    problems = []
    for node in sorted({n["class_type"] for n in graph.values()}):
        if node not in info:
            problems.append(f"node {node} is not registered; update ComfyUI (ACE-Step 1.5 nodes arrived in 0.11)")
    for node, field, name in required_files(VARIANTS[variant]):
        if node in info and name not in visible_options(info, node, field):
            problems.append(f"{name} is not visible to {node}; run TOOLS\\Install-MusicModels.ps1 -Set {variant}")
    return problems


def queue_position(base, prompt_id):
    """0 while running, n while n jobs are ahead, None when unknown."""
    try:
        q = http_json(base + "/queue", timeout=10)
    except Exception:
        return None
    if any(item[1] == prompt_id for item in q.get("queue_running", []) if len(item) > 1):
        return 0
    pending = sorted((item for item in q.get("queue_pending", []) if len(item) > 1), key=lambda item: item[0])
    for i, item in enumerate(pending):
        if item[1] == prompt_id:
            return i + 1 + len(q.get("queue_running", []))
    return None


def wait_for(base, prompt_id, timeout, log):
    start = time.time()
    last_note = start
    while time.time() - start < timeout:
        try:
            history = http_json(f"{base}/history/{prompt_id}", timeout=30)
        except (urllib.error.URLError, OSError) as e:
            raise BackendError(f"lost the backend while waiting: {e}")
        if prompt_id in history:
            return history[prompt_id], time.time() - start
        if time.time() - last_note >= 15:
            pos = queue_position(base, prompt_id)
            where = "rendering" if pos == 0 else (f"{pos} job(s) ahead" if pos else "queued")
            log(f"..   waiting     {time.time() - start:.0f}s ({where})")
            last_note = time.time()
        time.sleep(0.5)
    raise BackendError(f"no completion within {timeout}s (raise --timeout for long XL renders)")


def collect_audio(entry):
    files = []
    for output in entry.get("outputs", {}).values():
        for item in output.get("audio", []):
            files.append(item)
    return files


def audio_summary(path):
    """Cheap sanity facts about the written file: bytes, and length when it can be read."""
    info = {"bytes": os.path.getsize(path)}
    if os.path.splitext(path)[1].lower() == ".flac":
        with open(path, "rb") as fh:
            head = fh.read(42)
        if head[:4] == b"fLaC" and len(head) >= 26:
            # STREAMINFO: 20 bits sample rate, 3 bits channels-1, 5 bits depth-1, 36 bits total samples
            bits = int.from_bytes(head[18:26], "big")
            rate = bits >> 44
            total = bits & ((1 << 36) - 1)
            info["sample_rate"] = rate
            info["seconds"] = round(total / rate, 2) if rate and total else None
            return info
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30)
            info["seconds"] = round(float(out.stdout.strip()), 2)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    return info


def music_seconds(path, total):
    """Where the music ends: the start of the silence that runs to the end of the file.

    ACE-Step sometimes finishes early and pads the rest with silence. Needs ffmpeg on
    PATH; returns None when it is missing or the file cannot be read.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not total:
        return None
    try:
        run = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-af",
                              "silencedetect=noise=-50dB:d=1", "-f", "null", "-"],
                             capture_output=True, text=True, errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", run.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", run.stderr)]
    if run.returncode != 0:
        return None
    # The last silence counts only if it reaches the end of the file.
    if starts and (len(ends) < len(starts) or ends[-1] >= total - 0.1):
        return round(starts[-1], 2)
    return round(total, 2)


def unique_path(folder, name):
    """A path in folder that does not exist yet; an earlier take is never overwritten."""
    stem, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}-{n}{ext}")
        n += 1
    return candidate


def slug(text):
    cleaned = re.sub(r"[^\w\-]+", "-", text.strip(), flags=re.UNICODE).strip("-_")
    return cleaned[:80] or "song"


def tool_version():
    try:
        with open(os.path.join(REPO, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


# ---------------------------------------------------------------------------
# Settings: CLI flags, optionally on top of a sidecar from an earlier take
# ---------------------------------------------------------------------------
# Sidecar key -> argparse dest. A sidecar is the .json written next to every take.
REPLAY_KEYS = {
    "variant": "variant", "tags": "tags", "lyrics": "lyrics", "duration": "duration", "bpm": "bpm",
    "key": "key", "time_signature": "time_signature", "language": "language", "seed": "seed",
}
# Replayed only when the earlier take set them by hand ("overrides"), so a take made
# with one variant does not carry its step count into another variant.
OVERRIDE_KEYS = ("steps", "cfg", "shift", "format")


def load_replay(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise UsageError(f"--from {path}: {e}")
    if not isinstance(data, dict) or "tags" not in data:
        raise UsageError(f"--from {path}: not a comfy-forge music sidecar (no 'tags')")
    return data


def read_text_arg(value, path, what):
    if path:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return fh.read()
        except OSError as e:
            raise UsageError(f"cannot read the {what} file: {e}")
    return value


def resolve_settings(args):
    """Merge --from, files and flags into one settings dict, and validate it."""
    replay = load_replay(args.from_file) if getattr(args, "from_file", None) else {}
    s = {dest: replay.get(key) for key, dest in REPLAY_KEYS.items() if key in replay}
    s.update({k: v for k, v in (replay.get("overrides") or {}).items() if k in OVERRIDE_KEYS and v is not None})
    if getattr(args, "reseed", False):
        s.pop("seed", None)  # same settings, new random seeds
    for dest in list(REPLAY_KEYS.values()) + list(OVERRIDE_KEYS):
        value = getattr(args, dest, None)
        if value is not None:
            s[dest] = value
    tags = read_text_arg(args.tags, args.tags_file, "tags")
    if tags is not None:
        s["tags"] = tags
    if args.instrumental:
        s["lyrics"] = INSTRUMENTAL_LYRICS
    else:
        lyrics = read_text_arg(args.lyrics, args.lyrics_file, "lyrics")
        if lyrics is not None:
            s["lyrics"] = lyrics
    s["name"] = getattr(args, "name", "") or replay.get("name") or ""
    s.setdefault("variant", DEFAULT_VARIANT)
    s.setdefault("lyrics", "")
    s.setdefault("duration", 120.0)
    s.setdefault("bpm", 120)
    s.setdefault("key", "C major")
    s.setdefault("time_signature", "4")
    s.setdefault("language", "en")
    if not (s.get("tags") or "").strip():
        raise UsageError("a style caption is required: --tags \"...\", --tags-file, or --from an earlier take")
    if s["variant"] not in VARIANTS:
        raise UsageError(f"unknown variant {s['variant']!r}; choose one of {', '.join(VARIANTS)}")
    if s["language"] not in LANGUAGES:
        raise UsageError(f"--language {s['language']!r} is not one the node accepts: {', '.join(LANGUAGES)}")
    if len(s["lyrics"]) > MAX_LYRICS:
        warn(f"lyrics are {len(s['lyrics'])} characters; ACE-Step documents {MAX_LYRICS} as the maximum")
    return s


def warn(message):
    print(f"warn {message}", file=sys.stderr)


def graph_for(s, seed, prefix, args):
    lm = {k: v for k, v in (("cfg_scale", getattr(args, "lm_cfg", None)),
                            ("temperature", getattr(args, "lm_temperature", None)),
                            ("top_p", getattr(args, "lm_top_p", None)),
                            ("top_k", getattr(args, "lm_top_k", None))) if v is not None}
    graph = build_graph(s["variant"], s["tags"], s["lyrics"], duration=float(s["duration"]), bpm=int(s["bpm"]),
                        key=s["key"], time_signature=str(s["time_signature"]), language=s["language"],
                        seed=seed, steps=s.get("steps"), cfg=s.get("cfg"), shift=s.get("shift"),
                        audio_format=s.get("format"), prefix=prefix, lm=lm,
                        generate_audio_codes=not getattr(args, "no_audio_codes", False))
    return graph, lm


def take_seeds(seed, count):
    if seed is not None:
        if seed < 0:
            raise UsageError("--seed must be 0 or greater")
        return [seed + i for i in range(count)]
    rng = random.SystemRandom()
    return [rng.randint(0, 2**32 - 1) for _ in range(count)]


def run_generate(args, out_stream=sys.stdout):
    def log(message):
        print(message, file=out_stream, flush=True)

    s = resolve_settings(args)
    if args.count < 1:
        raise UsageError("--count must be 1 or more")
    seeds = take_seeds(s.get("seed"), args.count)
    prefix = f"audio/{slug(s['name'])}" if s["name"] else args.prefix
    graphs = [graph_for(s, seed, prefix, args) for seed in seeds]  # validates before any network call

    base, stats = resolve_backend(args.url)
    if not stats:
        log(no_backend_message(args.url))
        return 3
    log(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')} at {base}")
    try:
        problems = preflight(base, graphs[0][0], s["variant"])
        if problems:
            for p in problems:
                log(f"FAIL {p}")
            return 1
        submitted = []
        for seed, (graph, _lm) in zip(seeds, graphs):
            try:
                res = http_json(base + "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})
            except urllib.error.HTTPError as e:
                log(f"FAIL /prompt rejected the graph: {e.read()[:600]!r}")
                return 1
            pid = res.get("prompt_id")
            if not pid:
                log(f"FAIL no prompt_id in {res}")
                return 1
            submitted.append((seed, pid))
            log(f"ok   queued      {pid} (variant {s['variant']}, seed {seed})")

        os.makedirs(args.out, exist_ok=True)
        takes, written = [], []
        for seed, pid in submitted:
            entry, elapsed = wait_for(base, pid, args.timeout, log)
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                log(f"FAIL seed {seed}: execution error: {json.dumps(status.get('messages', ''))[:600]}")
                return 1
            files = collect_audio(entry)
            if not files:
                log(f"FAIL seed {seed}: the run produced no audio file")
                return 1
            take_files = []
            for item in files:
                query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                "type": item.get("type", "output")})
                data = http_bytes(f"{base}/view?{query}")
                dest = os.path.abspath(unique_path(args.out, os.path.basename(item["filename"])))
                with open(dest, "wb") as fh:
                    fh.write(data)
                info = audio_summary(dest)
                seconds = info.get("seconds")
                if info["bytes"] >= 20000:
                    info["music_seconds"] = music_seconds(dest, seconds or float(s["duration"]))
                log(f"ok   audio       {dest} {info} in {elapsed:.1f}s")
                if info["bytes"] < 20000:
                    log("FAIL the audio file is suspiciously small")
                    return 1
                if seconds is not None and abs(seconds - float(s["duration"])) > 1.5:
                    warn(f"{os.path.basename(dest)} is {seconds}s long; {s['duration']}s was asked for")
                ends = info["music_seconds"]
                if ends is not None and (seconds or float(s["duration"])) - ends > max(3.0, 0.1 * float(s["duration"])):
                    warn(f"{os.path.basename(dest)}: the music stops at {ends}s and the rest is silence; "
                         "try another seed or a shorter --duration")
                write_sidecar(dest, s, seed, args, stats, pid, elapsed, graphs[0][1], info["music_seconds"])
                take_files.append(dest)
            takes.append({"seed": seed, "prompt_id": pid, "files": take_files})
            written.extend(take_files)
    except BackendError as e:
        log(f"FAIL {e}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        log(f"FAIL backend connection: {e}")
        return 1
    log(json.dumps({"variant": s["variant"], "seed": seeds[0], "seeds": seeds, "files": written, "takes": takes}))
    return 0


def write_sidecar(dest, s, seed, args, stats, prompt_id, elapsed, lm, music_end=None):
    """Everything needed to hear this take again: `generate --from <this file>`."""
    record = {
        "tool": "comfy-forge music", "tool_version": tool_version(),
        "variant": s["variant"], "name": s["name"], "tags": s["tags"], "lyrics": s["lyrics"],
        "duration": float(s["duration"]), "bpm": int(s["bpm"]), "key": s["key"],
        "time_signature": str(s["time_signature"]), "language": s["language"], "seed": seed,
        "steps": s.get("steps") if s.get("steps") is not None else VARIANTS[s["variant"]]["steps"],
        "cfg": s.get("cfg") if s.get("cfg") is not None else VARIANTS[s["variant"]]["cfg"],
        "shift": s.get("shift") if s.get("shift") is not None else VARIANTS[s["variant"]]["shift"],
        "format": os.path.splitext(dest)[1].lstrip(".").lower(),
        "overrides": {k: s[k] for k in OVERRIDE_KEYS if s.get(k) is not None},
        "generate_audio_codes": not getattr(args, "no_audio_codes", False), "lm_overrides": lm,
        "comfyui_version": stats.get("system", {}).get("comfyui_version"),
        "prompt_id": prompt_id, "render_seconds": round(elapsed, 2), "music_seconds": music_end,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(dest + ".json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


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
        files = [name for _, name in variant_files(spec)]
        print(f"{key:9} {spec['title']}  steps={spec['steps']} cfg={spec['cfg']}")
        print(f"          {', '.join(files)}")
    print(f"default: {DEFAULT_VARIANT}")
    return 0


def cmd_check(args):
    base, stats = resolve_backend(args.url)
    if not stats:
        print(no_backend_message(args.url))
        return 3
    print(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')} at {base}")
    names = list(VARIANTS) if args.variant == "all" else [args.variant or DEFAULT_VARIANT]
    ready, failed = [], 0
    for name in names:
        problems = preflight(base, build_graph(name, "check", "", duration=10), name)
        if not problems:
            ready.append(name)
            print(f"ok   variant     {name}: nodes registered and model files visible")
        elif args.variant == "all":
            print(f"..   variant     {name}: not installed ({len(problems)} file(s) missing)")
        else:
            failed += 1
            for p in problems:
                print(f"FAIL {p}")
    if args.variant == "all":
        print(f"ready: {', '.join(ready) if ready else 'none (TOOLS\\Install-MusicModels.ps1 -WhatIf lists the files)'}")
        return 0 if ready else 1
    return 1 if failed else 0


def cmd_build(args):
    s = resolve_settings(args)
    seed = s.get("seed") if s.get("seed") is not None else 0
    prefix = f"audio/{slug(s['name'])}" if s["name"] else args.prefix
    graph, _ = graph_for(s, seed, prefix, args)
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
    url_help = "ComfyUI address; default: COMFYUI_URL, else the first of 8188 and 7820-7832 that answers"

    def prompt_args(sp):
        sp.add_argument("--variant", default=None, choices=list(VARIANTS), help=f"default {DEFAULT_VARIANT}")
        sp.add_argument("--from", dest="from_file", metavar="TAKE.json",
                        help="start from the settings of an earlier take (its .json sidecar); flags override")
        tags = sp.add_mutually_exclusive_group()
        tags.add_argument("--tags", help="style caption: genre, mood, instruments, vocals, production")
        tags.add_argument("--tags-file", help="UTF-8 file holding the style caption")
        grp = sp.add_mutually_exclusive_group()
        grp.add_argument("--lyrics", help="lyrics text, with [Verse] / [Chorus] section tags")
        grp.add_argument("--lyrics-file", help="UTF-8 file holding the lyrics")
        grp.add_argument("--instrumental", action="store_true", help=f"send {INSTRUMENTAL_LYRICS} instead of lyrics")
        sp.add_argument("--duration", type=float, default=None, help="seconds, 1 to 600 (default 120)")
        sp.add_argument("--bpm", type=int, default=None, help="10 to 300 (default 120)")
        sp.add_argument("--key", default=None, help="e.g. 'E minor', 'Bb major' (default C major)")
        sp.add_argument("--time-signature", default=None, choices=TIME_SIGNATURES, help="default 4")
        sp.add_argument("--language", default=None, help="lyrics language code (default en; ja, zh, ko, es, ...)")
        sp.add_argument("--seed", type=int, default=None, help="default: random, and printed")
        sp.add_argument("--reseed", action="store_true", help="with --from: ignore the take's seed, pick new ones")
        sp.add_argument("--steps", type=int, default=None)
        sp.add_argument("--cfg", type=float, default=None)
        sp.add_argument("--shift", type=float, default=None)
        sp.add_argument("--format", choices=FORMATS, default=None)
        sp.add_argument("--name", default="", help="file name stem for the take, e.g. 'boss-theme'")
        sp.add_argument("--prefix", default="audio/comfy-forge", help="ComfyUI output prefix when --name is not given")

    v = sub.add_parser("variants", help="list the model variants")
    v.add_argument("--json", action="store_true", help="machine-readable list with model subfolders")
    c = sub.add_parser("check", help="is the backend ready for a variant (or --variant all)")
    c.add_argument("--variant", default=DEFAULT_VARIANT, choices=list(VARIANTS) + ["all"])
    c.add_argument("--url", default=os.environ.get("COMFYUI_URL"), help=url_help)
    b = sub.add_parser("build", help="write the API graph without contacting a backend")
    prompt_args(b)
    b.add_argument("--out", default="", help="write the graph here instead of stdout")
    g = sub.add_parser("generate", help="render one or more takes and save them")
    prompt_args(g)
    g.add_argument("--url", default=os.environ.get("COMFYUI_URL"), help=url_help)
    g.add_argument("--count", type=int, default=1, help="number of takes; seeds count up from --seed")
    g.add_argument("--out", default=os.path.join("out", "music"), help="folder for the audio files (default out/music)")
    g.add_argument("--timeout", type=int, default=1800, help="seconds to wait for each take")
    g.add_argument("--no-audio-codes", action="store_true",
                   help="skip the language-model planning pass (faster, lower quality)")
    g.add_argument("--lm-cfg", type=float, default=None)
    g.add_argument("--lm-temperature", type=float, default=None)
    g.add_argument("--lm-top-p", type=float, default=None)
    g.add_argument("--lm-top-k", type=int, default=None)
    return p


def main(argv=None):
    # A path or lyric the console code page cannot show must not end the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
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
