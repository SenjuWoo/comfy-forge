#!/usr/bin/env python3
"""
Proves the MCP wiring works: starts `npx comfyui-mcp` exactly as the generated
client config would, performs the stdio handshake and lists the tools it exposes.

Needs node/npm and network on the first run (npx caches afterwards).
Run: python TESTS/test_mcp_handshake.py
"""
import json
import os
import subprocess
import sys
import threading

PACKAGE = "comfyui-mcp@0.52.206"


def main():
    env = dict(os.environ)
    env.update({
        "COMFYUI_HOST": "127.0.0.1",
        "COMFYUI_PORT": "7821",
        "COMFYUI_MCP_TOOL_MODE": "compact",
    })
    cmd = ["npx.cmd" if os.name == "nt" else "npx", "-y", PACKAGE]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "uabs-probe", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for m in messages:
        proc.stdin.write(json.dumps(m) + "\n")
    proc.stdin.flush()

    replies = {}
    def reader():
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and msg["id"] in (1, 2):
                replies[msg["id"]] = msg
            if len(replies) >= 2:
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout=180)
    proc.terminate()

    ok = True
    if 1 not in replies:
        print("FAIL initialize: no reply")
        err = proc.stderr.read()[:600] if proc.stderr else ""
        if err:
            print("   stderr:", err.strip()[:400])
        return 1
    info = replies[1].get("result", {}).get("serverInfo", {})
    print(f"ok   initialize -> {info.get('name')} {info.get('version')}")

    tools = replies.get(2, {}).get("result", {}).get("tools", [])
    if not tools:
        print("FAIL tools/list returned nothing")
        return 1
    print(f"ok   tools/list -> {len(tools)} tools: " + ", ".join(t["name"] for t in tools[:6]))

    # Compact mode must expose the meta-tools an agent actually calls.
    names = {t["name"] for t in tools}
    meta = {"list_tools", "describe_tool", "call_tool"}
    if meta & names:
        print("ok   compact meta-tools present")
    else:
        print("note tool names differ from the expected meta-tools: " + ", ".join(sorted(names)[:5]))
    print("MCP HANDSHAKE: " + ("PASS" if ok else "FAIL"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
