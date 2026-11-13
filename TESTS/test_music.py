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
import subprocess
import sys
import tempfile
import threading
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
FLAC = b"fLaC" + b"\x00\x00\x00\x22" + (4096).to_bytes(2, "big") * 2 + b"\x00" * 6 + \
    ((44100 << 44) | (1 << 41) | (15 << 36) | (44100 * 3)).to_bytes(8, "big") + b"\x00" * 16


class Stub(BaseHTTPRequestHandler):
    visible = True      # are the model files listed?
    submitted = []

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
        if self.path.startswith("/history/"):
            pid = self.path.rsplit("/", 1)[1]
            return self.send(200, {pid: {"status": {"status_str": "success"},
                                         "outputs": {"16": {"audio": [{"filename": "comfy-forge_00001_.flac",
                                                                       "subfolder": "audio", "type": "output"}]}}}})
        if self.path.startswith("/view?"):
            return self.send(200, FLAC + b"\x01" * 30000, "audio/flac")
        self.send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.submitted.append(body["prompt"])
        self.send(200, {"prompt_id": "stub-1", "number": 1, "node_errors": {}})


def run_cli(args, url=None, timeout=60):
    cmd = [sys.executable, os.path.join(REPO, "TOOLS", "music.py")] + args
    if url:
        cmd += ["--url", url]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_cli():
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "music folder (a!)")
        r = run_cli(["generate", "--tags", "lo-fi, mellow", "--instrumental", "--duration", "30",
                     "--seed", "5", "--out", out], url)
        check(r.returncode == 0, f"generate against a stub backend exits 0 ({r.stdout[-200:]}{r.stderr[-200:]})")
        written = os.listdir(out) if os.path.isdir(out) else []
        check(written == ["comfy-forge_00001_.flac"], "audio landed in a folder with spaces and punctuation")
        sent = Stub.submitted[-1] if Stub.submitted else {}
        check(sent.get("11", {}).get("inputs", {}).get("lyrics") == music.INSTRUMENTAL_LYRICS, "--instrumental sends the tag")
        check(sent.get("14", {}).get("inputs", {}).get("seed") == 5, "--seed reaches the sampler")
        check(sent.get("16", {}).get("inputs", {}).get("format") == "flac", "aio default format is flac")
        check("seed 5" in r.stdout, "the seed is printed so a take can be reproduced")

        # lyrics from a UTF-8 file round-trip untouched
        lyr = os.path.join(tmp, "lyrics file.txt")
        text = "[Verse]\nCafé lights, 東京の夜\n[Chorus]\nla la"
        with open(lyr, "w", encoding="utf-8") as fh:
            fh.write(text)
        r = run_cli(["generate", "--tags", "pop", "--lyrics-file", lyr, "--language", "ja", "--out", out], url)
        check(r.returncode == 0 and Stub.submitted[-1]["11"]["inputs"]["lyrics"] == text, "lyrics file passes through byte-exact")

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
        check(r.returncode == 3, "no backend: exit 3 (unverified), not a pass")
        r = run_cli(["generate", "--tags", "pop", "--duration", "9000", "--out", out], url)
        check(r.returncode == 2, "duration out of range: exit 2")
        r = run_cli(["generate", "--tags", "pop", "--key", "H sharp", "--out", out], url)
        check(r.returncode == 2, "bad key: exit 2")
        r = run_cli(["generate", "--tags", "   ", "--out", out], url)
        check(r.returncode == 2, "empty tags: exit 2")
        g = os.path.join(tmp, "graph.json")
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


def main():
    test_variants()
    test_installer_table()
    test_graph_rules()
    test_cli()
    print(f"\n{'PASS' if not fail else 'FAIL'}: {fail} failure(s)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
