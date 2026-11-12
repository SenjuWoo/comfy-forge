"""Failure injection for the merge test's artifact handling.

The live test writes a multi-gigabyte checkpoint. These cases prove that a run
which fails after that point removes exactly what it created: a file another
writer won is never deleted, a partial this run created is, and backend-reported
paths are only trusted inside a root this process derived itself.
"""
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from integration_merge_graph import (  # noqa: E402
    _copy_exclusive, claim_saved, cleanup, expose, preview_path, resolve_output_root)

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok   " + name)
    else:
        print("FAIL " + name)
        fail += 1


tmp = tempfile.mkdtemp(prefix="artifacts-")
# A ComfyUI-shaped tree: <root>/output and <root>/models must both exist.
root = os.path.join(tmp, "ComfyUI")
outdir = os.path.join(root, "output")
models = os.path.join(root, "models", "diffusion_models")
os.makedirs(outdir, exist_ok=True)
os.makedirs(models, exist_ok=True)

src = os.path.join(outdir, "saved.safetensors")
open(src, "wb").write(b"x" * 4096)

# --- hard link on one volume ------------------------------------------------
visible, how = expose(src, models, "run-a.safetensors", same_volume=True)
check(os.path.exists(visible), "a same-volume expose creates the file")
check(how == "hard link", "a same-volume expose links instead of copying")
check(os.stat(visible).st_ino == os.stat(src).st_ino, "the link shares the source bytes")
check(os.path.getsize(src) == 4096, "the source is untouched")

# --- refusal of an existing model -------------------------------------------
blocked = os.path.join(models, "run-blocked.safetensors")
open(blocked, "wb").write(b"user model")
before = open(blocked, "rb").read()
try:
    expose(src, models, "run-blocked.safetensors", same_volume=True)
    check(False, "an existing model is refused")
except FileExistsError:
    check(True, "an existing model is refused")
check(open(blocked, "rb").read() == before, "the existing model is unchanged")

# --- copy branch, successful ------------------------------------------------
STAMP = 1_600_000_000
os.utime(src, (STAMP, STAMP))
src_bytes = open(src, "rb").read()
v2, how2 = expose(src, models, "run-b.safetensors", same_volume=False)
check(os.path.exists(v2) and how2 == "copy", "a cross-volume expose copies")
check(open(v2, "rb").read() == src_bytes, "the copy is byte-for-byte identical")
check(os.path.getsize(v2) == os.path.getsize(src), "the copy has the source's size")
check(int(os.stat(v2).st_mtime) == STAMP, "the copy keeps the source's modification time")
if os.name != "nt":
    check(stat.S_IMODE(os.stat(v2).st_mode) == stat.S_IMODE(os.stat(src).st_mode),
          "the copy keeps the source's permissions")

# --- copy dies after the exclusive open: our partial is removed -------------
seen = {}
partial_path = os.path.join(models, "run-c.safetensors")
real_unlink = os.unlink


def spy_unlink(path):
    # Watch the deletion instead of the aftermath: the partial is removed by
    # _copy_exclusive itself, so the only moment it is observable is at unlink.
    if os.path.abspath(path) == os.path.abspath(partial_path) and os.path.exists(path):
        seen["existed"] = True
        seen["size"] = os.path.getsize(path)
    return real_unlink(path)


def injected_failure(a, b):
    os.unlink = spy_unlink
    try:
        _copy_exclusive(a, b, fail_after=1000)
    except OSError as e:
        seen["message"] = str(e)
        raise
    finally:
        os.unlink = real_unlink


try:
    expose(src, models, "run-c.safetensors", same_volume=False, writer=injected_failure)
    check(False, "a copy that dies midway raises")
except OSError:
    check("injected copy failure" in seen.get("message", ""), "a copy that dies midway raises")
check(seen.get("existed") is True, "the partial destination really existed before cleanup")
check(seen.get("size", 0) > 0, "the partial destination held bytes before cleanup")
check(int(seen["message"].split("after ")[1].split(" bytes")[0]) >= 1000,
      "the failure fired after the injected byte count")
check(not os.path.exists(os.path.join(models, "run-c.safetensors")),
      "a copy that dies midway leaves no partial behind")
check(open(src, "rb").read() == src_bytes, "a failed copy leaves the source unchanged")
check(int(os.stat(src).st_mtime) == STAMP, "a failed copy leaves the source's mtime unchanged")

# --- the race: another writer creates the destination first -----------------
# No lies about os.path.exists here: the competing writer really creates the file
# between expose()'s check and the exclusive open, and those bytes must survive.
race_name = "run-race.safetensors"
race_path = os.path.join(models, race_name)
RIVAL = b"another writer's model"


def rival_wins(a, b):
    with open(b, "wb") as f:          # the other process got there first
        f.write(RIVAL)
    raise FileExistsError("the destination appeared first")


try:
    expose(src, models, race_name, same_volume=False, writer=rival_wins)
    check(False, "a lost race raises FileExistsError")
except FileExistsError:
    check(True, "a lost race raises FileExistsError")
check(os.path.exists(race_path), "the competing file survives a lost race")
check(open(race_path, "rb").read() == RIVAL, "the competing file is byte-for-byte intact")
os.unlink(race_path)

# --- free space -------------------------------------------------------------
real_usage = shutil.disk_usage
shutil.disk_usage = lambda p: type("U", (), {"free": 1})()
try:
    expose(src, models, "run-d.safetensors", same_volume=False)
    check(False, "a copy without free space is refused")
except OSError as e:
    check("free space" in str(e), "a copy without free space is refused")
finally:
    shutil.disk_usage = real_usage
check(not os.path.exists(os.path.join(models, "run-d.safetensors")),
      "no file is created when free space is short")

# --- cleanup ----------------------------------------------------------------
a = os.path.join(tmp, "a.bin")
b = os.path.join(tmp, "b.bin")
open(a, "wb").write(b"1")
open(b, "wb").write(b"2")
cleanup([a, b], keep=False)
check(not os.path.exists(a) and not os.path.exists(b), "cleanup removes the run's files")
c = os.path.join(tmp, "c.bin")
open(c, "wb").write(b"3")
cleanup([c], keep=True)
check(os.path.exists(c), "keep mode leaves the file for inspection")
cleanup([os.path.join(tmp, "never-existed.bin")], keep=False)
check(True, "cleanup ignores paths that are already gone")

# --- saved_path ownership (a backend we do not trust) ----------------------
run = "abcd1234"
stem = "comfy-forge-4way-" + run
owned = os.path.join(outdir, stem + ".safetensors")
open(owned, "wb").write(b"merge")
user = os.path.join(outdir, "my-precious-model.safetensors")
open(user, "wb").write(b"user data")

got, why = claim_saved([owned], run, outdir)
check(got == os.path.realpath(owned) and why is None, "a run-prefixed saved_path inside the root is claimed")

got, why = claim_saved([owned], run, None)
check(got is None and "no trusted output root" in why,
      "claiming without a trusted root is refused, not defaulted")

got, why = claim_saved([user], run, outdir)
check(got is None and "refusing to delete" in why,
      "a backend path without this run's prefix is refused, not deleted")

dup = os.path.join(outdir, stem + "-b.safetensors")
open(dup, "wb").write(b"dup")
got, why = claim_saved([owned, dup], run, outdir)
check(got is None and "ambiguous" in why, "two matching saved_paths are refused as ambiguous")

other_dir = os.path.join(root, "output-elsewhere")
os.makedirs(other_dir, exist_ok=True)
outside = os.path.join(other_dir, stem + ".safetensors")
open(outside, "wb").write(b"outside")
got, why = claim_saved([outside], run, outdir)
check(got is None, "a prefixed file outside the trusted directory is refused")

got, why = claim_saved([os.path.join(outdir, "missing.safetensors")], run, outdir)
check(got is None, "a reported path that does not exist is refused")

check(open(user, "rb").read() == b"user data", "the user file is untouched by any refusal")

# --- trusted root comes from the operator, never from the backend -----------
fake_tree = os.path.join(tmp, "FakeComfy")
os.makedirs(os.path.join(fake_tree, "output"), exist_ok=True)
os.makedirs(os.path.join(fake_tree, "models"), exist_ok=True)
forged = os.path.join(fake_tree, "output", stem + ".safetensors")
open(forged, "wb").write(b"forged")

saved_env = os.environ.pop("COMFYUI_OUTPUT_DIR", None)
saved_local = os.environ.get("LOCALAPPDATA")
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "no-state-here")
try:
    check(resolve_output_root() is None,
          "no configuration means no trusted root, even beside a ComfyUI-shaped tree")
    check(resolve_output_root(os.path.join(tmp, "no-such-dir")) is None,
          "a root that does not exist is refused")
    check(os.path.realpath(resolve_output_root(outdir)) == os.path.realpath(outdir),
          "an explicit root is accepted")

    os.environ["COMFYUI_OUTPUT_DIR"] = outdir
    check(os.path.realpath(resolve_output_root()) == os.path.realpath(outdir),
          "the environment can name the trusted root")
    os.environ["COMFYUI_OUTPUT_DIR"] = os.path.join(tmp, "missing")
    check(resolve_output_root() is None, "an environment root that does not exist is refused")
finally:
    os.environ.pop("COMFYUI_OUTPUT_DIR", None)
    if saved_env is not None:
        os.environ["COMFYUI_OUTPUT_DIR"] = saved_env
    if saved_local is not None:
        os.environ["LOCALAPPDATA"] = saved_local

got, why = claim_saved([forged], run, None)
check(got is None, "a forged path in a ComfyUI-shaped tree is not claimed without a trusted root")

# --- preview registration happens before the download ----------------------
img = {"filename": stem + "-render_00001_.png", "subfolder": "", "type": "output"}
preview = preview_path(img, outdir, run)
check(preview == os.path.join(outdir, stem + "-render_00001_.png"),
      "the preview path is known before /view is called")
check(preview_path({"filename": "someone-elses.png", "subfolder": "", "type": "output"},
                   outdir, run) is None, "an unprefixed preview is not adopted")
check(preview_path({"filename": "x.png", "subfolder": "sub", "type": "output"},
                   outdir, run) is None, "a preview in a subfolder is not adopted")
check(preview_path({"filename": "../" + stem + "-render.png", "subfolder": "", "type": "output"},
                   outdir, run) is None, "a preview filename with ../ is rejected")
check(preview_path({"filename": stem + "-render.png", "subfolder": "", "type": "temp"},
                   outdir, run) is None, "a non-output preview type is rejected")

open(preview, "wb").write(b"png")
failed = cleanup([owned, preview], keep=False)
check(not os.path.exists(preview), "a failed download still removes the registered preview")
check(not os.path.exists(owned), "a failed download still removes the merge")

# --- cleanup reports what survives -----------------------------------------
locked = os.path.join(tmp, "locked.bin")
open(locked, "wb").write(b"locked")
real_unlink = os.unlink


def unlink_denied(path):
    if path == locked:
        raise PermissionError("file is in use by another process")
    return real_unlink(path)


os.unlink = unlink_denied
try:
    left = cleanup([locked], keep=False)
finally:
    os.unlink = real_unlink
check(len(left) == 1 and left[0][0] == locked, "a cleanup failure is reported, not swallowed")
check("in use" in left[0][1], "the cleanup failure keeps the reason")
check(os.path.exists(locked), "the locked file is left for the operator")
real_unlink(locked)

# --- keep mode is explicit and silent --------------------------------------
kept = os.path.join(tmp, "kept.bin")
open(kept, "wb").write(b"keep")
check(cleanup([kept], keep=True) == [], "keep mode reports no failures")
check(os.path.exists(kept), "keep mode leaves the artifact behind")
real_unlink(kept)

# --- exclusive publication: the guard is the open, not the pre-check --------
race2 = os.path.join(models, "run-race2.safetensors")
first = _copy_exclusive(src, race2)
try:
    _copy_exclusive(src, race2)
    check(False, "a second exclusive writer is refused")
except FileExistsError:
    check(True, "a second exclusive writer is refused")
check(open(race2, "rb").read() == b"x" * 4096, "the first writer's bytes survive")

shutil.rmtree(tmp, ignore_errors=True)
if fail == 0:
    print("MERGE ARTIFACTS: PASS")
    sys.exit(0)
print("MERGE ARTIFACTS: %d FAILED" % fail)
sys.exit(1)
