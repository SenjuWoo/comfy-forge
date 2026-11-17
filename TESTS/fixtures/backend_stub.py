"""A stand-in for SwarmUI and its ComfyUI backend, for tests that need no GPU.

One process answers both APIs on one port:

  SwarmUI   POST /API/GetNewSession, GetCurrentStatus, ListModels,
            GenerateText2Image, TriggerRefresh, ShutdownServer; GET /View/...
  ComfyUI   GET /system_stats, /object_info, /history/<id>, /view, /queue;
            POST /upload/image, /prompt

Every request body is appended to <record>/requests.jsonl so a test can read what
the tool under test really sent. It also plays SwarmUI's launcher and ComfyUI's
main.py: unknown arguments (--launch_mode none, --listen, --disable-auto-launch)
are accepted and ignored.

    python backend_stub.py --port 7999 --record DIR [--loading-seconds 1.5]

Environment (used when it is started through a fake launch-windows.bat):
  COMFY_FORGE_TEST_PORT, COMFY_FORGE_TEST_RECORD, COMFY_FORGE_TEST_FAIL=1 (exit 3 at once)
"""
import argparse
import json
import os
import struct
import sys
import threading
import time
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHECKPOINTS = [
    {"name": "Anima/Anime/3.8B/Deep.safetensors", "architecture": "anima", "compat_class": "anima"},
    {"name": "Anima/Anime/woomix.safetensors", "architecture": "anima", "compat_class": "anima"},
    {"name": "SDXL/dup.safetensors", "architecture": "stable-diffusion-xl-v1-base", "compat_class": "stable-diffusion-xl-v1"},
    {"name": "SDXL/alt/dup.safetensors", "architecture": "stable-diffusion-xl-v1-base", "compat_class": "stable-diffusion-xl-v1"},
]
LORAS = [{"name": "Anima/Styles/ink.safetensors"}, {"name": "Anima/Body/big.safetensors"}]


def png(width=1, height=1):
    """A valid opaque grey PNG of the given size."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def glb(triangles=12):
    """A structurally valid .glb whose JSON chunk declares `triangles` triangles."""
    doc = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"count": triangles * 3, "type": "VEC3", "componentType": 5126},
                         {"count": triangles * 3, "type": "SCALAR", "componentType": 5125}],
           "materials": [{"name": "m"}], "images": [{}, {}, {}]}
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    pad = b"\x00" * 20000  # big enough that the tool does not call it suspiciously small
    return (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(body) + 8 + len(pad))
            + struct.pack("<I", len(body)) + b"JSON" + body + struct.pack("<I", len(pad)) + b"BIN\x00" + pad)


class State:
    def __init__(self, record, loading_seconds):
        self.record = record
        self.ready_at = time.time() + loading_seconds
        self.lock = threading.Lock()
        self.issued = set()      # SwarmUI image paths handed out
        self.uploads = set()     # names LoadImage may open
        self.prompts = {}        # prompt id -> graph
        self.count = 0
        self.missing = set(json.loads(os.environ.get("COMFY_FORGE_TEST_MISSING", "[]")))

    def log(self, route, body):
        os.makedirs(self.record, exist_ok=True)
        with self.lock, open(os.path.join(self.record, "requests.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"route": route, "body": body}, ensure_ascii=False) + "\n")


def object_info(state):
    """Node definitions for every class a recorded template uses, built from the shipped templates."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(here, "TOOLS"))
    import model3d
    info = {}
    for variant in model3d.VARIANTS:
        graph = model3d.build_graph(variant, image="x.png", sheet_size=(1024, 1024))
        for node in graph.values():
            entry = info.setdefault(node["class_type"], {"input": {"required": {}, "optional": {}}})
            for key, value in node["inputs"].items():
                entry["input"]["required"].setdefault(key.split(".")[0], ["*", {}])
        for folder, name, cls, field in model3d.required_files(graph):
            spec = info[cls]["input"]["required"][field]
            if not isinstance(spec[0], list):
                spec = info[cls]["input"]["required"][field] = [[], {}]
            if name not in spec[0] and name not in state.missing:
                spec[0].append(name)
    return info


class Handler(BaseHTTPRequestHandler):
    state = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, payload, code=200, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        st = self.state
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        if path == "/system_stats":
            return self.reply({"system": {"comfyui_version": "stub"}})
        if path == "/object_info":
            return self.reply(object_info(st))
        if path == "/queue":
            return self.reply({"queue_running": [], "queue_pending": []})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            graph = st.prompts.get(pid)
            if graph is None:
                return self.reply({})
            outputs = {}
            for nid, node in graph.items():
                prefix = str(node["inputs"].get("filename_prefix", ""))
                sub, _, stem = prefix.rpartition("/")
                if node["class_type"] == "Save3DAdvanced":
                    outputs[nid] = {"3d": [{"filename": f"{stem}_00001_.glb", "subfolder": sub, "type": "output"}]}
                elif node["class_type"] == "SaveGLB":
                    ext = "spz" if graph.get(node["inputs"]["mesh"][0], {}).get("class_type") == "SplatToFile3D" else "glb"
                    outputs[nid] = {"3d": [{"filename": f"{stem}_00001_.{ext}", "subfolder": sub, "type": "output"}]}
                elif node["class_type"] == "PreviewImage":
                    outputs[nid] = {"images": [{"filename": f"preview_{nid}.png", "subfolder": "", "type": "temp"}]}
            return self.reply({pid: {"status": {"status_str": "success", "completed": True}, "outputs": outputs}})
        if path == "/view":
            name = query.get("filename", [""])[0]
            return self.reply(glb() if name.endswith(".glb") else b"splat-bytes" * 4000 if name.endswith(".spz") else png(),
                              content_type="application/octet-stream")
        if path.startswith("/View/"):
            if path[1:] not in st.issued:
                return self.reply({"error": "no such image"}, 404)
            return self.reply(png(), content_type="image/png")
        return self.reply({"error": "unknown route"}, 404)

    def do_POST(self):
        st = self.state
        path = urllib.parse.urlsplit(self.path).path
        raw = self.body()
        if path == "/upload/image":
            marker = b'filename="'
            start = raw.index(marker) + len(marker)
            name = raw[start:raw.index(b'"', start)].decode("utf-8")
            st.uploads.add(name)
            st.log("upload/image", {"name": name, "bytes": len(raw)})
            return self.reply({"name": name, "subfolder": "", "type": "input"})
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return self.reply({"error": "body is not UTF-8 JSON"}, 400)
        route = path.rsplit("/", 1)[1]
        st.log(route, body)
        if path == "/prompt":
            graph = body.get("prompt") or {}
            errors = {nid: f"image {node['inputs'].get('image')} was never uploaded" for nid, node in graph.items()
                      if node["class_type"] == "LoadImage" and node["inputs"].get("image") not in st.uploads}
            if errors:
                return self.reply({"error": "validation", "node_errors": errors}, 400)
            st.count += 1
            pid = f"stub-{st.count}"
            st.prompts[pid] = graph
            return self.reply({"prompt_id": pid})
        if not path.startswith("/API/"):
            return self.reply({"error": "unknown route"}, 404)
        if route == "GetNewSession":
            return self.reply({"session_id": "stub-session"})
        if route == "GetCurrentStatus":
            # A file named `busy` in the record folder stands for a render in flight.
            busy = 1 if os.path.exists(os.path.join(st.record, "busy")) else 0
            return self.reply({"status": {"waiting_gens": 0, "live_gens": busy},
                               "backend_status": {"status": "running" if time.time() >= st.ready_at else "loading"}})
        if route == "ListModels":
            return self.reply({"folders": [], "files": LORAS if body.get("subtype") == "LoRA" else CHECKPOINTS})
        if route == "TriggerRefresh":
            return self.reply({})
        if route == "ShutdownServer":
            self.reply({"success": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return None
        if route == "GenerateText2Image":
            if time.time() < st.ready_at:
                return self.reply({"error": "No backends available!"})
            st.count += 1
            # SwarmUI names an image after its prompt; keep that so a non-ASCII prompt
            # makes a non-ASCII path, as it does for real.
            word = "".join(ch for ch in str(body.get("prompt", ""))[:24] if ch.isalnum()) or "image"
            images = [f"View/local/raw/2026-01-01/{st.count:04d}-{i}-{word}.png" for i in range(int(body.get("images", 1)))]
            st.issued.update(images)
            return self.reply({"images": images})
        return self.reply({"error": f"unknown API route {route}"}, 404)


def main():
    if os.environ.get("COMFY_FORGE_TEST_FAIL"):
        print("stub: told to fail at startup")
        return 3
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.environ.get("COMFY_FORGE_TEST_PORT") or 0))
    p.add_argument("--record", default=os.environ.get("COMFY_FORGE_TEST_RECORD") or ".")
    p.add_argument("--loading-seconds", type=float, default=float(os.environ.get("COMFY_FORGE_TEST_LOADING") or 0))
    args, _ignored = p.parse_known_args()
    Handler.state = State(args.record, args.loading_seconds)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Starting webserver on http://localhost:{server.server_address[1]}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
