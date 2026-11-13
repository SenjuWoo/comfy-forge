"""Folder in, upload archive out: inspect, filter, order, archive layout, validate.

A grabber-style fixture folder is built in a temporary directory whose name carries a
space, '!', parentheses and a non-ASCII letter. No tagger weights are needed.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "TOOLS", "anima_dataset.py")
sys.path.insert(0, os.path.join(HERE, "..", "TOOLS"))
from anima_dataset import (  # noqa: E402
    flat_names, inspect_folder, is_bookkeeping, main, order_tags, read_caption_detail,
    split_namespace, validate_dataset)

fail = 0
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(errors="backslashreplace")


def check(cond, name):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail += 1


def picture(path, colour, size=(1024, 1024), stripes=0):
    """A real image. `stripes` gives it structure so a perceptual hash has something
    to compare; two pictures with different stripes are different pictures."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(image)
    for i in range(stripes):
        x = (i + 1) * size[0] // (stripes + 1)
        draw.rectangle([x - 12, 0, x + 12, size[1]], fill=(255 - colour[0], 255 - colour[1], 255 - colour[2]))
    image.save(path)


def caption(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def run_cli(*args):
    done = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    return done.returncode, done.stdout, done.stderr


def build_fixture(root):
    src = os.path.join(root, "!shots (v2) é")
    os.makedirs(os.path.join(src, "set b"))
    picture(os.path.join(src, "001.png"), (200, 40, 40), stripes=2)
    caption(os.path.join(src, "001.txt"),
            "1girl, long_hair, artist:some_painter, character:mira_(original), copyright:original, "
            "rating:general, commentary_request, bad_pixiv_id, highres, score_7, red_dress, :3")
    picture(os.path.join(src, "002.jpg"), (40, 200, 40), stripes=3)
    caption(os.path.join(src, "002.txt"), "solo\n1girl\nlong_hair\ngreen_dress\nmeta:translated\nrating:e\n")
    shutil.copyfile(os.path.join(src, "001.png"), os.path.join(src, "001-copy.png"))   # exact duplicate
    caption(os.path.join(src, "001-copy.txt"), "1girl, long_hair")
    picture(os.path.join(src, "small.png"), (40, 40, 200), size=(320, 480), stripes=1)
    caption(os.path.join(src, "small.txt"), "1girl, long_hair, blue_dress")
    picture(os.path.join(src, "set b", "001.png"), (90, 90, 20), stripes=5)             # same name, other folder
    caption(os.path.join(src, "set b", "001.txt"), "1girl, long_hair, yellow_dress, re:zero")
    picture(os.path.join(src, "nocap.png"), (10, 10, 10), stripes=4)
    with open(os.path.join(src, "broken.png"), "wb") as handle:
        handle.write(b"not a png")
    caption(os.path.join(src, "broken.txt"), "1girl")
    caption(os.path.join(src, "orphan.txt"), "1girl")
    # the same picture as 002, re-encoded smaller: a near duplicate, not an exact one
    from PIL import Image
    Image.open(os.path.join(src, "002.jpg")).resize((800, 800)).save(os.path.join(src, "002-small.png"))
    caption(os.path.join(src, "002-small.txt"), "1girl, long_hair, green_dress")
    return src


def main_test() -> int:
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="ds pipeline "))
    try:
        src = build_fixture(tmp)

        # --- tag parsing -----------------------------------------------------
        check(split_namespace("artist:some_painter") == ("artist", "some_painter"), "artist: prefix is a namespace")
        check(split_namespace(":3") == ("", ":3"), "':3' stays a tag")
        check(split_namespace("re:zero") == ("", "re:zero"), "'re:zero' stays a tag")
        check(is_bookkeeping("commentary request") and is_bookkeeping("bad pixiv id")
              and not is_bookkeeping("highres") and not is_bookkeeping("red dress"),
              "bookkeeping tags are recognised and picture tags are not")
        tags, problems, kinds = read_caption_detail(os.path.join(src, "001.txt"))
        check("@some painter" in tags and kinds.get("@some painter") == "artist", "an artist becomes @name")
        check("safe" in tags and kinds.get("safe") == "safety", "rating:general becomes the safety tag")
        check("mira (original)" in tags and kinds.get("mira (original)") == "character", "character prefix is recorded")
        check(not any(":" in t and t != ":3" for t in tags), "no namespace prefix survives into a tag")
        ordered = order_tags(["red dress", "@some painter", "original", "mira (original)", "1girl", "safe",
                              "score_7", "highres"],
                             {"@some painter": "artist", "original": "copyright",
                              "mira (original)": "character", "safe": "safety"}, {})
        check(ordered == ["score_7", "highres", "safe", "1girl", "mira (original)", "original",
                          "@some painter", "red dress"], "Anima section order: %s" % ordered)
        check(flat_names(["a/x.png", "b/x.png", "x.jpg"]) == {"a/x.png": "x.png", "b/x.png": "b_x.png", "x.jpg": "x_2.jpg"},
              "flat archive names never collide, even across extensions")

        # --- inspect ---------------------------------------------------------
        report = inspect_folder(src)
        check(report["images"] == 8 and report["usable"] == 7, "inspect counts images and usable ones")
        check(report["missing_caption"] == ["nocap.png"], "inspect names the image with no caption")
        check(report["orphan_captions"] == ["orphan.txt"], "inspect names the caption with no image")
        check(len(report["unreadable"]) == 1 and report["unreadable"][0]["image"] == "broken.png",
              "inspect names the image that does not decode")
        check(report["exact_duplicates"] == [["001-copy.png", "001.png"]], "inspect groups exact duplicates")
        near = {(p["a"], p["b"]) for p in report["near_duplicates"]}
        check(("002-small.png", "002.jpg") in near, "inspect finds the resized copy as a near duplicate")
        check(not any("set b" in a or "set b" in b for a, b in near), "different pictures are not near duplicates")
        check(report["sizes"]["under_768"] == 1 and report["sizes"]["short_side_min"] == 320,
              "inspect measures the short side")
        check("commentary request" in report["tags"]["bookkeeping"], "inspect lists bookkeeping tags")
        check("--mode auto" in report["next"] and src in report["next"], "inspect prints the next command")
        code, out, err = run_cli("inspect", "--images", src, "--json")
        check(code == 0 and json.loads(out)["captioned"] == 6, "inspect --json from a real process: %s" % err[-200:])
        # a name the console cannot encode must not end the run
        odd = os.path.join(tmp, "odd")
        os.makedirs(odd)
        picture(os.path.join(odd, "絵 ★.png"), (9, 99, 199), stripes=2)
        caption(os.path.join(odd, "絵 ★.txt"), "1girl, 絵")
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        for arguments in (["inspect", "--images", odd],
                          ["--images", odd, "--out", os.path.join(tmp, "odd-out"), "--mode", "convert", "--dry-run"]):
            done = subprocess.run([sys.executable, TOOL, *arguments], capture_output=True, env=env, timeout=300)
            check(done.returncode == 0 and b"Traceback" not in done.stderr,
                  "a name outside the console code page does not crash %s" % arguments[0])
        before = sorted(os.listdir(src))

        # --- build -----------------------------------------------------------
        out_dir = os.path.join(tmp, "out (1)")
        archive = os.path.join(tmp, "mira (upload).zip")
        code, out, err = run_cli("--images", src, "--out", out_dir, "--mode", "convert", "--trigger", "mira_oc",
                                 "--min-side", "512", "--zip", archive, "--json")
        check(code == 2, "a run that leaves images out exits 2 (got %d): %s" % (code, err[-300:]))
        result = json.loads(out)
        check(result["images_seen"] == 8 and result["images_exported"] == 4, "four pairs are exported: %s" % result["excluded"])
        check(result["excluded"] == {"duplicate": 1, "too-small": 1, "missing-caption": 1, "unreadable": 1},
              "every left-out image has a named reason")
        check(result["archive"]["verified"] and result["archive"]["layout"] == "flat", "the archive is verified and flat")
        check(sorted(os.listdir(src)) == before, "the source folder is untouched")

        first = open(os.path.join(out_dir, "001.txt"), encoding="utf-8").read().strip()
        check(first == "mira oc, highres, score_7, safe, 1girl, mira (original), original, @some painter, "
                       "long hair, red dress, :3", "001 caption: %s" % first)
        second = open(os.path.join(out_dir, "002.txt"), encoding="utf-8").read().strip()
        check(second == "mira oc, explicit, solo, 1girl, long hair, green dress", "002 caption: %s" % second)
        check(os.path.isfile(os.path.join(out_dir, "set b", "001.txt")), "the output folder keeps the source tree")
        review = open(os.path.join(out_dir, "_review.tsv"), encoding="utf-8").read()
        check("dropped 2 post-bookkeeping tag(s): commentary request, bad pixiv id" in review,
              "the review file says which tags were dropped")

        with zipfile.ZipFile(archive) as zf:
            names = sorted(zf.namelist())
        check(not any("/" in n for n in names), "no folder inside the upload archive: %s" % names)
        check({"001.png", "001.txt", "set b_001.png", "set b_001.txt", "trigger.txt"} <= set(names),
              "the two 001 images keep separate names in the archive")

        # --- validate --------------------------------------------------------
        good = validate_dataset(archive)
        check(good["status"] == "PASS" and good["pairs"] == 4 and good["trigger"] == "mira oc",
              "the archive validates: %s" % good["problems"])
        check(validate_dataset(out_dir)["status"] == "PASS", "the output folder validates")
        code, out, _ = run_cli("validate", archive, "--json")
        check(code == 0 and json.loads(out)["status"] == "PASS", "validate from a real process")

        tampered = os.path.join(tmp, "tampered.zip")
        with zipfile.ZipFile(archive) as zin, zipfile.ZipFile(tampered, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "002.txt":
                    data = b"\n"
                if item.filename == "set b_001.txt":
                    continue
                if item.filename == "001.png":
                    buffer = io.BytesIO()
                    from PIL import Image
                    Image.new("RGB", (64, 64), (1, 2, 3)).save(buffer, "PNG")
                    data = buffer.getvalue()
                zout.writestr(item, data)
        bad = validate_dataset(tampered)
        text = " | ".join(bad["problems"])
        check(bad["status"] == "FAIL", "a tampered archive fails validation")
        check("002.txt is empty" in text, "an empty caption is named")
        check("set b_001.png has no caption" in text, "a missing caption is named")
        check("001.png is not the image the manifest recorded" in text, "a swapped image is named")
        code, _, _ = run_cli("validate", tampered)
        check(code == 2, "validate exits 2 on a bad archive")
        check(validate_dataset(os.path.join(tmp, "absent.zip"))["status"] == "FAIL", "a missing path fails cleanly")

        # --- options ---------------------------------------------------------
        keep = os.path.join(tmp, "keep")
        main(["--images", src, "--out", keep, "--mode", "convert", "--order", "source", "--keep-bookkeeping",
              "--keep-duplicates", "--drop-rating", "--drop-artists"])
        kept = open(os.path.join(keep, "001.txt"), encoding="utf-8").read().strip()
        check(kept.startswith("1girl, long hair, mira (original)") and "commentary request" in kept
              and "safe" not in kept.split(", ") and "@some painter" not in kept,
              "source order, kept bookkeeping, dropped rating and artist: %s" % kept)
        check(os.path.isfile(os.path.join(keep, "001-copy.txt")), "--keep-duplicates exports the copy")

        kohya_zip = os.path.join(tmp, "kohya.zip")
        main(["--images", src, "--out", os.path.join(tmp, "kohya"), "--mode", "convert", "--trigger", "mira_oc",
              "--zip", kohya_zip, "--zip-layout", "kohya", "--repeats", "8"])
        with zipfile.ZipFile(kohya_zip) as zf:
            check(any(n.startswith("8_mira oc/") for n in zf.namelist()), "kohya layout uses one '<repeats>_<trigger>' folder")

        # --- PowerShell launcher passes --out and --mode through --------------
        shell = shutil.which("powershell.exe") if os.name == "nt" else None
        if shell:
            ps_out = os.path.join(tmp, "ps out")
            done = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                 os.path.join(HERE, "..", "TOOLS", "dataset.ps1"), "build", "--images", src,
                 "--out", ps_out, "--mode", "convert", "--force", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            manifest_file = os.path.join(ps_out, "dataset-manifest.json")
            check(done.returncode == 2 and os.path.isfile(manifest_file),
                  "the launcher writes to the --out it was given (exit %d): %s" % (done.returncode, done.stderr[-200:]))
            if os.path.isfile(manifest_file):
                check(json.load(open(manifest_file, encoding="utf-8"))["mode"] == "convert",
                      "the launcher does not swallow --mode")
            check(not os.path.isdir(os.path.join(src, "anima-dataset")), "nothing lands in the default folder instead")
        else:
            print("skip the PowerShell launcher (Windows only)")

        # --- resume against a manifest from before these settings ------------
        legacy = os.path.join(tmp, "legacy")
        main(["--images", src, "--out", legacy, "--mode", "convert", "--order", "source", "--keep-bookkeeping",
              "--keep-duplicates"])
        manifest_path = os.path.join(legacy, "dataset-manifest.json")
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        for key in ("order", "keep_bookkeeping", "drop_rating", "min_side", "keep_duplicates"):
            manifest["settings"].pop(key)
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"))
        try:
            main(["--images", src, "--out", legacy, "--mode", "convert", "--resume"])
            check(False, "resuming an old dataset under the new defaults is refused")
        except SystemExit as exc:
            check("order" in str(exc) and "--force" in str(exc), "resuming an old dataset under the new defaults is refused")
        code = main(["--images", src, "--out", legacy, "--mode", "convert", "--resume", "--order", "source",
                     "--keep-bookkeeping", "--keep-duplicates"])
        check(code == 2, "the old settings still resume an old dataset")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nDATASET PIPELINE: " + ("PASS" if not fail else "%d FAILED" % fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_test())
