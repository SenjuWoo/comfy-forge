"""Dataset defects found in the 0.10.0 audit, each driven through a real process.

1. --exclude must strip the tag from captions that already exist. inspect and the
   docs tell you to `--exclude` a tag that is in most captions; it only filtered
   tagger predictions, so a convert run kept every excluded tag.
2. A caption saved as UTF-16 (what Windows PowerShell 5.1's `>` writes) must be
   read as text, not exported as NUL-filled garbage with status ok.
3. A rebuild after a source image was removed must not leave that pair in the
   output; the tool's own `validate` failed the folder it had just written.
   A file the user changed in the output is never deleted.

Needs Pillow. Run: python TESTS/test_dataset_regressions.py
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "TOOLS", "anima_dataset.py")
fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name)
        fail += 1


def picture(path, colour, size=(800, 800)):
    img = Image.new("RGB", size, colour)
    for x in range(0, size[0], 7):
        img.putpixel((x, (x * 3 + colour[0]) % size[1]), (255, 255, 255))
    img.save(path)


def cli(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    # Without --json the readable report is on stdout; with it, on stderr. Tests
    # that look for a message read both.
    return r.returncode, r.stdout, r.stdout + r.stderr


def caption_of(folder, stem):
    with open(os.path.join(folder, stem + ".txt"), encoding="utf-8") as fh:
        return fh.read().strip()


def main():
    with tempfile.TemporaryDirectory(prefix="dataset regress ") as tmp:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        for i, colour in enumerate([(200, 10, 10), (10, 200, 10), (10, 10, 200)]):
            picture(os.path.join(src, "img%d.png" % i), colour)
            with open(os.path.join(src, "img%d.txt" % i), "w", encoding="utf-8") as fh:
                fh.write("1girl, watermark, long_hair, signature, artist:some_painter, red_dress, smile\n")

        # 1. --exclude reaches existing captions, artist tags included.
        out = os.path.join(tmp, "out")
        code, _, err = cli("--images", src, "--out", out, "--mode", "convert", "--trigger", "tok",
                           "--exclude", "watermark,signature,some_painter")
        cap = caption_of(out, "img0")
        check(code == 0, "convert with --exclude exits 0 (%s)" % err[-200:])
        check("watermark" not in cap and "signature" not in cap, "--exclude strips existing tags: %s" % cap)
        check("@some painter" not in cap, "--exclude matches an artist tag by name, without the @")
        check("long hair" in cap and "red dress" in cap, "tags that were not excluded stay")
        with open(os.path.join(out, "_review.tsv"), encoding="utf-8") as fh:
            review = fh.read()
        check("excluded" in review and "watermark" in review, "the review file says which tags --exclude removed")

        # 2. UTF-16 captions, with and without a BOM, read as the text they hold.
        u16 = os.path.join(tmp, "u16")
        os.makedirs(u16)
        picture(os.path.join(u16, "a.png"), (90, 30, 30))
        picture(os.path.join(u16, "b.png"), (30, 90, 30))
        with open(os.path.join(u16, "a.txt"), "wb") as fh:
            fh.write("1girl, smile, café\r\n".encode("utf-16"))      # BOM, little endian
        with open(os.path.join(u16, "b.txt"), "wb") as fh:
            fh.write("1girl, frown\r\n".encode("utf-16-le"))         # no BOM
        u16_out = os.path.join(tmp, "u16-out")
        code, _, err = cli("--images", u16, "--out", u16_out, "--mode", "convert")
        a, b = caption_of(u16_out, "a"), caption_of(u16_out, "b")
        check(a == "1girl, smile, café", "UTF-16 with a BOM reads as text: %r" % a)
        check(b == "1girl, frown", "UTF-16 without a BOM reads as text: %r" % b)
        check("\x00" not in a + b and "�" not in a + b, "no NUL or replacement characters reach a caption")
        check(code == 0, "UTF-16 captions are not a problem (exit %d, %s)" % (code, err[-200:]))

        # Bytes that are no text encoding at all are still reported, not passed as ok.
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad)
        picture(os.path.join(bad, "c.png"), (30, 30, 90))
        with open(os.path.join(bad, "c.txt"), "wb") as fh:
            fh.write(b"1girl, \xff\xfe\xfa smile\n")
        code, _, err = cli("--images", bad, "--out", os.path.join(tmp, "bad-out"), "--mode", "convert")
        check(code == 2 and "not valid UTF-8" in err, "undecodable bytes still produce a named problem")

        # 3. A removed source leaves no stale pair after a rebuild.
        os.remove(os.path.join(src, "img2.png"))
        os.remove(os.path.join(src, "img2.txt"))
        code, _, err = cli("--images", src, "--out", out, "--mode", "convert", "--trigger", "tok",
                           "--exclude", "watermark,signature,some_painter", "--force")
        check(code == 0, "rebuild after a removed source exits 0 (%s)" % err[-200:])
        check(not os.path.exists(os.path.join(out, "img2.png")) and not os.path.exists(os.path.join(out, "img2.txt")),
              "the pair whose source is gone was removed from the output")
        code, stdout, _ = cli("validate", out, "--json")
        stdout = stdout.strip()
        check(code == 0 and json.loads(stdout)["status"] == "PASS", "the rebuilt folder validates: %s" % stdout[-300:])

        # A stale output file the user changed is kept, and the run says so.
        os.remove(os.path.join(src, "img1.png"))
        os.remove(os.path.join(src, "img1.txt"))
        with open(os.path.join(out, "img1.txt"), "w", encoding="utf-8") as fh:
            fh.write("tok, my own caption\n")
        code, _, err = cli("--images", src, "--out", out, "--mode", "convert", "--trigger", "tok",
                           "--exclude", "watermark,signature,some_painter", "--force")
        check(os.path.isfile(os.path.join(out, "img1.txt")), "a caption you edited is never deleted")
        check(not os.path.exists(os.path.join(out, "img1.png")), "its unchanged image copy is removed")
        check(code == 2 and "img1.txt" in err, "the kept file is reported, so validate's complaint is explained")

    print("DATASET REGRESSIONS: %s" % ("PASS" if not fail else "%d FAILED" % fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
