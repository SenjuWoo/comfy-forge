"""Add or replace one MCP server entry in an agent's JSON config, and nothing else.

    python TOOLS/mcp_config.py --target ~/.claude.json --name comfyui --server-file server.json

`image-stack.ps1 mcp -Write` calls this. The config is the user's file and holds
much more than one server, so the edit is done here rather than through
PowerShell 5.1's JSON cmdlets, which read UTF-8 as ANSI, flatten anything nested
past their depth limit into a string and write a BOM.

What this guarantees:
  * every other key and value comes back as it was, in the same order;
  * the previous file is kept beside it as <name>.bak-<timestamp> before any change;
  * the new file is written to a temporary name and renamed, so a crash cannot
    leave half a config;
  * a file that does not parse as a JSON object is refused and left untouched;
  * an entry that is already identical changes nothing.

Exit codes: 0 written or unchanged, 1 refused or failed.
"""
import argparse
import json
import os
import shutil
import sys
import time
import uuid


def fail(message):
    print("error  " + message, file=sys.stderr)
    return 1


def backup_path(target):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = "%s.bak-%s" % (target, stamp)
    n = 1
    while os.path.exists(candidate):
        candidate = "%s.bak-%s-%d" % (target, stamp, n)
        n += 1
    return candidate


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set one MCP server entry in a JSON agent config.")
    ap.add_argument("--target", required=True, help="the agent's JSON config file")
    ap.add_argument("--name", default="comfyui", help="key under mcpServers (default comfyui)")
    ap.add_argument("--server-file", required=True, help="JSON file holding the server entry")
    args = ap.parse_args(argv)

    try:
        with open(args.server_file, encoding="utf-8-sig") as fh:
            server = json.load(fh)
    except (OSError, ValueError) as exc:
        return fail("cannot read the server entry: %s" % exc)
    if not isinstance(server, dict):
        return fail("the server entry must be a JSON object")

    target = os.path.abspath(os.path.expanduser(args.target))
    had_bom = False
    exists = os.path.isfile(target)
    if exists:
        try:
            with open(target, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            return fail("cannot read %s: %s" % (target, exc))
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            config = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            return fail("%s is not valid UTF-8 JSON (%s); it was left untouched" % (target, exc))
        if not isinstance(config, dict):
            return fail("%s does not hold a JSON object; it was left untouched" % target)
    elif os.path.exists(target):
        return fail("%s is not a file" % target)
    else:
        config = {}

    servers = config.get("mcpServers")
    if servers is None:
        servers = config["mcpServers"] = {}
    if not isinstance(servers, dict):
        return fail("mcpServers in %s is not an object; it was left untouched" % target)
    if exists and servers.get(args.name) == server:
        print("unchanged  %s already has this %s entry" % (target, args.name))
        return 0
    servers[args.name] = server

    folder = os.path.dirname(target)
    tmp = "%s.tmp-%s" % (target, uuid.uuid4().hex[:8])
    try:
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8-sig" if had_bom else "utf-8", newline="\n") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        # Prove the new file before it replaces anything.
        with open(tmp, encoding="utf-8-sig") as fh:
            if json.load(fh) != config:
                raise ValueError("the written file does not read back as the same config")
        if exists:
            kept = backup_path(target)
            shutil.copy2(target, kept)
            print("backup     %s" % kept)
        os.replace(tmp, target)
    except (OSError, ValueError) as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return fail("could not write %s: %s" % (target, exc))
    print("%s    %s" % ("wrote  " if exists else "created", target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
