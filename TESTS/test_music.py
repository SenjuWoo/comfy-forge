"""ACE-Step 1.5 music support: the generator matches the shipped templates.

No GPU, model or ComfyUI install is needed. What this proves:
  * every variant in TOOLS/music.py has a template in presets/music/ and the
    sampler, shift, loader files and prompt-encoder values agree with it
  * the API graph the tool builds has exactly the wiring the template has
  * the installer's file table agrees with the model URLs inside each template
  * generate drives a (stub) backend end to end as a real process, and refuses
    cleanly when the backend is absent, a model file is missing, or the
    arguments are wrong
What it does not prove: that a real backend renders music. That is
TESTS/integration_music.py, which needs the weights.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "TOOLS"))
import music  # noqa: E402

PRESETS = os.path.join(REPO, "presets", "music")
fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name)
        fail += 1


def load(name):
    with open(os.path.join(PRESETS, name), encoding="utf-8") as fh:
        return json.load(fh)


def by_type(doc, type_name):
    return [n for n in doc["nodes"] if n["type"] == type_name]


VALUE_SOURCES = ("PrimitiveNode", "PrimitiveInt")
# Older templates save with SaveAudioMP3; the tool uses SaveAudioAdvanced for every
# variant (same input, format is a widget). Treated as the same saver here.
SAVER_ALIASES = {"SaveAudioMP3": "SaveAudioAdvanced", "SaveAudio": "SaveAudioAdvanced", "SaveAudioOpus": "SaveAudioAdvanced"}


def canon(type_name):
    return SAVER_ALIASES.get(type_name, type_name)


def template_edges(doc):
    """{(src class, src slot, dst class, dst input)} for node-to-node links only.

    Primitive nodes are literals in API format, so their links are not edges.
    """
    nodes = {n["id"]: n for n in doc["nodes"]}
    edges = set()
    for _id, origin, oslot, target, tslot, _type in doc["links"]:
        src, dst = nodes[origin], nodes[target]
        if src["type"] in VALUE_SOURCES:
            continue
        edges.add((src["type"], oslot, canon(dst["type"]), dst["inputs"][tslot]["name"]))
    return edges


def graph_edges(graph):
    edges = set()
    for node in graph.values():
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                edges.add((graph[value[0]]["class_type"], value[1], node["class_type"], name))
    return edges


def widgets_by_name(node, names):
    """Map a node's UI widget values onto names, skipping the seed 'control' string."""
    values = list(node["widgets_values"])
    out, i = {}, 0
    for name in names:
        out[name] = values[i]
        i += 1
        if name == "seed":
            i += 1  # "fixed" / "randomize"
    return out


ENCODER_FIELDS = ["tags", "lyrics", "seed", "bpm", "duration", "timesignature", "language",
                  "keyscale", "generate_audio_codes", "cfg_scale", "temperature", "top_p", "top_k", "min_p"]


def test_variants():
    for key, spec in music.VARIANTS.items():
        doc = load(spec["template"])
        graph = music.build_graph(key, "tags", "lyrics", seed=7)
        check(graph_edges(graph) == template_edges(doc), f"{key}: wiring equals {spec['template']}")
        check(sorted(n["class_type"] for n in graph.values()) ==
              sorted(canon(n["type"]) for n in doc["nodes"] if n["type"] not in VALUE_SOURCES + ("Note", "MarkdownNote")),
              f"{key}: same node classes as the template")

        ks = widgets_by_name(by_type(doc, "KSampler")[0], ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"])
        got = graph["14"]["inputs"]
        check((ks["steps"], ks["cfg"], ks["sampler_name"], ks["scheduler"], ks["denoise"]) ==
              (got["steps"], got["cfg"], got["sampler_name"], got["scheduler"], got["denoise"]),
              f"{key}: sampler steps/cfg/euler/simple/denoise equal the template")
        shift = by_type(doc, "ModelSamplingAuraFlow")[0]["widgets_values"][0]
        check(graph["10"]["inputs"]["shift"] == shift, f"{key}: shift equals the template")

        enc = widgets_by_name(by_type(doc, "TextEncodeAceStepAudio1.5")[0], ENCODER_FIELDS)
        gi = graph["11"]["inputs"]
        check(all(enc[f] == gi[f] for f in ("cfg_scale", "temperature", "top_p", "top_k", "min_p", "generate_audio_codes")),
              f"{key}: prompt-encoder defaults equal the template")

        if spec["loader"] == "checkpoint":
            names = [by_type(doc, "CheckpointLoaderSimple")[0]["widgets_values"][0]]
            check(names == [graph["1"]["inputs"]["ckpt_name"]], f"{key}: checkpoint file equals the template")
        else:
            unet = by_type(doc, "UNETLoader")[0]["widgets_values"][0]
            clips = by_type(doc, "DualCLIPLoader")[0]["widgets_values"][:3]
            vae = by_type(doc, "VAELoader")[0]["widgets_values"][0]
            c = graph["2"]["inputs"]
            check(unet == graph["1"]["inputs"]["unet_name"] and clips == [c["clip_name1"], c["clip_name2"], c["type"]]
                  and vae == graph["3"]["inputs"]["vae_name"], f"{key}: loader files equal the template")


def test_installer_table():
    text = open(os.path.join(REPO, "TOOLS", "Install-MusicModels.ps1"), encoding="utf-8").read()
    rows = re.findall(r"Rel = '([^']+)';\s+Repo = '([^']+)';\s+Bytes = (\d+)", text)
    table = {rel.replace("\\", "/"): (repo, int(size)) for rel, repo, size in rows}
    check(len(table) == 9, "installer lists 9 files")
    base = "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
    needed = set()
    for key, spec in music.VARIANTS.items():
        doc = load(spec["template"])
        for node in doc["nodes"]:
            for model in (node.get("properties") or {}).get("models", []):
                rel = f"{model['directory']}/{model['name']}"
                needed.add(rel)
                repo = table.get(rel, (None,))[0]
                check(repo is not None and model["url"] == base + repo, f"{key}: installer URL for {model['name']} equals the template")
    check(needed == set(table), "installer ships exactly the files the templates ask for")
    check(all(size > 1_000_000 for _, size in table.values()), "every installer size is a real byte count")


# ---------------------------------------------------------------------------
# Stub backend: speaks just enough of ComfyUI's HTTP API to drive generate.
# ---------------------------------------------------------------------------
def flac_bytes(seconds, rate=48000):
    """A FLAC header whose STREAMINFO says `seconds` long, padded past the size floor."""
    info = (rate << 44) | (1 << 41) | (15 << 36) | int(seconds * rate)
    return b"fLaC" + b"\x00\x00\x00\x22" + (4096).to_bytes(2, "big") * 2 + b"\x00" * 6 + \
        info.to_bytes(8, "big") + b"\x00" * 16 + b"\x01" * 30000


class Stub(BaseHTTPRequestHandler):
    visible = True        # are the model files listed?
    fixed_name = None     # force every output to this file name
    submitted = []
    jobs = {}             # prompt_id -> (filename, seconds)

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/system_stats":
            return self.send(200, {"system": {"comfyui_version": "0.44.0-stub"}})
        if self.path == "/object_info":
            files = ["ace_step_1.5_turbo_aio.safetensors"] if Stub.visible else []
            classes = {c: {"input": {"required": {}}} for c in
                       ("TextEncodeAceStepAudio1.5", "ModelSamplingAuraFlow", "ConditioningZeroOut", "KSampler",
                        "EmptyAceStep1.5LatentAudio", "VAEDecodeAudio", "SaveAudioAdvanced", "UNETLoader",
                        "DualCLIPLoader", "VAELoader")}
            classes["CheckpointLoaderSimple"] = {"input": {"required": {"ckpt_name": [files]}}}
            return self.send(200, classes)
        if self.path == "/queue":
            return self.send(200, {"queue_running": [], "queue_pending": []})
        if self.path.startswith("/history/"):
            pid = self.path.rsplit("/", 1)[1]
            name, _seconds = Stub.jobs[pid]
            return self.send(200, {pid: {"status": {"status_str": "success"},
                                         "outputs": {"16": {"audio": [{"filename": name, "subfolder": "audio",
                                                                       "type": "output"}]}}}})
        if self.path.startswith("/view?"):
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["filename"][0]
            seconds = next(sec for n, sec in Stub.jobs.values() if n == name)
            return self.send(200, flac_bytes(seconds), "audio/flac")
        self.send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        graph = body["prompt"]
        Stub.submitted.append(graph)
        pid = f"stub-{len(Stub.submitted)}"
        stem = graph["16"]["inputs"]["filename_prefix"].rsplit("/", 1)[-1]
        name = Stub.fixed_name or f"{stem}_{len(Stub.submitted):05d}_.flac"
        Stub.jobs[pid] = (name, graph["13"]["inputs"]["seconds"])
        self.send(200, {"prompt_id": pid, "number": len(Stub.submitted), "node_errors": {}})


def run_cli(args, url=None, timeout=60, env=None):
    cmd = [sys.executable, os.path.join(REPO, "TOOLS", "music.py")] + args
    if url:
        cmd += ["--url", url]
    full_env = {k: v for k, v in os.environ.items() if k not in ("COMFYUI_URL", "COMFY_FORGE_PROBE_PORTS")}
    full_env.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=full_env)


def last_json(r):
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def strip_seed(graph):
    g = json.loads(json.dumps(graph))
    g["11"]["inputs"]["seed"] = g["14"]["inputs"]["seed"] = None
    return g


def test_cli():
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "music folder (a!)")
        r = run_cli(["generate", "--tags", "lo-fi, mellow", "--instrumental", "--duration", "30",
                     "--seed", "5", "--out", out], url)
        check(r.returncode == 0, f"generate against a stub backend exits 0 ({r.stdout[-200:]}{r.stderr[-200:]})")
        written = sorted(os.listdir(out)) if os.path.isdir(out) else []
        check(written == ["comfy-forge_00001_.flac", "comfy-forge_00001_.flac.json"],
              f"audio and its sidecar landed in a folder with spaces and punctuation ({written})")
        sent = Stub.submitted[-1] if Stub.submitted else {}
        check(sent.get("11", {}).get("inputs", {}).get("lyrics") == music.INSTRUMENTAL_LYRICS, "--instrumental sends the tag")
        check(sent.get("14", {}).get("inputs", {}).get("seed") == 5, "--seed reaches the sampler")
        check(sent.get("16", {}).get("inputs", {}).get("format") == "flac", "aio default format is flac")
        check("seed 5" in r.stdout, "the seed is printed so a take can be reproduced")
        check("warn" not in r.stderr, "a take of the asked-for length raises no length warning")
        side = os.path.join(out, "comfy-forge_00001_.flac.json")
        rec = json.load(open(side, encoding="utf-8")) if os.path.isfile(side) else {}
        check(rec.get("seed") == 5 and rec.get("tags") == "lo-fi, mellow" and rec.get("variant") == "aio"
              and rec.get("steps") == 8 and rec.get("comfyui_version") == "0.44.0-stub" and rec.get("overrides") == {},
              "sidecar records seed, caption, variant, effective steps and backend version")

        # --from replays the take exactly; --reseed keeps everything but the seed
        r = run_cli(["generate", "--from", side, "--out", out], url)
        check(r.returncode == 0 and Stub.submitted[-1] == Stub.submitted[0], "--from rebuilds the identical graph")
        r = run_cli(["generate", "--from", side, "--reseed", "--out", out], url)
        check(r.returncode == 0 and Stub.submitted[-1]["14"]["inputs"]["seed"] != 5
              and strip_seed(Stub.submitted[-1]) == strip_seed(Stub.submitted[0]), "--reseed changes only the seed")
        g = os.path.join(tmp, "graph.json")
        r = run_cli(["build", "--from", side, "--variant", "xl-sft", "--out", g])
        built = json.load(open(g, encoding="utf-8")) if r.returncode == 0 else {}
        check(built.get("14", {}).get("inputs", {}).get("steps") == 50,
              "--from an aio take with --variant xl-sft uses xl-sft's 50 steps, not the take's 8")
        r = run_cli(["build", "--from", side, "--steps", "12", "--out", g])
        check(r.returncode == 0 and json.load(open(g, encoding="utf-8"))["14"]["inputs"]["steps"] == 12, "a flag overrides --from")
        bad = os.path.join(tmp, "not a take.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        r = run_cli(["build", "--from", bad])
        check(r.returncode == 2 and "sidecar" in r.stderr, "--from a file that is not a sidecar: exit 2")

        # several takes, named, never overwriting
        r = run_cli(["generate", "--tags", "boss fight, orchestral metal", "--instrumental", "--count", "3",
                     "--seed", "10", "--name", "Boss Theme!", "--duration", "45", "--out", out], url)
        res = last_json(r)
        check(r.returncode == 0 and res.get("seeds") == [10, 11, 12] and len(res.get("files", [])) == 3,
              "--count 3 renders three takes with seeds 10, 11, 12")
        check([s["14"]["inputs"]["seed"] for s in Stub.submitted[-3:]] == [10, 11, 12], "each take reaches the sampler with its own seed")
        check(Stub.submitted[-1]["16"]["inputs"]["filename_prefix"] == "audio/Boss-Theme", "--name becomes a safe file prefix")
        check(all(os.path.isfile(f + ".json") for f in res.get("files", [])), "every take has a sidecar")
        boss = (res.get("files") or [""])[0]
        r = run_cli(["build", "--from", boss + ".json", "--out", g])
        check(r.returncode == 0 and json.load(open(g, encoding="utf-8"))["16"]["inputs"]["filename_prefix"] == "audio/Boss-Theme",
              "--from keeps the take's --name")
        check(all(os.path.isabs(f) for f in res.get("files", [])), "the result lists absolute paths")
        Stub.fixed_name = "same_00001_.flac"
        first = (last_json(run_cli(["generate", "--tags", "pop", "--duration", "20", "--out", out], url)).get("files") or [""])[0]
        second = (last_json(run_cli(["generate", "--tags", "pop", "--duration", "20", "--out", out], url)).get("files") or [""])[0]
        Stub.fixed_name = None
        check(bool(first and second) and first != second and os.path.isfile(first) and os.path.isfile(second),
              "a second take with the same backend file name does not overwrite the first")

        # captions and lyrics from files round-trip untouched, BOM dropped
        lyr = os.path.join(tmp, "lyrics file.txt")
        text = "[Verse]\nCafé lights, 東京の夜\n[Chorus]\nla la"
        with open(lyr, "w", encoding="utf-8") as fh:
            fh.write(text)
        cap = os.path.join(tmp, "caption.txt")
        with open(cap, "w", encoding="utf-8-sig") as fh:
            fh.write("city pop, warm Rhodes, female vocal")
        r = run_cli(["generate", "--tags-file", cap, "--lyrics-file", lyr, "--language", "ja", "--out", out], url)
        check(r.returncode == 0 and Stub.submitted[-1]["11"]["inputs"]["lyrics"] == text, "lyrics file passes through byte-exact")
        check(Stub.submitted[-1]["11"]["inputs"]["tags"] == "city pop, warm Rhodes, female vocal", "--tags-file is read, BOM dropped")

        # a non-ASCII folder on a console that cannot show it
        r = run_cli(["generate", "--tags", "pop", "--duration", "20", "--out", os.path.join(tmp, "東京 takes")], url,
                    env={"PYTHONIOENCODING": "cp1252"})
        check(r.returncode == 0, f"a path the console cannot encode does not crash the run ({r.stderr[-160:]})")

        r = run_cli(["generate", "--tags", "pop", "--lyrics", "la " * 1500, "--out", out], url)
        check(r.returncode == 0 and "4096" in r.stderr, "lyrics past 4096 characters warn but still render")

        # backend discovery: no --url, no COMFYUI_URL
        r = run_cli(["check"], env={"COMFY_FORGE_PROBE_PORTS": f"1,{port}"})
        check(r.returncode == 0 and f"at {url}" in r.stdout, "the backend is found by probing ports")
        r = run_cli(["generate", "--tags", "pop", "--out", out], env={"COMFY_FORGE_PROBE_PORTS": "1,2"})
        check(r.returncode == 3 and "or pass --url" in r.stdout, "nothing answering: exit 3 with what to start")
        r = run_cli(["check"], env={"COMFYUI_URL": url})
        check(r.returncode == 0, "COMFYUI_URL is honoured")
        r = run_cli(["check", "--variant", "all"], url)
        check(r.returncode == 0 and "ready: aio" in r.stdout and "xl-sft: not installed" in r.stdout,
              "check --variant all lists what is ready and what is not")

        Stub.visible = False
        before = len(Stub.submitted)
        r = run_cli(["generate", "--tags", "pop", "--out", out], url)
        check(r.returncode == 1 and "Install-MusicModels" in r.stdout and len(Stub.submitted) == before,
              "missing model file: exit 1, names the installer, submits nothing")
        r = run_cli(["check"], url)
        check(r.returncode == 1, "check fails when the model is not visible")
        Stub.visible = True
        r = run_cli(["check"], url)
        check(r.returncode == 0, "check passes when nodes and files are visible")

        r = run_cli(["generate", "--tags", "pop", "--out", out], "http://127.0.0.1:1")
        check(r.returncode == 3, "no backend at --url: exit 3 (unverified), not a pass")
        for bad_args, why in ((["--duration", "9000"], "duration out of range"), (["--key", "H sharp"], "bad key"),
                              (["--language", "xx"], "unknown language"), (["--count", "0"], "zero takes"),
                              (["--seed", "-4"], "negative seed")):
            before = len(Stub.submitted)
            r = run_cli(["generate", "--tags", "pop", "--out", out] + bad_args, url)
            check(r.returncode == 2 and len(Stub.submitted) == before, f"{why}: exit 2 before anything is sent")
        r = run_cli(["generate", "--tags", "   ", "--out", out], url)
        check(r.returncode == 2, "empty tags: exit 2")
        r = run_cli(["generate", "--out", out], url)
        check(r.returncode == 2 and "--tags" in r.stderr, "no caption at all: exit 2 naming --tags")
        r = run_cli(["build", "--variant", "xl-sft", "--tags", "synthwave", "--out", g])
        check(r.returncode == 0 and json.load(open(g, encoding="utf-8"))["14"]["inputs"]["steps"] == 50, "build writes the graph offline")
    server.shutdown()


def test_graph_rules():
    g = music.build_graph("turbo", "t", "l", duration=45.5, bpm=90, key="Bb major", audio_format="mp3")
    check(g["13"]["inputs"]["seconds"] == 45.5 == g["11"]["inputs"]["duration"], "duration feeds both latent and encoder")
    check(g["16"]["inputs"]["format"] == "mp3" and g["16"]["inputs"]["format.quality"] == "V0", "mp3 carries its quality key")
    check("format.quality" not in music.build_graph("aio", "t", "l")["16"]["inputs"], "flac has no quality key")
    check(music.build_graph("xl-sft", "t", "l")["11"]["inputs"]["top_p"] == 1.0, "xl-sft keeps its template top_p")
    try:
        music.build_graph("nope", "t", "l")
        check(False, "unknown variant is refused")
    except music.UsageError:
        check(True, "unknown variant is refused")


def test_music_seconds():
    """The early-stop check finds where the music ends (needs ffmpeg; skipped without it)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("skip music_seconds: ffmpeg is not on PATH")
        return
    with tempfile.TemporaryDirectory() as tmp:
        early = os.path.join(tmp, "early stop.flac")
        full = os.path.join(tmp, "full.flac")
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-f", "lavfi",
                        "-i", "anullsrc=r=48000:cl=mono", "-filter_complex", "[0][1]concat=n=2:v=0:a=1,atrim=0:9",
                        "-y", early], check=True)
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-y", full], check=True)
        check(music.music_seconds(early, 9.0) == 5.0, "a take that goes silent at 5 s of 9 is reported as ending at 5 s")
        check(music.music_seconds(full, 6.0) == 6.0, "a take with sound to the end is reported at full length")
        check(music.music_seconds(os.path.join(tmp, "missing.flac"), 6.0) is None, "an unreadable file gives no answer, not a guess")


def main():
    test_variants()
    test_installer_table()
    test_graph_rules()
    test_music_seconds()
    test_cli()
    print(f"\n{'PASS' if not fail else 'FAIL'}: {fail} failure(s)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
