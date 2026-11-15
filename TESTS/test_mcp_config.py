"""`image-stack.ps1 mcp -Write` must never damage the agent config it edits.

The config belongs to the user and holds far more than one server entry. The
old PowerShell round trip read UTF-8 as ANSI, cut anything nested deeper than 12
levels down to a string, added a BOM and kept no backup. These checks drive
TOOLS/mcp_config.py as a real process, the way the CLI does.

Run: python TESTS/test_mcp_config.py
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "TOOLS", "mcp_config.py")
SERVER = {"command": "npx", "args": ["-y", "comfyui-mcp@0.52.206", "--comfyui-url", "http://127.0.0.1:8188"],
          "env": {"COMFYUI_MCP_TOOL_MODE": "compact"}}
fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name)
        fail += 1


def run(target, server=SERVER, name="comfyui"):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(server, fh)
        server_file = fh.name
    try:
        return subprocess.run([sys.executable, TOOL, "--target", target, "--name", name,
                               "--server-file", server_file], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(server_file)


def deep(levels=40):
    node = {"leaf": "deep value"}
    for i in range(levels):
        node = {"level%d" % i: node}
    return node


def backups(target):
    return sorted(glob.glob(target + ".bak-*"))


def main():
    with tempfile.TemporaryDirectory(prefix="mcp config ") as tmp:
        # 1. An existing config keeps everything it had.
        target = os.path.join(tmp, "agent é.json")
        original = {"name": "café – Zoë", "tree": deep(), "ratio": 0.1, "big": 12345678901234567890,
                    "empty_list": [], "empty_object": {}, "one": ["only"], "when": "2026-11-06T14:12:46Z",
                    "mcpServers": {"other": {"command": "x", "args": []}}}
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(original, fh, ensure_ascii=False)
        before = open(target, "rb").read()
        r = run(target)
        check(r.returncode == 0, "writing into an existing config exits 0 (%s)" % (r.stdout + r.stderr)[-200:])
        raw = open(target, "rb").read()
        after = json.loads(raw.decode("utf-8"))
        check(not raw.startswith(b"\xef\xbb\xbf"), "no BOM is added to a file that had none")
        check(after["mcpServers"]["comfyui"] == SERVER, "the comfyui server is written")
        check(after["mcpServers"]["other"] == original["mcpServers"]["other"], "other servers are kept")
        expected = dict(original, mcpServers=after["mcpServers"])
        check(after == expected, "every other value survives: text, 40 levels of nesting, numbers, empties, dates")
        check(list(after)[:3] == list(original)[:3], "key order is kept")
        kept = backups(target)
        check(len(kept) == 1 and open(kept[0], "rb").read() == before, "the previous file is kept byte for byte as a backup")

        # 2. Writing the same entry again changes nothing and makes no second backup.
        stamp = open(target, "rb").read()
        r = run(target)
        check(r.returncode == 0 and "unchanged" in r.stdout, "an identical entry is reported as unchanged")
        check(open(target, "rb").read() == stamp and len(backups(target)) == 1, "an unchanged config is not rewritten")

        # 3. A changed entry replaces only that entry.
        moved = dict(SERVER, args=SERVER["args"][:-1] + ["http://127.0.0.1:7821"])
        r = run(target, moved)
        after = json.loads(open(target, encoding="utf-8").read())
        check(r.returncode == 0 and after["mcpServers"]["comfyui"] == moved and "other" in after["mcpServers"],
              "a new port replaces only the comfyui entry")
        check(len(backups(target)) == 2, "each real change keeps its own backup")

        # 4. A BOM that was there stays there.
        bom = os.path.join(tmp, "bom.json")
        with open(bom, "w", encoding="utf-8-sig") as fh:
            json.dump({"keep": "ü"}, fh, ensure_ascii=False)
        r = run(bom)
        raw = open(bom, "rb").read()
        check(r.returncode == 0 and raw.startswith(b"\xef\xbb\xbf") and json.loads(raw.decode("utf-8-sig"))["keep"] == "ü",
              "a file that had a BOM keeps it, and its text")

        # 5. Anything that is not a JSON object is refused and left exactly as it was.
        for label, body in (("invalid JSON", b'{"a": 1,,}'), ("a JSON list", b"[1, 2]"),
                            ("mcpServers that is not an object", b'{"mcpServers": [1]}'),
                            ("bytes that are not UTF-8", b'{"a": "\xff\xfe"}')):
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "wb") as fh:
                fh.write(body)
            r = run(bad)
            check(r.returncode == 1 and open(bad, "rb").read() == body and not backups(bad),
                  "%s is refused and left untouched" % label)

        # 6. A missing file is created, with its folder.
        fresh = os.path.join(tmp, "new folder", "config.json")
        r = run(fresh)
        check(r.returncode == 0 and json.loads(open(fresh, encoding="utf-8").read()) == {"mcpServers": {"comfyui": SERVER}},
              "a missing config is created with only the server")
        check(not backups(fresh), "a new file has nothing to back up")
        check(not [n for n in os.listdir(os.path.dirname(fresh)) if n != "config.json"], "no temporary file is left behind")

    print("MCP CONFIG: %s" % ("PASS" if not fail else "%d FAILED" % fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
