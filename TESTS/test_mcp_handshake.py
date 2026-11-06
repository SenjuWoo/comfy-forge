#!/usr/bin/env python3
"""MCP integration: prove an agent can perform a real backend operation.

A handshake only proves the server starts. This also asks ComfyUI for live
system stats through the MCP tool surface, which succeeds only when the
configured target is reachable. With no backend running the real call is
reported as UNVERIFIED instead of quietly passing.

Run: python TESTS/test_mcp_handshake.py
"""
import json
import os
import socket
import subprocess
import sys

PACKAGE = "comfyui-mcp@0.52.206"
CANDIDATE_PORTS = [7821, 7822, 8188, 8000]


def detect_port():
    """A real ComfyUI endpoint, not just an open socket.

    Half-dead backends hold a listening port while never answering; probing
    /system_stats is what tells the two apart.
    """
    import urllib.request
    env_port = os.environ.get("COMFYUI_PORT")
    ports = ([int(env_port)] if env_port else []) + CANDIDATE_PORTS
    for port in ports:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=5) as r:
                if "system" in json.loads(r.read().decode()):
                    return port
        except Exception:
            continue
    return None


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    want_workflow = "--workflow" in argv
    port = detect_port()
    print(f"ok   backend detected on 127.0.0.1:{port}" if port
          else "note no ComfyUI backend is running; handshake only")

    env = dict(os.environ)
    env["COMFYUI_MCP_TOOL_MODE"] = "compact"
    cmd = ["npx.cmd" if os.name == "nt" else "npx", "-y", PACKAGE]
    if port:
        # comfyui-mcp ignores COMFYUI_HOST/COMFYUI_PORT and defaults to 8188;
        # --comfyui-url is the only target setting it actually honours.
        cmd += ["--comfyui-url", f"http://127.0.0.1:{port}"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
    assert proc.stdin is not None and proc.stdout is not None

    def send(mid, method, params=None, timeout=90):
        """Bounded read: a stalled npx/MCP start must not hang the test forever."""
        import queue
        import threading
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                     "method": method,
                                     "params": params or {}}) + chr(10))
        proc.stdin.flush()
        box = queue.Queue()

        def read():
            try:
                box.put(proc.stdout.readline())
            except Exception as e:  # pragma: no cover - process death
                box.put(e)

        t = threading.Thread(target=read, daemon=True)
        t.start()
        try:
            line = box.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"MCP server did not answer {method} within {timeout}s")
        if isinstance(line, Exception):
            raise line
        if not line:
            raise RuntimeError(f"MCP server closed the stream during {method}")
        return json.loads(line)

    try:
        init = send(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "comfy-kit", "version": "1.0"}})
        info = init["result"]["serverInfo"]
        print(f"ok   initialize -> {info.get('name')} {info.get('version')}")

        names = sorted(t["name"] for t in send(2, "tools/list")["result"]["tools"])
        print(f"ok   tools/list -> {len(names)} tools: {', '.join(names)}")
        if not {"list_tools", "describe_tool", "call_tool"} <= set(names):
            print("FAIL compact meta-tools missing")
            return 1

        catalog = send(3, "tools/call", {"name": "list_tools", "arguments": {}})
        if "tool catalog" not in catalog["result"]["content"][0]["text"]:
            print("FAIL list_tools returned no catalog")
            return 1
        print("ok   list_tools -> catalog returned")

        if not port:
            print("UNVERIFIED: no backend, real operation not performed")
            print("MCP: UNVERIFIED (handshake only)")
            return 3

        stats = send(4, "tools/call", {"name": "call_tool",
                                       "arguments": {"name": "get_system_stats",
                                                     "args": {"action": "stats"}}})
        body = stats["result"]["content"][0]["text"]
        if '"system"' not in body:
            print("FAIL real backend operation: " + body[:200])
            return 1
        system = json.loads(body)["system"]
        ram = round(system.get("ram_total", 0) / 1e9)
        print(f"ok   get_system_stats -> ComfyUI {system.get('comfyui_version')}, {ram} GB RAM")
        if not want_workflow:
            print("MCP: PASS (real backend operation)")
            return 0

        # A real submission through the MCP surface, not just a stats read.
        # The checkpoint and scheduler are named so this cannot pass on a
        # different model than the one it claims to have used.
        # Use the loader's real id ("Anima\woomix..."), not a bare filename:
        # a name the loader cannot resolve is a test bug, not a backend failure.
        import urllib.request as _ur
        with _ur.urlopen(f"http://127.0.0.1:{port}/object_info/CheckpointLoaderSimple", timeout=20) as r:
            oi = json.loads(r.read().decode())
        ckpts = oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        ckpt = next((c for c in ckpts if "woomixAnima_v175" in str(c)), None)
        if not ckpt:
            print("UNVERIFIED: the merge checkpoint is not visible to CheckpointLoaderSimple")
            return 3
        print(f"note submitting a workflow through MCP with {ckpt} ...")
        # Compact mode exposes meta-tools only; the real call goes through
        # call_tool with the tool name and its args, exactly as a client would.
        run = send(5, "tools/call", {"name": "call_tool", "arguments": {"name": "generate_image", "args": {
            "action": "image",
            "prompt": "a red fox in snow, sharp focus",
            "width": 512, "height": 512, "steps": 6,
            "checkpoint": ckpt,
            "sampler": "euler", "scheduler": "beta57",
        }}})
        text = run.get("result", {}).get("content", [{}])[0].get("text", "")
        if run.get("error") or not text:
            print("FAIL MCP workflow submission: " + json.dumps(run)[:300])
            return 1
        try:
            payload = json.loads(text)
        except ValueError:
            print("FAIL MCP workflow returned non-JSON: " + text[:300])
            return 1
        pid = payload.get("prompt_id")
        if payload.get("status") != "enqueued" or not pid:
            print("FAIL MCP workflow was not enqueued: " + text[:300])
            return 1
        print(f"ok   generate_image -> enqueued {pid}")

        # Enqueued is not rendered. Wait for the backend to finish the job and
        # confirm an image actually exists in its history.
        import time
        import urllib.request
        deadline = time.time() + 300
        image = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/history/{pid}", timeout=20) as r:
                    hist = json.loads(r.read().decode())
            except Exception:
                hist = {}
            if pid in hist:
                outs = hist[pid].get("outputs", {})
                image = next((i for o in outs.values() for i in o.get("images", [])), None)
                break
            time.sleep(2)
        if not image:
            print("FAIL the MCP-submitted job produced no image within 300s")
            return 1
        print(f"ok   rendered      {image['filename']}")
        print("MCP: PASS (workflow executed)")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
