"""Resolve a ComfyUI node pack's declared dependencies.

HaDeS declares its requirements in `pyproject.toml` (PEP 621) and RES4LYF in
`requirements.txt`; reading only one of them silently under-installs. This module
reads both, keeps version constraints intact, and reports each requirement as
satisfied, missing, or unsupported instead of discarding what it cannot parse.

Output is JSON on stdout so the PowerShell side never has to parse prose:
    {"pack": ..., "requirements": [...], "missing": [...], "unsupported": [...]}

Usage:
    python check_deps.py --pack <custom_nodes dir>/<pack> [--pack ...]
"""
import argparse
import importlib.metadata as md
import json
import os
import re
import sys

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    tomllib = None


def read_requirements_txt(path):
    out, unsupported = [], []
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Editable installs, VCS URLs, index directives and direct file refs
        # cannot be replayed as plain specifiers. They are reported, not dropped.
        if line.startswith("-") or "://" in line or line.startswith("git+"):
            unsupported.append(line)
            continue
        out.append(line)
    return out, unsupported


def read_pyproject(path):
    if tomllib is None:
        return [], ["pyproject.toml present but this Python has no tomllib"]
    data = tomllib.loads(open(path, encoding="utf-8").read())
    project = data.get("project") or {}
    # Only core dependencies are mandatory. Extras are opt-in features; pulling
    # them all in would install packages the user never asked for.
    return list(project.get("dependencies") or []), []


def parse_manifest(pack_dir):
    reqs, unsupported, seen = [], [], set()
    for name, reader in (("requirements.txt", read_requirements_txt),
                         ("pyproject.toml", read_pyproject)):
        path = os.path.join(pack_dir, name)
        if not os.path.exists(path):
            continue
        got, bad = reader(path)
        unsupported.extend(bad)
        for r in got:
            key = r.lower()
            if key not in seen:
                seen.add(key)
                reqs.append(r)
    return reqs, unsupported


def packaging_available():
    try:
        import packaging.requirements  # noqa: F401
        return True
    except ImportError:
        return False


def satisfied(spec):
    """True/False when the specifier is interpretable, None when it is not."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return None
    try:
        req = Requirement(spec)
    except Exception:
        return None
    try:
        if req.marker is not None and not req.marker.evaluate():
            return True  # does not apply to this platform/environment
    except Exception:
        pass
    try:
        dist = md.distribution(req.name)
    except md.PackageNotFoundError:
        return False
    if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
        return False
    return True


def self_test():
    """Prove the resolver reads both manifest types and keeps constraints.

    Offline: it parses only, it never installs. The fixtures include a local path
    requirement that cannot be replayed as a specifier, which must be reported
    rather than silently dropped.
    """
    import tempfile
    ok = True

    def write(path, lines):
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    tmp = tempfile.mkdtemp(prefix="deps-selftest-")
    pack_a = os.path.join(tmp, "pack-a")
    os.makedirs(pack_a)
    write(os.path.join(pack_a, "requirements.txt"),
          ["# comment", "numpy>=1.26.4", "-e ./vendor"])
    pack_b = os.path.join(tmp, "pack-b")
    os.makedirs(pack_b)
    write(os.path.join(pack_b, "pyproject.toml"),
          ["[project]", 'name = "pack-b"',
           'dependencies = ["safetensors>=0.4", "scipy"]'])

    reqs_a, bad_a = parse_manifest(pack_a)
    if "numpy>=1.26.4" not in reqs_a:
        print("FAIL requirements.txt lost its version constraint: %s" % reqs_a)
        ok = False
    if not any("-e" in b for b in bad_a):
        print("FAIL an editable directive was not reported as unsupported")
        ok = False

    reqs_b, bad_b = parse_manifest(pack_b)
    if "safetensors>=0.4" not in reqs_b:
        print("FAIL pyproject.toml dependencies were not read: %s" % reqs_b)
        ok = False
    if bad_b:
        print("FAIL pyproject.toml produced spurious unsupported entries: %s" % bad_b)
        ok = False

    if ok:
        print("ok   requirements.txt keeps version constraints")
        print("ok   pyproject.toml dependencies are read")
        print("ok   unsupported directives are reported, not dropped")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", action="append")
    ap.add_argument("--self-test", action="store_true")
    # Callers that act on the JSON (the installer installs what is missing) need
    # a zero exit; automation that only wants a verdict gets a nonzero one.
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.pack:
        ap.error("--pack is required unless --self-test is given")
    report = []
    for pack in args.pack:
        name = os.path.basename(os.path.normpath(pack))
        if not os.path.isdir(pack):
            report.append({"pack": name, "present": False,
                           "requirements": [], "missing": [], "unsupported": []})
            continue
        reqs, unsupported = parse_manifest(pack)
        entry = {"pack": name, "present": True, "requirements": reqs,
                 "missing": [], "unsupported": unsupported, "status": "ok",
                 "error": None}
        if reqs and not packaging_available():
            # Without packaging, a version constraint cannot be evaluated. That
            # is a failed validation, not a pass with unknowns.
            entry["status"] = "failed"
            entry["error"] = ("the 'packaging' library is missing from this "
                              "interpreter, so constraints cannot be checked")
            report.append(entry)
            continue
        missing = [r for r in reqs if satisfied(r) is False]
        unknown = [r for r in reqs if satisfied(r) is None]
        if unknown:
            entry["status"] = "failed"
            entry["error"] = "unparseable requirement(s): " + ", ".join(unknown)
        entry["missing"] = missing
        report.append(entry)
    print(json.dumps(report, indent=2))
    if args.report_only:
        return 0
    # Exit status contract: 0 = every mandatory requirement verified, 2 = at
    # least one pack has a missing or failed requirement. Unsupported directives
    # are resolved by the caller, so they do not by themselves change the code.
    if any(p.get("status") == "failed" or p.get("missing") for p in report):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
