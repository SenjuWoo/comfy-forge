"""Install-MusicModels.ps1 against a local stub server: reconnect, resume, size check.

No network and no real weights. The three large files of the `turbo` set are
pre-created as sparse placeholders of the right size (the installer skips files
whose size already matches), so only the 337 MB VAE is actually served.

Proves:
  * a connection stuck under -MinSpeedMB is dropped and the download resumes on a
    new connection, and the finished file has the published size
  * a file that comes back short is deleted and the run fails
  * a server error fails the run without looping through reconnects
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "TOOLS", "Install-MusicModels.ps1")
VAE_BYTES = 337431732
VAE_PATH = "/split_files/vae/ace_1.5_vae.safetensors"
PLACEHOLDERS = {
    "diffusion_models/acestep_v1.5_turbo.safetensors": 4787825604,
    "text_encoders/qwen_0.6b_ace15.safetensors": 1191588248,
    "text_encoders/qwen_1.7b_ace15.safetensors": 3708523360,
}
CHUNK = b"\x5a" * (1 << 20)
fail = 0


def check(cond, name):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail += 1


class Stub(BaseHTTPRequestHandler):
    mode = "slow-first"   # slow-first | short | error
    connections = 0
    ranges = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != VAE_PATH:
            self.send_error(404)
            return
        Stub.connections += 1
        n = Stub.connections
        if Stub.mode == "error":
            self.send_error(500)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0] or 0)
        Stub.ranges.append(start)
        total = VAE_BYTES if Stub.mode != "short" else VAE_BYTES - 1000
        length = total - start
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{total - 1}/{total}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        sent = 0
        try:
            while sent < length:
                piece = CHUNK[: min(len(CHUNK), length - sent)]
                if Stub.mode == "slow-first" and n == 1:
                    # a stuck CDN connection: a trickle, far below the 1 MB/s floor
                    piece = piece[:16384]
                    time.sleep(0.1)
                self.wfile.write(piece)
                sent += len(piece)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass


def make_root(tmp):
    root = os.path.join(tmp, "Comfy root (a!)")
    for rel, size in PLACEHOLDERS.items():
        path = os.path.join(root, "models", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
        subprocess.run(["fsutil", "sparse", "setflag", path], capture_output=True)
        with open(path, "r+b") as fh:
            fh.truncate(size)
    os.makedirs(os.path.join(root, "models", "vae"), exist_ok=True)
    return root


def run(root, url, extra=()):
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Set", "turbo",
           "-ComfyRoot", root, "-BaseUrl", url, "-MinSpeedMB", "1", "-SlowSeconds", "3", "-Reconnects", "2", *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    tmp = tempfile.mkdtemp(prefix="music-install-")
    try:
        root = make_root(tmp)
        vae = os.path.join(root, "models", "vae", "ace_1.5_vae.safetensors")

        Stub.mode, Stub.connections, Stub.ranges = "slow-first", 0, []
        r = run(root, url)
        out = r.stdout + r.stderr
        check(r.returncode == 0, f"stuck connection: install finishes (exit {r.returncode}) {out[-300:] if r.returncode else ''}")
        check(Stub.connections >= 2, f"a new connection was opened ({Stub.connections} total)")
        check(any(s > 0 for s in Stub.ranges[1:]), "the new connection resumed from the partial file, not from zero")
        check("slow connection" in out, "the script's own reconnect fired (not curl's restart-from-zero retry)")
        check(os.path.isfile(vae) and os.path.getsize(vae) == VAE_BYTES, "finished file has the published size")
        check(not os.path.exists(vae + ".part"), "no .part file left behind")
        check(out.count("have  ") == 3, "the three placeholders were recognised and left alone")

        os.remove(vae)
        Stub.mode, Stub.connections = "short", 0
        r = run(root, url, ["-MinSpeedMB", "0"])
        check(r.returncode != 0 and not os.path.exists(vae) and not os.path.exists(vae + ".part"),
              "short file: run fails and the partial file is removed")

        Stub.mode, Stub.connections = "error", 0
        r = run(root, url)
        check(r.returncode != 0 and not os.path.exists(vae), "server error: run fails")
        check(Stub.connections == 1, f"an HTTP error is final: no reconnect loop ({Stub.connections} request(s))")
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'PASS' if not fail else 'FAIL'}: {fail} failure(s)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
