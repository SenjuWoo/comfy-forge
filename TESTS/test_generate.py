"""image-stack.ps1 start / stop / generate / capabilities, against a stand-in backend.

No GPU, no SwarmUI, no model: TESTS/fixtures/backend_stub.py answers SwarmUI's
API and records every request, and a fake SwarmUI folder whose launch-windows.bat
starts that stub proves `start` and `stop` drive a real process. The state folder
is redirected (COMFY_FORGE_STATE_DIR), so the machine's own install record,
backend record and logs are never read or written.

What this proves:
  - a render with no -Model picks a model SwarmUI lists (0.10.0 named a file most
    installs do not have, and the default render was refused)
  - a non-ASCII prompt arrives as UTF-8 and its image is found again
  - every generate option reaches the request under SwarmUI's own parameter id
  - start waits for the backend, stop ends only what start launched
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLI = os.path.join(REPO, "TOOLS", "image-stack.ps1")
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import backend_stub  # noqa: E402

fail = 0


def check(cond, name, detail=""):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name, detail)
        fail += 1


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_closed(port, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if not listening(port):
            return True
        time.sleep(0.2)
    return False


class Bench:
    """A fake SwarmUI install, a redirected state folder, and the environment that points at both."""

    def __init__(self, root):
        self.root = root
        self.port = free_port()
        # A space and parentheses: the launcher path is quoted, or `start` fails here.
        self.swarm = os.path.join(root, "Swarm UI (fake)")
        self.state = os.path.join(root, "state")
        self.record = os.path.join(root, "record")
        self.out = os.path.join(root, "out")
        os.makedirs(os.path.join(self.swarm, "Data"))
        os.makedirs(os.path.join(self.swarm, "Models", "Stable-Diffusion"))
        # One music variant "installed" (an empty file is enough for the readiness check).
        checkpoints = os.path.join(self.swarm, "dlbackend", "comfy", "ComfyUI", "models", "checkpoints")
        os.makedirs(checkpoints)
        open(os.path.join(checkpoints, "ace_step_1.5_turbo_aio.safetensors"), "w").close()
        os.makedirs(self.state)
        open(os.path.join(self.swarm, "SwarmUI.sln"), "w").close()
        open(os.path.join(self.swarm, "Data", "Backends.fds"), "w").close()
        with open(os.path.join(self.swarm, "Data", "Settings.fds"), "w") as fh:
            fh.write(f"Network:\n\tPort: {self.port}\n")
        shutil.copy(os.path.join(HERE, "fixtures", "backend_stub.py"), os.path.join(self.swarm, "stub.py"))
        with open(os.path.join(self.swarm, "launch-windows.bat"), "w") as fh:
            fh.write('@echo off\r\n"%COMFY_FORGE_TEST_PYTHON%" "%~dp0stub.py" %*\r\n')
        self.env = dict(os.environ)
        for name in ("COMFYUI_ROOT", "COMFYUI_URL", "COMFY_FORGE_MODEL", "COMFY_FORGE_NO_AUTOSTART", "COMFY_FORGE_TEST_FAIL"):
            self.env.pop(name, None)
        self.env.update(
            COMFY_FORGE_STATE_DIR=self.state, SWARMUI_ROOT=self.swarm, COMFY_FORGE_PROBE_PORTS=str(self.port),
            COMFY_FORGE_TEST_PYTHON=sys.executable, COMFY_FORGE_TEST_PORT=str(self.port),
            COMFY_FORGE_TEST_RECORD=self.record, COMFY_FORGE_TEST_LOADING="1.5",
            # The stub imports TOOLS/model3d.py from the repo, not from the fake folder.
            PYTHONPATH=os.path.join(REPO, "TOOLS"))

    def command(self, *args):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", CLI] + list(args)

    def cli(self, *args, env=None, timeout=180):
        # Output goes to a file, not a pipe: if a started backend ever inherits the
        # handle again, this suite reports it (see the pipe check) instead of hanging.
        self.calls = getattr(self, "calls", 0) + 1
        log = os.path.join(self.root, f"cli-{self.calls}.txt")
        with open(log, "wb") as fh:
            try:
                code = subprocess.run(self.command(*args), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      env=env or self.env, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                code = -1
        with open(log, encoding="utf-8", errors="replace") as fh:
            return code, fh.read()

    def start_through_pipe(self):
        """`start` with its output captured through a pipe, as an agent runs it.

        Returns (exit code, True when the pipe closed once the command returned)."""
        import threading
        proc = subprocess.Popen(self.command("start"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=self.env)
        try:
            code = proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            return -1, False
        reader = threading.Thread(target=proc.stdout.read, daemon=True)
        reader.start()
        reader.join(10)
        return code, not reader.is_alive()

    def requests(self, route="GenerateText2Image"):
        path = os.path.join(self.record, "requests.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [r["body"] for r in map(json.loads, fh) if r["route"] == route]

    def backend_record(self):
        path = os.path.join(self.state, "backend.json")
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)


def last_json(text):
    for line in reversed(text.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return None


def main():
    if os.name != "nt" or not shutil.which("powershell"):
        print("UNVERIFIED: this suite drives Windows PowerShell")
        return 3
    root = tempfile.mkdtemp(prefix="cf-generate-")
    b = Bench(root)
    try:
        run(b)
    finally:
        b.cli("stop")
        # Anything a failed check left running was started from this folder.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
                        + os.path.basename(root) + "') -and $_.ProcessId -ne $PID } | "
                        "ForEach-Object { taskkill /PID $_.ProcessId /T /F } | Out-Null"],
                       capture_output=True, timeout=60)
        shutil.rmtree(root, ignore_errors=True)
    if fail:
        print(f"GENERATE: {fail} FAILED")
        return 1
    print("GENERATE: PASS")
    return 0


def run(b):
    # --- refusals that must not start anything -------------------------------
    code, text = b.cli("generate", "-Prompt", "x", "-NoStart")
    check(code == 1 and "start" in text and not listening(b.port), "generate -NoStart refuses when the backend is down", text)
    code, text = b.cli("generate", "-Preset", "no such preset")
    check(code == 1 and "no preset named" in text and not listening(b.port),
          "an unknown preset is refused before a backend is started", text)
    code, text = b.cli("generate", "-Prompt", "x", "-Port", str(b.port))
    check(code == 1 and not listening(b.port), "generate -Port never starts a backend of its own", text)
    for args, what in ((["-Set", "novalue"], "-Set without ="), (["-Mask", "m.png"], "-Mask without -InitImage"),
                       (["-InitImage", "missing.png"], "a missing -InitImage"), (["-Creativity", "3"], "-Creativity out of range"),
                       (["-Upscale", "9"], "-Upscale out of range"), (["-Count", "0"], "-Count 0")):
        code, text = b.cli("generate", "-Prompt", "x", *args)
        check(code == 1 and not listening(b.port), f"{what} is refused before a backend is started", text)

    # --- start ----------------------------------------------------------------
    began = time.time()
    code, text = b.cli("start")
    rec = b.backend_record()
    check(code == 0 and listening(b.port), "start launches the backend headless", text)
    check(time.time() - began >= 1.4 and "waiting for the ComfyUI backend" in text,
          "start waits until the backend has loaded, not just until the web server answers", text)
    check(bool(rec) and rec.get("kind") == "SwarmUI" and rec.get("port") == b.port and os.path.isfile(rec.get("log", "")),
          "start records what it launched and where the log is", str(rec))
    code, text = b.cli("start")
    check(code == 0 and "already answering" in text and b.backend_record()["pid"] == rec["pid"],
          "a second start changes nothing", text)
    code, text = b.cli("status")
    check(code == 0 and "started by comfy-forge" in text, "status says who started the backend", text)

    # --- default render: model choice, UTF-8, result line, sidecar ------------
    prompt = "café 猫 fox"
    code, text = b.cli("generate", "-Prompt", prompt, "-Out", b.out, "-Seed", "5", "-Count", "2", "-Json")
    result = last_json(text)
    sent = b.requests()
    check(code == 0 and result is not None, "generate renders and ends with one JSON line", text)
    body = sent[-1] if sent else {}
    check(body.get("model") == "Anima/Anime/woomix.safetensors",
          "with no -Model, the shallowest model of the preset's family is used", str(body.get("model")))
    check(body.get("prompt") == prompt, "a non-ASCII prompt reaches the backend as UTF-8", repr(body.get("prompt")))
    check(body.get("seed") == 5 and body.get("images") == 2 and "vae" not in body,
          "seed and count are sent; a preset's VAE 'Automatic' is not", str({k: body.get(k) for k in ("seed", "images", "vae")}))
    files = (result or {}).get("files") or []
    check(len(files) == 2 and all(os.path.isfile(f) and os.path.isfile(f + ".json") for f in files),
          "every image is saved with a .json sidecar", str(files))
    check(all("猫" in os.path.basename(f) for f in files),
          "an image SwarmUI named after a non-ASCII prompt is downloaded under that name", str(files))
    check((result or {}).get("seeds") == [5, 6] and (result or {}).get("model") == body.get("model"),
          "the JSON line carries the seeds and the model", str(result))
    if files:
        with open(files[1] + ".json", encoding="utf-8") as fh:
            side = json.load(fh)
        check(side.get("seed") == 6 and side.get("prompt") == prompt and side.get("preset"),
              "a sidecar records the seed of its own image", str(side))
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Json")
    seed = (last_json(text) or {}).get("seed")
    check(code == 0 and isinstance(seed, int) and seed >= 0 and b.requests()[-1]["seed"] == seed,
          "a random seed is chosen here and reported, so the render can be repeated", text)

    # --- model resolution -------------------------------------------------------
    before = len(b.requests())
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Model", "Deep")
    check(code == 0 and b.requests()[-1]["model"] == "Anima/Anime/3.8B/Deep.safetensors", "-Model takes a bare file name", text)
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Model", "dup")
    check(code == 1 and "matches 2" in text, "a -Model name that fits two files is refused, not guessed", text)
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Model", "nope")
    check(code == 1 and "woomix" in text, "an unknown -Model is refused and the installed ones are listed", text)
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Preset", "SDXL Illustrious - Quality")
    check(code == 1 and "preset" in text, "a preset whose own model is not installed is refused", text)
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Preset", "FLUX1 Dev - Quality")
    check(code == 1 and "no installed model fits" in text, "a preset with no installed model of its family is refused", text)
    check(len(b.requests()) == before + 1, "none of the refused renders reached the backend")
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Preset", "SDXL Base - Realism Quality")
    check(code == 0 and b.requests()[-1]["model"] == "SDXL/dup.safetensors", "an SDXL preset picks an SDXL model", text)
    env = dict(b.env, COMFY_FORGE_MODEL="Deep")
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, env=env)
    check(code == 0 and b.requests()[-1]["model"] == "Anima/Anime/3.8B/Deep.safetensors",
          "COMFY_FORGE_MODEL sets the default model", text)

    # --- every option under SwarmUI's own parameter id --------------------------
    init = os.path.join(b.root, "init image.png")
    mask = os.path.join(b.root, "mask.png")
    with open(init, "wb") as fh:
        fh.write(backend_stub.png(640, 480))
    with open(mask, "wb") as fh:
        fh.write(backend_stub.png(640, 480))
    code, text = b.cli("generate", "-Prompt", "edit", "-Out", b.out, "-Negative", "blurry, text", "-InitImage", init,
                       "-Creativity", "0.4", "-Mask", mask, "-Lora", "ink:0.8,big", "-Transparent", "-Tileable",
                       "-Upscale", "1.5", "-Steps", "12", "-Cfg", "3", "-Set", "aspectratio=16:9,colordepth=16bit")
    body = b.requests()[-1] if code == 0 else {}
    check(code == 0, "a render with every option runs", text)
    check(body.get("negativeprompt") == "blurry, text", "-Negative -> negativeprompt (a comma stays inside the value)")
    data = str(body.get("initimage", ""))
    check(data.startswith("data:image/png;base64,") and base64.b64decode(data.split(",", 1)[1]) == open(init, "rb").read(),
          "-InitImage -> initimage, the file's own bytes")
    check(body.get("initimagecreativity") == 0.4 and str(body.get("maskimage", "")).startswith("data:image/png"),
          "-Creativity -> initimagecreativity, -Mask -> maskimage")
    check((body.get("width"), body.get("height")) == (640, 480), "an edit keeps the size of the image it edits",
          str((body.get("width"), body.get("height"))))
    check(body.get("loras") == "Anima/Styles/ink,Anima/Body/big" and body.get("loraweights") == "0.8,1",
          "-Lora -> loras and loraweights, names resolved against SwarmUI's list", str(body.get("loras")))
    check(body.get("removebackground") is True and body.get("seamlesstileable") == "true" and body.get("refinerupscale") == 1.5,
          "-Transparent, -Tileable, -Upscale -> removebackground, seamlesstileable, refinerupscale")
    check(str(body.get("steps")) == "12" and float(body.get("cfgscale")) == 3.0, "-Steps and -Cfg override the preset")
    check(body.get("aspectratio") == "16:9" and body.get("colordepth") == "16bit", "-Set passes any other parameter through")
    code, text = b.cli("generate", "-Prompt", "x", "-Out", b.out, "-Lora", "nolora")
    check(code == 1 and "LoRA" in text, "an unknown LoRA is refused", text)

    # --- capabilities ---------------------------------------------------------
    code, text = b.cli("capabilities")
    try:
        caps = json.loads(text[text.index("{"):])
    except ValueError:
        caps = {}
    check(code == 0 and set(caps.get("lanes", {})) >= {"image", "model3d", "music", "dataset", "merge", "mcp"},
          "capabilities is one JSON object naming every lane", text[:300])
    check(caps.get("backend", {}).get("started_by_comfy_forge") is True and caps["backend"]["swarmui"]["running"] is True,
          "capabilities reports the running backend and who started it")
    lanes = caps.get("lanes", {})
    check(lanes.get("music", {}).get("variants_ready") == ["aio"] and lanes["music"]["ready"] is True
          and lanes.get("model3d", {}).get("variants_ready") == [] and lanes["model3d"]["ready"] is False
          and "pixal3d" in (lanes["model3d"].get("variants_missing") or {}),
          "one ready variant is a list of one, none is an empty list, and missing files are named",
          str({k: lanes.get(k, {}).get("variants_ready") for k in ("music", "model3d")}))

    # --- stop -----------------------------------------------------------------
    os.makedirs(b.record, exist_ok=True)
    busy = os.path.join(b.record, "busy")
    open(busy, "w").close()
    code, text = b.cli("stop")
    check(code == 1 and "job" in text and listening(b.port) and b.backend_record() is not None,
          "stop refuses while the backend is rendering: another tool's job is not killed", text)
    os.remove(busy)
    code, text = b.cli("stop")
    check(code == 0 and wait_closed(b.port) and b.backend_record() is None, "stop ends the backend it started", text)
    code, text = b.cli("stop")
    check(code == 0 and "nothing to stop" in text, "stop with nothing running is not an error", text)

    # --- generate starts the backend itself ------------------------------------
    code, text = b.cli("generate", "-Prompt", "auto", "-Out", b.out, "-Json")
    check(code == 0 and listening(b.port) and (last_json(text) or {}).get("files"),
          "generate starts the backend when it is down, then renders", text)
    open(busy, "w").close()
    code, text = b.cli("stop", "-Force")
    os.remove(busy)
    check(code == 0 and wait_closed(b.port), "stop -Force ends a busy backend that generate started", text)

    # --- a caller that captures the output is not held by the backend -----------
    code, closed = b.start_through_pipe()
    check(code == 0 and listening(b.port) and closed,
          "start returns to a caller that captures its output: the backend does not inherit the pipe",
          f"exit={code} pipe closed={closed}")
    b.cli("stop")
    wait_closed(b.port)

    # --- a launcher that dies is reported, quickly ------------------------------
    env = dict(b.env, COMFY_FORGE_TEST_FAIL="1")
    began = time.time()
    code, text = b.cli("start", "-Wait", "60", env=env)
    check(code == 1 and "exited during startup" in text and time.time() - began < 30 and b.backend_record() is None,
          "a backend that exits at startup fails fast with its log, and leaves no record", text)

    # --- stop never ends a process it did not start -----------------------------
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        with open(os.path.join(b.state, "backend.json"), "w") as fh:
            json.dump({"kind": "SwarmUI", "pid": sleeper.pid, "started": 1, "port": b.port, "root": b.swarm, "log": ""}, fh)
        code, text = b.cli("stop")
        check(code == 0 and "nothing to stop" in text and sleeper.poll() is None,
              "a record whose pid belongs to another process is not acted on", text)
    finally:
        sleeper.kill()

    # --- standalone ComfyUI (no SwarmUI) ---------------------------------------
    comfy = os.path.join(b.root, "ComfyUI")
    os.makedirs(os.path.join(comfy, "comfy"))
    os.makedirs(os.path.join(comfy, "models"))
    open(os.path.join(comfy, "comfy", "samplers.py"), "w").close()
    shutil.copy(os.path.join(HERE, "fixtures", "backend_stub.py"), os.path.join(comfy, "main.py"))
    made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", os.path.join(comfy, "venv")],
                          capture_output=True, text=True)
    if made.returncode != 0:
        print("skip standalone ComfyUI start: could not create a venv:", made.stderr.strip()[-200:])
        return
    env = dict(b.env, COMFYUI_ROOT=comfy)
    env.pop("SWARMUI_ROOT")
    code, text = b.cli("start", env=env)
    rec = b.backend_record()
    check(code == 0 and listening(b.port) and bool(rec) and rec.get("kind") == "ComfyUI",
          "with no SwarmUI, start launches a standalone ComfyUI", text)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{b.port}/system_stats", timeout=5) as r:
            up = "system" in json.loads(r.read())
    except OSError:
        up = False
    check(up, "the standalone ComfyUI answers /system_stats")
    code, text = b.cli("stop", env=env)
    check(code == 0 and wait_closed(b.port), "stop ends the standalone ComfyUI", text)
    # That venv has no torch: status must say so, not end in a traceback.
    code, text = b.cli("status", env=env)
    check(code == 0 and "merge deps" in text and "missing" in text and "Traceback" not in text,
          "status reports a Python without torch as a missing requirement instead of crashing", text)


if __name__ == "__main__":
    sys.exit(main())
