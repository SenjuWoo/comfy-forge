"""Validate config/dependencies.lock.json without installing anything.

Checks the file's shape, then asks GitHub whether each pinned commit actually
exists in the repository the lock names. A pin that cannot be resolved is the
defect that let a ComfyUI commit masquerade as a HaDeS revision for a release.

Exit codes:
  0  PASS       - schema valid and every pin resolves
  1  FAIL       - schema invalid, or a pin does not resolve
  3  UNVERIFIED - schema valid, but the network could not be asked

Run: python TOOLS/validate-lock.py
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "config", "dependencies.lock.json")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fail(msg):
    print("FAIL " + msg)


def github_slug(url):
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_exists(slug, sha):
    """True/False, or None when the answer cannot be obtained."""
    try:
        out = subprocess.run(["gh", "api", f"repos/{slug}/commits/{sha}"],
                             capture_output=True, text=True, timeout=45)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode == 0:
        return True
    if "422" in out.stdout + out.stderr or "No commit found" in out.stdout + out.stderr:
        return False
    return None


def main():
    try:
        lock = json.load(open(LOCK, encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"cannot read {LOCK}: {e}")
        return 1

    problems, unresolved, seen = [], [], set()
    packs = lock.get("node_packs")
    if not isinstance(packs, list) or not packs:
        fail("node_packs must be a non-empty list")
        return 1

    for pack in packs:
        name = pack.get("name", "<unnamed>")
        for field in ("name", "url", "sha", "provides"):
            if field not in pack:
                problems.append(f"{name}: missing '{field}'")
        sha = str(pack.get("sha", ""))
        if not SHA_RE.match(sha):
            problems.append(f"{name}: sha '{sha}' is not a 40-character commit id")
            continue
        if name in seen:
            problems.append(f"{name}: duplicate entry")
        seen.add(name)
        slug = github_slug(str(pack.get("url", "")))
        if not slug:
            problems.append(f"{name}: url is not a github.com repository")
            continue
        exists = commit_exists(slug, sha)
        if exists is False:
            # This is exactly how the previous lock broke: the recorded SHA
            # belonged to a different repository entirely.
            problems.append(f"{name}: commit {sha[:7]} does not exist in {slug}")
        elif exists is None:
            unresolved.append(f"{name}: could not ask GitHub about {sha[:7]}")

    if problems:
        for p in problems:
            fail(p)
        print(f"LOCK: {len(problems)} problem(s)")
        return 1
    if unresolved:
        for u in unresolved:
            print("note " + u)
        print("LOCK: UNVERIFIED (network or gh unavailable)")
        return 3
    print(f"ok   {len(packs)} pins resolve in their declared repositories")
    print("LOCK: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
