"""Failure injection for the merge test's artifact handling.

The live test writes a multi-gigabyte checkpoint. These cases prove that a run
which fails after that point removes what it created, refuses to touch a model
that was already there, and never leaves a half-copied file behind.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from integration_merge_graph import claim_saved, cleanup, expose, preview_path  # noqa: E402

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok   " + name)
    else:
        print("FAIL " + name)
        fail += 1


tmp = tempfile.mkdtemp(prefix="artifacts-")
src = os.path.join(tmp, "saved.safetensors")
open(src, "wb").write(b"x" * 4096)
models = os.path.join(tmp, "models")
os.makedirs(models, exist_ok=True)

# --- hard link on one volume ------------------------------------------------
visible, how = expose(src, models, "run-a.safetensors", same_volume=True)
check(os.path.exists(visible), "a same-volume expose creates the file")
check(how == "hard link", "a same-volume expose links instead of copying")
check(os.stat(visible).st_ino == os.stat(src).st_ino, "the link shares the source bytes")
check(os.path.getsize(src) == 4096, "the source is untouched")

# --- refusal ----------------------------------------------------------------
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
copied = []
def copy_ok(a, b):
    shutil.copyfile(a, b)
    copied.append(b)

v2, how2 = expose(src, models, "run-b.safetensors", copy=copy_ok, same_volume=False)
check(os.path.exists(v2) and how2 == "copy", "a cross-volume expose copies")
check(os.path.getsize(v2) == 4096, "the copy is complete")

# --- copy branch, fails midway ---------------------------------------------
def copy_dies(a, b):
    with open(b, "wb") as f:          # a partial file, then the failure
        f.write(b"x" * 1000)
    raise OSError("disk full")

try:
    expose(src, models, "run-c.safetensors", copy=copy_dies, same_volume=False)
    check(False, "a failed copy raises")
except OSError:
    check(True, "a failed copy raises")
check(not os.path.exists(os.path.join(models, "run-c.safetensors")),
      "a failed copy leaves no partial model behind")
check(os.path.exists(src) and os.path.getsize(src) == 4096,
      "a failed copy leaves the source intact")

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


# --- saved_path ownership (a backend we do not trust) -----------------------
run = "abcd1234"
stem = "comfy-kit-4way-" + run
owned = os.path.join(tmp, stem + ".safetensors")
open(owned, "wb").write(b"merge")
user = os.path.join(tmp, "my-precious-model.safetensors")
open(user, "wb").write(b"user data")

got, why = claim_saved([owned], run)
check(got == owned and why is None, "a run-prefixed saved_path is claimed")

got, why = claim_saved([user], run)
check(got is None and "refusing to delete" in why,
      "a backend path without this run's prefix is refused, not deleted")

got, why = claim_saved([owned, os.path.join(tmp, stem + "-b.safetensors")], run)
open(os.path.join(tmp, stem + "-b.safetensors"), "wb").write(b"dup")
got, why = claim_saved([owned, os.path.join(tmp, stem + "-b.safetensors")], run)
check(got is None and "ambiguous" in why, "two matching saved_paths are refused as ambiguous")

other_dir = os.path.join(tmp, "elsewhere")
os.makedirs(other_dir, exist_ok=True)
outside = os.path.join(other_dir, stem + ".safetensors")
open(outside, "wb").write(b"outside")
got, why = claim_saved([outside], run, root=models)
check(got is None, "a matching name outside the expected directory is refused")

got, why = claim_saved([os.path.join(tmp, "missing.safetensors")], run)
check(got is None, "a reported path that does not exist is refused")

check(open(user, "rb").read() == b"user data", "the user file is untouched by any refusal")

# --- preview registration happens before the download -----------------------
img = {"filename": stem + "-render_00001_.png", "subfolder": "", "type": "output"}
preview = preview_path(img, owned, run)
check(preview == os.path.join(tmp, stem + "-render_00001_.png"),
      "the preview path is known before /view is called")
check(preview_path({"filename": "someone-elses.png", "subfolder": ""}, owned, run) is None,
      "an unprefixed preview is not adopted")
check(preview_path({"filename": "x.png", "subfolder": "sub"}, owned, run) is None,
      "a preview in a subfolder is not adopted")

open(preview, "wb").write(b"png")
failed = []
def download_dies():
    raise OSError("connection reset")
try:
    download_dies()
except OSError:
    pass
failed.extend(cleanup([owned, preview], keep=False))
check(not os.path.exists(preview), "a failed download still removes the registered preview")
check(not os.path.exists(owned), "a failed download still removes the merge")

# --- cleanup reports what survives ------------------------------------------
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

# --- keep mode is explicit and silent ---------------------------------------
kept = os.path.join(tmp, "kept.bin")
open(kept, "wb").write(b"keep")
check(cleanup([kept], keep=True) == [], "keep mode reports no failures")
check(os.path.exists(kept), "keep mode leaves the artifact behind")
real_unlink(kept)

# --- exclusive publication (TOCTOU between exists() and copy) ---------------
race = os.path.join(models, "run-race.safetensors")
real_exists = os.path.exists
os.path.exists = lambda p: False          # lie: pretend the destination is free
try:
    expose(src, models, "run-race.safetensors", same_volume=False)
    second_failed = False
    try:
        expose(src, models, "run-race.safetensors", same_volume=False)
    except FileExistsError:
        second_failed = True
    check(second_failed, "exclusive creation loses the race instead of overwriting")
    check(open(race, "rb").read() == b"x" * 4096, "the first writer's bytes survive")
finally:
    os.path.exists = real_exists

shutil.rmtree(tmp, ignore_errors=True)
if fail == 0:
    print("MERGE ARTIFACTS: PASS")
    sys.exit(0)
print("MERGE ARTIFACTS: %d FAILED" % fail)
sys.exit(1)
