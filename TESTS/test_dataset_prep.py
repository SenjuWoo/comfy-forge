"""Dataset prep regressions: grabber input, output isolation, zip pairing, resume.

Every test runs against a temporary folder with a stub tagger, so CI needs no weights
and the user's own image collection is never touched.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TOOLS"))
from anima_dataset import (  # noqa: E402
    compose_caption, main, normalize_tag, normalize_tags, read_caption_file, verify_zip)

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok   " + name)
    else:
        print("FAIL " + name)
        fail += 1


CATEGORIES = {
    "1girl": 0, "solo": 0, "school_uniform": 0, "red_eyes": 0, "cherry_blossoms": 0,
    "hatsune_miku": 4, "vocaloid": 3, "blurry": 0,
}


class StubTagger:
    def __init__(self, scores=None):
        self.tags = list(CATEGORIES.items())
        self.scores = scores or {"1girl": 0.99, "solo": 0.95, "school_uniform": 0.88,
                                 "red_eyes": 0.80, "blurry": 0.10}
        self.calls = 0

    def predict(self, path):
        self.calls += 1
        return self.scores


def no_tagger(name):
    raise AssertionError("convert mode must not load a tagger")


def png(path, colour=(120, 40, 40)):
    from PIL import Image
    Image.new("RGB", (48, 48), colour).save(path)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


tmp = tempfile.mkdtemp(prefix="ds-")
src = os.path.join(tmp, "grabber")
out = os.path.join(tmp, "out")
os.makedirs(src)

try:
    # --- Anima formatting ----------------------------------------------------
    check(normalize_tag("score_7") == "score_7", "score tags keep their underscore")
    check(normalize_tag("long_hair") == "long hair", "underscores become spaces")
    check(normalize_tag("  Red_Eyes  ") == "red eyes", "whitespace and case normalised")
    check(normalize_tags(["a_b", "a b", "A_B", ""]) == ["a b"], "duplicates and blanks collapse")
    check(normalize_tag("artist_name_(style)") == "artist name (style)",
          "parentheses survive normalisation")

    # --- grabber captions are read-only --------------------------------------
    png(os.path.join(src, "001.png"))
    grabber_txt = os.path.join(src, "001.txt")
    original = "1girl, solo, long_hair, red_eyes, score_7\n"
    open(grabber_txt, "w", encoding="utf-8", newline="\n").write(original)
    before_hash = sha(grabber_txt)

    code = main(["--images", src, "--out", out, "--mode", "convert", "--trigger", "mychar"],
                tagger_factory=no_tagger)
    check(code == 0, "convert mode completes without a tagger")
    check(sha(grabber_txt) == before_hash, "the source caption is byte-identical afterwards")
    check(open(grabber_txt, encoding="utf-8").read() == original, "the source caption text is unchanged")

    out_caption = os.path.join(out, "001.txt")
    check(os.path.isfile(out_caption), "a converted caption lands in the output folder")
    converted = open(out_caption, encoding="utf-8").read().strip()
    check(converted.startswith("mychar"), "the trigger leads the converted caption")
    check("long hair" in converted and "score_7" in converted,
          "conversion normalises tags and keeps score_7")
    check(os.path.isfile(os.path.join(out, "001.png")), "the image is copied into the output folder")
    manifest = json.load(open(os.path.join(out, "dataset-manifest.json"), encoding="utf-8"))
    check(manifest["images_exported"] == 1, "the manifest counts the exported image")
    check(manifest["entries"][0]["origin"] == "grabber", "the caption is attributed to grabber")
    check(os.path.isfile(os.path.join(out, "_review.tsv")), "a review file is written")

    # --- the zip carries the converted caption, not the source ---------------
    zip_path = os.path.join(tmp, "one.zip")
    code = main(["--images", src, "--out", os.path.join(tmp, "out2"), "--mode", "convert",
                 "--trigger", "mychar", "--zip", zip_path], tagger_factory=no_tagger)
    check(code == 0, "convert with --zip completes")
    with zipfile.ZipFile(zip_path) as zf:
        body = zf.read("001.txt").decode("utf-8").strip()
    check(body == converted, "the archive caption equals the converted caption")
    check("long_hair" not in body, "the archive does not carry the raw source caption")

    # --- mixed dataset: reuse what exists, tag what does not ----------------
    png(os.path.join(src, "002.png"))
    stub = StubTagger()
    mixed_out = os.path.join(tmp, "mixed")
    code = main(["--images", src, "--out", mixed_out, "--mode", "auto", "--trigger", "mychar"],
                tagger_factory=lambda name: stub)
    check(code == 0, "auto mode handles a mixed dataset")
    mixed = json.load(open(os.path.join(mixed_out, "dataset-manifest.json"), encoding="utf-8"))
    origins = {e["image"]: e["origin"] for e in mixed["entries"]}
    check(origins.get("001.png") == "grabber", "the grabber caption is reused, not replaced")
    check(origins.get("002.png") == "tagger", "the uncaptioned image is tagged")
    check(stub.calls == 1, "the tagger runs only for the image that needs it")
    check("1girl" in open(os.path.join(mixed_out, "002.txt"), encoding="utf-8").read(),
          "the generated caption carries tagger output")

    # --- resume keeps a complete record -------------------------------------
    resumed_out = os.path.join(tmp, "resumed")
    main(["--images", src, "--out", resumed_out, "--mode", "convert", "--trigger", "mychar"],
         tagger_factory=no_tagger)
    edited = os.path.join(resumed_out, "001.txt")
    open(edited, "w", encoding="utf-8", newline="\n").write("mychar, hand edited\n")
    code = main(["--images", src, "--out", resumed_out, "--mode", "auto", "--resume",
                 "--trigger", "mychar"], tagger_factory=lambda name: StubTagger())
    check(code == 0, "resume completes")
    check(open(edited, encoding="utf-8").read().strip() == "mychar, hand edited",
          "resume keeps the hand-edited caption")
    resume_manifest = json.load(open(os.path.join(resumed_out, "dataset-manifest.json"), encoding="utf-8"))
    check(resume_manifest["images_exported"] == resume_manifest["images_seen"] > 0,
          "resume accounts for every image in the manifest")
    check(any(e["origin"] == "resumed" for e in resume_manifest["entries"]),
          "resumed entries are recorded as such")

    # --- same stem, two extensions -----------------------------------------
    collide = os.path.join(tmp, "collide")
    os.makedirs(collide)
    png(os.path.join(collide, "same.png"), (10, 200, 10))
    png(os.path.join(collide, "same.jpg"), (200, 10, 10))
    collide_out = os.path.join(tmp, "collide-out")
    code = main(["--images", collide, "--out", collide_out, "--mode", "auto", "--trigger", "t"],
                tagger_factory=lambda name: StubTagger())
    produced = sorted(f for f in os.listdir(collide_out) if f.endswith(".txt"))
    check(len(produced) == 2 and len(set(produced)) == 2,
          "two images with one stem produce two captions with no collision")

    # --- malformed, empty and duplicate tags -------------------------------
    messy = os.path.join(tmp, "messy")
    os.makedirs(messy)
    png(os.path.join(messy, "a.png"))
    open(os.path.join(messy, "a.txt"), "w", encoding="utf-8").write("")
    png(os.path.join(messy, "b.png"))
    open(os.path.join(messy, "b.txt"), "w", encoding="utf-8").write("1girl, 1girl, ONE_girl, solo\n")
    tags_a, problems_a = read_caption_file(os.path.join(messy, "a.txt"))
    check(tags_a == [] and any("empty" in p for p in problems_a), "an empty caption is reported")
    tags_b, _ = read_caption_file(os.path.join(messy, "b.txt"))
    check(tags_b == ["1girl", "one girl", "solo"], "duplicate tags collapse after normalisation")

    # --- concept types ------------------------------------------------------
    character = compose_caption(["1girl", "hatsune miku"], "character", "mychar", "a girl", False)
    style = compose_caption(["1girl", "hatsune miku"], "style", "mychar", "lineart style", False)
    check(character.startswith("mychar"), "character captions carry the trigger")
    check(not style.startswith("mychar"), "style captions do not force a trigger")
    check("a girl" not in character, "the concept sentence is not appended by default")
    check(compose_caption(["1girl"], "character", "mychar", "a girl", True).count("a girl") == 1,
          "the concept can be appended when asked")

    # --- corrupt image ------------------------------------------------------
    broken = os.path.join(tmp, "broken")
    os.makedirs(broken)
    open(os.path.join(broken, "bad.png"), "wb").write(b"not an image at all")
    broken_out = os.path.join(tmp, "broken-out")
    code = main(["--images", broken, "--out", broken_out, "--mode", "auto"],
                tagger_factory=lambda name: StubTagger())
    check(code == 2, "a corrupt image makes the run report problems, not success")
    check(not os.path.exists(os.path.join(broken_out, "bad.txt")),
          "no caption is invented for a corrupt image")
    broken_manifest = json.load(open(os.path.join(broken_out, "dataset-manifest.json"), encoding="utf-8"))
    check(broken_manifest["images_exported"] == 0, "the manifest does not claim an export")

    # --- dry run ------------------------------------------------------------
    dry_out = os.path.join(tmp, "dry")
    code = main(["--images", src, "--out", dry_out, "--mode", "convert", "--trigger", "t",
                 "--dry-run"], tagger_factory=no_tagger)
    check(code == 0 and not os.path.exists(dry_out), "dry run writes nothing")

    # --- zip round trip and tamper detection --------------------------------
    trip_zip = os.path.join(tmp, "trip.zip")
    trip_out = os.path.join(tmp, "trip")
    main(["--images", src, "--out", trip_out, "--mode", "convert", "--trigger", "t",
          "--zip", trip_zip], tagger_factory=no_tagger)
    trip_manifest = json.load(open(os.path.join(trip_out, "dataset-manifest.json"), encoding="utf-8"))
    check(verify_zip(trip_zip, trip_manifest) == [], "a fresh archive verifies against its manifest")

    tampered = os.path.join(tmp, "tampered.zip")
    with zipfile.ZipFile(trip_zip) as source_zip, zipfile.ZipFile(tampered, "w") as dst_zip:
        for item in source_zip.namelist():
            body = source_zip.read(item)
            if item.endswith(".txt") and item != "trigger.txt":
                body = b"different caption\n"
            dst_zip.writestr(item, body)
    check(verify_zip(tampered, trip_manifest) != [], "a tampered archive fails verification")

    # --- output folder guard -------------------------------------------------
    guard_out = os.path.join(tmp, "guard")
    os.makedirs(guard_out)
    open(os.path.join(guard_out, "keep.txt"), "w").write("x")
    try:
        main(["--images", src, "--out", guard_out, "--mode", "convert"], tagger_factory=no_tagger)
        check(False, "a non-empty output folder is refused")
    except SystemExit as exc:
        check("not empty" in str(exc), "a non-empty output folder is refused")
    check(os.path.exists(os.path.join(guard_out, "keep.txt")), "the refusal leaves it untouched")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if fail == 0:
    print("DATASET PREP: PASS")
    sys.exit(0)
print("DATASET PREP: %d FAILED" % fail)
sys.exit(1)
