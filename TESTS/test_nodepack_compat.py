"""Guard: installed node packs must not call APIs ComfyUI removed.

ComfyUI 0.45 dropped the DirectML helpers from `comfy.model_management`. Any node
pack that still calls `comfy.model_management.is_directml_enabled` (or imports the
name) raises AttributeError at *sample* time, which surfaces as a failed generation
with a message that names the sampler node, not the pack at fault - RES4LYF 1.2.1
broke every KSampler this way.

This check runs against whatever packs are installed, so it catches the breakage
before a render does. It is a static scan of the installed tree, not an import:
importing ComfyUI node packs outside a running backend fails for unrelated reasons.

Exit codes:
  0  PASS  - no offending call sites (or nothing installed to check)
  1  FAIL  - an installed pack still calls a removed API

Usage:
    python TESTS/test_nodepack_compat.py [--nodes-dir <custom_nodes folder>]
"""
import argparse
import os
import re
import sys

# Direct attribute access on the module (the 1.2.1 failure) and the import form.
BAD_CALL = re.compile(r"comfy\.model_management\.is_directml_enabled")
BAD_IMPORT = re.compile(r"from\s+comfy\.model_management\s+import\s+[^\n]*is_directml_enabled")

REMOVED = ("is_directml_enabled",)


def find_nodes_dir(explicit=None):
    if explicit:
        return explicit if os.path.isdir(explicit) else None
    env = os.environ.get("COMFY_NODES_DIR")
    if env and os.path.isdir(env):
        return env
    roots = []
    for var in ("COMFYUI_ROOT", "SWARMUI_ROOT"):
        v = os.environ.get(var)
        if v:
            roots.append(v)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots.append(here)
    for root in roots:
        for cand in (os.path.join(root, "custom_nodes"),
                     os.path.join(root, "dlbackend", "comfy", "ComfyUI", "custom_nodes"),
                     os.path.join(root, "ComfyUI", "custom_nodes")):
            if os.path.isdir(cand):
                return cand
    return None


def pack_dirs(nodes_dir):
    out = []
    for name in sorted(os.listdir(nodes_dir)):
        path = os.path.join(nodes_dir, name)
        if os.path.isdir(path) and (os.path.isdir(os.path.join(path, ".git")) or name != "__pycache__"):
            out.append((name, path))
    return out


def scan_pack(name, path):
    hits = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules", ".venv")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if BAD_CALL.search(line) or BAD_IMPORT.search(line):
                            hits.append((os.path.relpath(full, path), lineno, line.strip()[:100]))
            except OSError:
                continue
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes-dir", default=None)
    args = ap.parse_args(argv)

    nodes_dir = find_nodes_dir(args.nodes_dir)
    if not nodes_dir:
        print("NODEPACK COMPAT: SKIP (no installed custom_nodes tree found; nothing to check)")
        return 0

    packs = pack_dirs(nodes_dir)
    if not packs:
        print(f"NODEPACK COMPAT: SKIP (no packs in {nodes_dir})")
        return 0

    failures = []
    for name, path in packs:
        hits = scan_pack(name, path)
        if hits:
            failures.append((name, hits))
            print(f"FAIL {name}: calls a removed ComfyUI API")
            for rel, lineno, text in hits:
                print(f"       {rel}:{lineno}: {text}")
        else:
            print(f"ok   {name}")

    print()
    if failures:
        print("NODEPACK COMPAT: FAIL - update the packs above (ComfyUI 0.45 removed "
              "comfy.model_management.is_directml_enabled)")
        return 1
    print(f"NODEPACK COMPAT: PASS ({len(packs)} pack(s) checked in {nodes_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())