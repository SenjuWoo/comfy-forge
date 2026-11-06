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
    env_port = os.environ.get("COMFYUI_PORT")
    ports = ([int(env_port)] if env_port else []) + CANDIDATE_PORTS
    for port in ports:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return port
    return None


def main():
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

    def send(mid, method, params=None):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                     "method": method,
                                     "params": params or {}}) + chr(10))
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

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
            print("UNVERIFIED: real backend operation (nothing to talk to)")
            print("MCP: PASS (handshake only)")
            return 0

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
        print("MCP: PASS (real backend operation)")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
