"""Weight installers: root discovery must never crash, and must find the stack.

Install-3DModels.ps1 and Install-MusicModels.ps1 probed `D:\\ComfyUI` with
Join-Path, which throws "Cannot find drive" on a machine without a D: drive, so
`-WhatIf` died before printing anything. CI runners have a D: drive, so the
missing drive is simulated here with a drive letter that does not exist.
Install-3DModels.ps1 also ignored the install record that names the SwarmUI.

Nothing is downloaded: every run is -WhatIf. Windows only.
Run: python TESTS/test_installers.py
"""
import json
import os
import string
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLERS = ("Install-3DModels.ps1", "Install-MusicModels.ps1")
fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name)
        fail += 1


def missing_drive():
    used = {d[0].upper() for d in os.listdrives()} if hasattr(os, "listdrives") else set()
    for letter in reversed(string.ascii_uppercase[3:]):
        if letter not in used and not os.path.exists(letter + ":\\"):
            return letter + ":"
    return None


def run(script, local_appdata, extra_env=None, args=("-WhatIf",)):
    env = {k: v for k, v in os.environ.items() if k not in ("SWARMUI_ROOT", "COMFYUI_ROOT")}
    env["LOCALAPPDATA"] = local_appdata
    env.update(extra_env or {})
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(REPO, "TOOLS", script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    return r.returncode, r.stdout + r.stderr


def main():
    if os.name != "nt":
        print("INSTALLERS: SKIP (Windows only)")
        return 0
    drive = missing_drive()
    with tempfile.TemporaryDirectory(prefix="installers ") as tmp:
        empty = os.path.join(tmp, "empty appdata")
        os.makedirs(empty)
        # 1. An install root on a drive that does not exist is "not found", not a crash.
        if drive:
            for script in INSTALLERS:
                code, out = run(script, empty, {"COMFYUI_ROOT": drive + "\\ComfyUI"})
                check("Cannot find drive" not in out and "DriveNotFound" not in out,
                      "%s: a root on a missing drive (%s) does not crash" % (script, drive))
                check(code == 1 and "Could not locate" in out, "%s: no root found exits 1 and says so" % script)
                code, out = run(script, empty, args=("-WhatIf", "-ComfyRoot", drive + "\\ComfyUI"))
                check("Cannot find drive" not in out and code == 1,
                      "%s: -ComfyRoot on a missing drive is refused cleanly" % script)
        else:
            print("skip missing-drive case: every drive letter is in use")

        # 2. The install record names the SwarmUI; its backend's models folder is used.
        swarm = os.path.join(tmp, "Swarm UI (test)")
        models = os.path.join(swarm, "dlbackend", "comfy", "ComfyUI", "models")
        os.makedirs(models)
        record = os.path.join(tmp, "appdata", "comfy-kit")
        os.makedirs(record)
        with open(os.path.join(record, "anima-swarmui.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "frontends": [{"kind": "SwarmUI", "root": swarm}]}, fh)
        for script in INSTALLERS:
            code, out = run(script, os.path.join(tmp, "appdata"))
            check(code == 0 and os.path.dirname(models) in out,
                  "%s: the SwarmUI named by the install record is found (%s)" % (script, out.strip()[-160:]))
            check("would get" in out and "nothing was downloaded" in out, "%s: -WhatIf lists files and fetches none" % script)
        check(not any(files for _r, _d, files in os.walk(models)), "no file was written into the models folder")

    print("INSTALLERS: %s" % ("PASS" if not fail else "%d FAILED" % fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
