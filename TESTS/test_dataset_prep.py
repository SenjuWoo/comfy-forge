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
    apply_policy, compose_caption, main, normalize_tag, normalize_tags, read_caption_file,
    verify_zip)

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


class ColourTagger:
    """A tagger whose only skill is naming the dominant colour, so a changed image
    produces a visibly different caption."""
    tags = [("red background", 0), ("green background", 0)]

    def __init__(self, name=None):
        pass

    def predict(self, path):
        from PIL import Image
        data = Image.open(path).convert("RGB").tobytes()
        name = "red background" if data[0] > data[1] else "green background"
        return {name: 0.99}


def dead_tagger(name):
    raise SystemExit("no tagger in this test run")


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
    main(["--images", src, "--out", resumed_out, "--mode", "auto", "--trigger", "mychar"],
         tagger_factory=lambda name: StubTagger())
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

    # --- resume must not pair a changed image with a stale caption ----------
    rb = os.path.join(tmp, "resume-b")
    os.makedirs(rb)
    rb_img = os.path.join(rb, "r.png")
    png(rb_img, (200, 30, 30))
    open(os.path.join(rb, "r.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    rb_out = os.path.join(tmp, "resume-b-out")
    main(["--images", rb, "--out", rb_out, "--mode", "convert"], tagger_factory=no_tagger)
    png(rb_img, (30, 200, 30))
    open(os.path.join(rb, "r.txt"), "w", encoding="utf-8", newline="\n").write("green_hair\n")
    main(["--images", rb, "--out", rb_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    check(sha(os.path.join(rb_out, "r.png")) == sha(rb_img), "resume refreshes a changed image")
    check("green hair" in open(os.path.join(rb_out, "r.txt"), encoding="utf-8").read(),
          "resume does not keep a stale caption beside a changed image")

    # --- an emptied resumed caption is refused, not archived ----------------
    re_src = os.path.join(tmp, "resume-empty")
    os.makedirs(re_src)
    png(os.path.join(re_src, "e.png"))
    open(os.path.join(re_src, "e.txt"), "w", encoding="utf-8", newline="\n").write("1girl, solo\n")
    re_out = os.path.join(tmp, "resume-empty-out")
    main(["--images", re_src, "--out", re_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(re_out, "e.txt"), "w", encoding="utf-8", newline="\n").write("   \n")
    re_zip = os.path.join(tmp, "resume-empty.zip")
    code = main(["--images", re_src, "--out", re_out, "--mode", "convert", "--resume",
                 "--zip", re_zip], tagger_factory=no_tagger)
    check(code != 0, "an empty resumed caption makes the run fail")
    re_manifest = json.load(open(os.path.join(re_out, "dataset-manifest.json"), encoding="utf-8"))
    check(re_manifest["entries"][0]["status"] == "empty-caption", "the empty caption is marked incomplete")
    import zipfile as _zip
    check(not os.path.isfile(re_zip) or "e.txt" not in _zip.ZipFile(re_zip).namelist(),
          "an empty caption never reaches the archive")

    # --- a hand-edited caption on a changed image is kept and reported ------
    ce_src = os.path.join(tmp, "resume-conflict")
    os.makedirs(ce_src)
    ce_img = os.path.join(ce_src, "c.png")
    png(ce_img, (90, 90, 90))
    open(os.path.join(ce_src, "c.txt"), "w", encoding="utf-8", newline="\n").write("grey_hair\n")
    ce_out = os.path.join(tmp, "resume-conflict-out")
    main(["--images", ce_src, "--out", ce_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(ce_out, "c.txt"), "w", encoding="utf-8", newline="\n").write("my own caption\n")
    png(ce_img, (250, 250, 250))
    code = main(["--images", ce_src, "--out", ce_out, "--mode", "convert", "--resume"],
                tagger_factory=no_tagger)
    check(open(os.path.join(ce_out, "c.txt"), encoding="utf-8").read().strip() == "my own caption",
          "a hand-edited caption survives a changed image")
    check(code != 0, "the conflict is reported rather than passed silently")
    ce_manifest = json.load(open(os.path.join(ce_out, "dataset-manifest.json"), encoding="utf-8"))
    check(ce_manifest["entries"][0]["status"] == "conflict", "the conflict is recorded in the manifest")

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
    style = compose_caption(["1girl"], "style", "mystyle", "painterly fantasy", False)
    check(character.startswith("mychar"), "character captions carry the trigger")
    check(style.startswith("mystyle"), "style captions carry their trigger too")
    check("a girl" not in character, "the concept sentence is not appended by default")
    check(compose_caption(["1girl"], "character", "mychar", "a girl", True).count("a girl") == 1,
          "the concept can be appended when asked")

    # style policy: identity tags go when their category is known, and unknown tags
    # come back so the caller can warn instead of pretending they were filtered
    kept, unknown = apply_policy(["hatsune miku", "vocaloid", "1girl", "long hair"],
                                 {"hatsune_miku": 4, "vocaloid": 3}, "style", "mystyle", False)
    check(kept == ["1girl", "long hair"], "style drops classified character and copyright tags")
    check(unknown == [], "classified tags are not reported as unknown")
    kept, unknown = apply_policy(["hatsune miku", "1girl"], {}, "style", "mystyle", False)
    check(unknown == ["hatsune miku", "1girl"], "with no category table, every tag is reported for review")
    check("hatsune miku" in kept, "an unclassifiable tag is kept rather than silently dropped")

    # the concept reorders what it names, rather than being ignored
    ordered, _ = apply_policy(["1girl", "solo", "painterly", "long hair"], {}, "character",
                              "", False, "a painterly fantasy style")
    check(ordered[0] == "painterly", "tags named by the concept move to the front")

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

    # --- resume must notice a source caption edit with an unchanged image ---
    cs = os.path.join(tmp, "cap-edit")
    os.makedirs(cs)
    png(os.path.join(cs, "a.png"), (200, 20, 20))
    open(os.path.join(cs, "a.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    cs_out = os.path.join(tmp, "cap-edit-out")
    main(["--images", cs, "--out", cs_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(cs, "a.txt"), "w", encoding="utf-8", newline="\n").write("green_hair\n")
    main(["--images", cs, "--out", cs_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    check("green hair" in open(os.path.join(cs_out, "a.txt"), encoding="utf-8").read(),
          "resume notices a source caption edit even when the image is unchanged")

    # --- a deleted manifest makes resume untrustworthy ---------------------
    nm = os.path.join(tmp, "no-manifest")
    os.makedirs(nm)
    nm_img = os.path.join(nm, "b.png")
    png(nm_img, (200, 20, 20))
    open(os.path.join(nm, "b.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    nm_out = os.path.join(tmp, "no-manifest-out")
    main(["--images", nm, "--out", nm_out, "--mode", "convert"], tagger_factory=no_tagger)
    os.remove(os.path.join(nm_out, "dataset-manifest.json"))
    png(nm_img, (20, 200, 20))
    open(os.path.join(nm, "b.txt"), "w", encoding="utf-8", newline="\n").write("green_hair\n")
    try:
        main(["--images", nm, "--out", nm_out, "--mode", "convert", "--resume"],
             tagger_factory=no_tagger)
        check(False, "resume without a manifest is refused")
    except SystemExit as exc:
        check("manifest" in str(exc), "resume without a manifest is refused")

    # --- a changed uncaptioned image needs the tagger again -----------------
    uc = os.path.join(tmp, "uncaptioned")
    os.makedirs(uc)
    uc_img = os.path.join(uc, "c.png")
    png(uc_img, (200, 20, 20))
    uc_out = os.path.join(tmp, "uncaptioned-out")
    main(["--images", uc, "--out", uc_out, "--mode", "auto"], tagger_factory=lambda name: ColourTagger())
    first_caption = open(os.path.join(uc_out, "c.txt"), encoding="utf-8").read()
    png(uc_img, (20, 200, 20))
    code = main(["--images", uc, "--out", uc_out, "--mode", "auto", "--resume"],
                tagger_factory=lambda name: ColourTagger())
    second_caption = open(os.path.join(uc_out, "c.txt"), encoding="utf-8").read()
    check(code == 0 and "green" in second_caption and "red" in first_caption,
          "a changed uncaptioned image reloads the tagger and gets a new caption")

    # --- a failed entry stays failed until a real repair --------------------
    rp = os.path.join(tmp, "repeat")
    os.makedirs(rp)
    rp_img = os.path.join(rp, "d.png")
    png(rp_img, (200, 20, 20))
    rp_out = os.path.join(tmp, "repeat-out")
    main(["--images", rp, "--out", rp_out, "--mode", "auto"],
         tagger_factory=lambda name: ColourTagger())
    png(rp_img, (20, 200, 20))
    failed = main(["--images", rp, "--out", rp_out, "--mode", "auto", "--resume"],
                  tagger_factory=dead_tagger)
    still_stale = open(os.path.join(rp_out, "d.txt"), encoding="utf-8").read()
    repaired = main(["--images", rp, "--out", rp_out, "--mode", "auto", "--resume"],
                    tagger_factory=lambda name: ColourTagger())
    final_caption = open(os.path.join(rp_out, "d.txt"), encoding="utf-8").read()
    check(failed != 0, "a resume that cannot regenerate reports failure")
    check("red" in still_stale, "the failed run does not pretend the old caption is fresh")
    check(repaired == 0 and "green" in final_caption,
          "the next successful resume actually repairs the caption")

    # --- settings that change captions block a resume ----------------------
    try:
        main(["--images", rp, "--out", rp_out, "--mode", "auto", "--resume", "--type", "style"],
             tagger_factory=lambda name: ColourTagger())
        check(False, "a settings change is refused on resume")
    except SystemExit as exc:
        check("settings" in str(exc), "a settings change is refused on resume")

    # --- an unresolved conflict survives repeated resumes -------------------
    cf = os.path.join(tmp, "conflict-repeat")
    os.makedirs(cf)
    cf_img = os.path.join(cf, "a.png")
    png(cf_img, (200, 20, 20))
    open(os.path.join(cf, "a.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    cf_out = os.path.join(tmp, "conflict-repeat-out")
    main(["--images", cf, "--out", cf_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(cf_out, "a.txt"), "w", encoding="utf-8", newline="\n").write("custom hand caption\n")
    png(cf_img, (20, 200, 20))
    first = main(["--images", cf, "--out", cf_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    second = main(["--images", cf, "--out", cf_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    third = main(["--images", cf, "--out", cf_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    kept = open(os.path.join(cf_out, "a.txt"), encoding="utf-8").read().strip()
    check(first != 0 and second != 0 and third != 0, "a conflict keeps failing on every resume")
    check(kept == "custom hand caption", "repeated resumes never overwrite the kept caption")
    cf_manifest = json.load(open(os.path.join(cf_out, "dataset-manifest.json"), encoding="utf-8"))
    check(cf_manifest["entries"][0]["status"] == "conflict", "the conflict is still recorded, not resolved")

    # editing the caption is the documented way to settle it
    open(os.path.join(cf_out, "a.txt"), "w", encoding="utf-8", newline="\n").write("my final caption\n")
    settled = main(["--images", cf, "--out", cf_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    check(settled == 0 and open(os.path.join(cf_out, "a.txt"), encoding="utf-8").read().strip() == "my final caption",
          "editing the kept caption resolves the conflict")

    # --- a resolved conflict still gets validated --------------------------
    vr = os.path.join(tmp, "validated-resolve")
    os.makedirs(vr)
    vr_img = os.path.join(vr, "a.png")
    png(vr_img, (200, 20, 20))
    open(os.path.join(vr, "a.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    vr_out = os.path.join(tmp, "validated-resolve-out")
    main(["--images", vr, "--out", vr_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(vr_out, "a.txt"), "w", encoding="utf-8", newline="\n").write("hand written\n")
    png(vr_img, (20, 200, 20))
    main(["--images", vr, "--out", vr_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)

    # empty the kept caption, then resolve by rerunning
    open(os.path.join(vr_out, "a.txt"), "w", encoding="utf-8", newline="\n").write("   \n")
    vr_zip = os.path.join(tmp, "validated-resolve.zip")
    code = main(["--images", vr, "--out", vr_out, "--mode", "convert", "--resume", "--zip", vr_zip],
                tagger_factory=no_tagger)
    vr_manifest = json.load(open(os.path.join(vr_out, "dataset-manifest.json"), encoding="utf-8"))
    check(code != 0, "an emptied caption on a resolved conflict fails the run")
    check(vr_manifest["entries"][0]["status"] == "empty-caption",
          "the emptied caption is marked, not exported")
    import zipfile as _zip2
    check(not os.path.isfile(vr_zip) or "a.txt" not in _zip2.ZipFile(vr_zip).namelist(),
          "the emptied caption never reaches the archive")

    # corrupt the source image, then resolve the conflict by hand
    cr = os.path.join(tmp, "validated-corrupt")
    os.makedirs(cr)
    cr_img = os.path.join(cr, "b.png")
    png(cr_img, (200, 20, 20))
    open(os.path.join(cr, "b.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    cr_out = os.path.join(tmp, "validated-corrupt-out")
    main(["--images", cr, "--out", cr_out, "--mode", "convert"], tagger_factory=no_tagger)
    open(os.path.join(cr_out, "b.txt"), "w", encoding="utf-8", newline="\n").write("hand written\n")
    open(cr_img, "wb").write(b"\x89PNG not really a png")
    main(["--images", cr, "--out", cr_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
    open(os.path.join(cr_out, "b.txt"), "w", encoding="utf-8", newline="\n").write("resolved by hand\n")
    cr_zip = os.path.join(tmp, "validated-corrupt.zip")
    code = main(["--images", cr, "--out", cr_out, "--mode", "convert", "--resume", "--zip", cr_zip],
                tagger_factory=no_tagger)
    cr_manifest = json.load(open(os.path.join(cr_out, "dataset-manifest.json"), encoding="utf-8"))
    check(code != 0, "a corrupt image on a resolved conflict fails the run")
    check(cr_manifest["entries"][0]["status"] == "unreadable",
          "the corrupt image is marked, not exported")
    check(not os.path.isfile(cr_zip) or "b.png" not in _zip2.ZipFile(cr_zip).namelist(),
          "the corrupt image never reaches the archive")
    check(open(os.path.join(cr_out, "b.txt"), encoding="utf-8").read().strip() == "resolved by hand",
          "the manual caption is preserved even when the image is rejected")

    # --- nested output captions without a manifest refuse before writing ----
    ns = os.path.join(tmp, "nested-src")
    os.makedirs(os.path.join(ns, "nested"))
    png(os.path.join(ns, "nested", "a.png"), (200, 20, 20))
    open(os.path.join(ns, "nested", "a.txt"), "w", encoding="utf-8", newline="\n").write("red_hair\n")
    ns_out = os.path.join(tmp, "nested-out")
    main(["--images", ns, "--out", ns_out, "--mode", "convert"], tagger_factory=no_tagger)
    nested_caption = os.path.join(ns_out, "nested", "a.txt")
    open(nested_caption, "w", encoding="utf-8", newline="\n").write("hand written nested\n")
    before = sha(nested_caption)
    os.remove(os.path.join(ns_out, "dataset-manifest.json"))
    open(os.path.join(ns, "nested", "a.txt"), "w", encoding="utf-8", newline="\n").write("green_hair\n")
    try:
        main(["--images", ns, "--out", ns_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
        check(False, "nested captions without a manifest refuse resume")
    except SystemExit as exc:
        check("manifest" in str(exc), "nested captions without a manifest refuse resume")
    check(sha(nested_caption) == before, "the refusal writes nothing to the output")

    open(os.path.join(ns_out, "dataset-manifest.json"), "w", encoding="utf-8").write("{not json")
    try:
        main(["--images", ns, "--out", ns_out, "--mode", "convert", "--resume"], tagger_factory=no_tagger)
        check(False, "a corrupt manifest with nested captions refuses resume")
    except SystemExit as exc:
        check("manifest" in str(exc), "a corrupt manifest with nested captions refuses resume")
    check(sha(nested_caption) == before, "a corrupt manifest leaves the output untouched")

    # --- --force is the deliberate rebuild ---------------------------------
    forced = main(["--images", ns, "--out", ns_out, "--mode", "convert", "--force"], tagger_factory=no_tagger)
    check(forced == 0 and "green hair" in open(nested_caption, encoding="utf-8").read(),
          "--force rebuilds a nested dataset on purpose")

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
