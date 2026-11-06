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
from integration_merge_graph import cleanup, expose  # noqa: E402

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

shutil.rmtree(tmp, ignore_errors=True)
if fail == 0:
    print("MERGE ARTIFACTS: PASS")
    sys.exit(0)
print("MERGE ARTIFACTS: %d FAILED" % fail)
sys.exit(1)
