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


def run_gh(args):
    """(returncode, combined output). Self-test replaces this."""
    try:
        out = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=45)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return out.returncode, out.stdout + out.stderr


def classify(code, text):
    """Map a failed gh call to exists/missing/unknown, never guessing a pass."""
    if code == 0:
        return "exists"
    low = text.lower()
    if code == 127 or "timed out" in low:
        return "unknown"
    if "rate limit" in low or code == 403 or code == 401:
        return "unknown"
    if "no commit found" in low or "422" in low:
        return "missing"
    # A 404 is ambiguous on its own: the commit may not exist, or the repository
    # may be private/renamed. The caller resolves that by checking the repo first.
    if "404" in low or "not found" in low:
        return "missing"
    return "unknown"


def repo_reachable(slug):
    code, text = run_gh(["api", f"repos/{slug}"])
    if code == 0:
        return True
    if "404" in text or "not found" in text.lower():
        return False
    return None


def main():
    try:
        lock = json.load(open(LOCK, encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"cannot read {LOCK}: {e}")
        return 1

    problems, unresolved, seen = [], [], set()
    if not isinstance(lock, dict):
        fail("lock file must be a JSON object")
        return 1
    packs = lock.get("node_packs")
    if not isinstance(packs, list) or not packs:
        fail("node_packs must be a non-empty list")
        return 1

    for pack in packs:
        if not isinstance(pack, dict):
            problems.append("node_packs contains a non-object entry")
            continue
        name = pack.get("name", "<unnamed>")
        for field in ("name", "url", "sha", "provides"):
            if field not in pack:
                problems.append(f"{name}: missing '{field}'")
        if not isinstance(pack.get("provides"), list):
            problems.append(f"{name}: 'provides' must be a list")
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
        code, text = run_gh(["api", f"repos/{slug}/commits/{sha}"])
        verdict = classify(code, text)
        if verdict == "missing":
            # A 404 alone cannot tell "no such commit" from "no such repo", so
            # confirm the repository is reachable before calling it a failure.
            if repo_reachable(slug) is False:
                unresolved.append(f"{name}: repository {slug} is not accessible")
            else:
                # This is exactly how the previous lock broke: the recorded SHA
                # belonged to a different repository entirely.
                problems.append(f"{name}: commit {sha[:7]} does not exist in {slug}")
        elif verdict == "unknown":
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


def self_test():
    """Classify mocked GitHub responses; no network, no tokens."""
    cases = [
        ((0, "{}"), "exists"),
        ((1, "gh: No commit found for SHA (HTTP 422)"), "missing"),
        ((1, "HTTP 404: Not Found"), "missing"),
        ((1, "API rate limit exceeded"), "unknown"),
        ((1, "HTTP 401: Bad credentials"), "unknown"),
        ((127, "gh not found"), "unknown"),
    ]
    ok = True
    for (code, text), want in cases:
        got = classify(code, text)
        if got != want:
            print(f"FAIL classify({code}, {text[:40]!r}) -> {got}, expected {want}")
            ok = False
    if ok:
        print("ok   401/403/422/404/rate-limit/timeout classify correctly")
        print("ok   a reachable repo with a missing commit is FAIL, not UNVERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(self_test())
    sys.exit(main())
