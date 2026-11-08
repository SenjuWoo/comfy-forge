"""Caption a folder for Anima LoRA training without needing the tagger weights.

The ONNX tagger is replaced by a stub that returns fixed scores, so these run in CI
and on a machine that has never downloaded a model.
"""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TOOLS"))
from anima_dataset import (  # noqa: E402
    assemble_caption, build_manifest, dataset_warnings, filter_scores, main)

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok   " + name)
    else:
        print("FAIL " + name)
        fail += 1


CATEGORIES = {
    "1girl": 0, "solo": 0, "silver_hair": 0, "red_eyes": 0, "school_uniform": 0,
    "blurry": 0, "hatsune_miku": 4, "vocaloid": 3, "general": 9,
}


class StubTagger:
    """Same interface as the real tagger, deterministic scores, no ONNX."""

    tags = list(CATEGORIES.items())

    def __init__(self, scores):
        self.scores = scores

    def predict(self, path):
        return self.scores


# --- tag filtering ----------------------------------------------------------
scores = {
    "1girl": 0.99, "solo": 0.95, "silver_hair": 0.71, "red_eyes": 0.62,
    "school_uniform": 0.42, "blurry": 0.20, "hatsune_miku": 0.55,
    "vocaloid": 0.40, "general": 0.99,
}
kept = [t for t, _ in filter_scores(scores, CATEGORIES, 0.35, 0.50, set(), 40)]
check(kept[:4] == ["1girl", "solo", "silver hair", "red eyes"], "tags are sorted by confidence")
check("school uniform" in kept, "a tag above the general threshold survives")
check("blurry" not in kept, "a low-confidence tag is dropped")
check("general" not in kept, "rating-category tags never reach a caption")
check("vocaloid" not in kept, "a copyright tag under the character threshold is dropped")
check("hatsune miku" in kept, "a character tag above its threshold survives")

capped = filter_scores(scores, CATEGORIES, 0.35, 0.50, set(), 2)
check(len(capped) == 2, "max-tags caps the caption")

excluded = filter_scores(scores, CATEGORIES, 0.35, 0.50, {"1girl"}, 40)
check("1girl" not in [t for t, _ in excluded], "the exclude list removes a tag")

# --- caption assembly -------------------------------------------------------
caption = assemble_caption("woomixchar", "a silver-haired girl in a red coat,",
                           [("1girl", 0.99), ("silver hair", 0.9)])
check(caption == "woomixchar, a silver-haired girl in a red coat, 1girl, silver hair",
      "trigger, then concept, then tags")
check(assemble_caption("", "", [("1girl", 1.0)]) == "1girl", "an empty trigger and concept are skipped")
check(assemble_caption("x", "", []) == "x", "a caption can be the trigger alone")

# --- dataset warnings -------------------------------------------------------
many = [{"file": "a.png", "tags": ["1girl", "solo"]} for _ in range(10)]
warned = dataset_warnings(many, 10)
check(any("1girl" in w and "bake" in w for w in warned),
      "a tag in every caption is flagged as a leak risk")
thin = [{"file": "b.png", "tags": ["1girl"]}]
check(any("only 1 tags" in w for w in dataset_warnings(thin, 1)),
      "a nearly empty caption is flagged")
empty = [{"file": "c.png", "tags": []}]
check(any("no confident tags" in w for w in dataset_warnings(empty, 1)),
      "an empty caption is flagged")

# --- end to end on a temp folder -------------------------------------------
tmp = tempfile.mkdtemp(prefix="dataset-")
try:
    from PIL import Image
    for name in ("one.png", "two.png"):
        Image.new("RGB", (64, 64), (120, 30, 30)).save(os.path.join(tmp, name))
    open(os.path.join(tmp, "notes.txt"), "w").write("not an image")

    import argparse
    args = argparse.Namespace(trigger="woomixchar", concept="a girl", threshold=0.35,
                              character_threshold=0.5, max_tags=40, exclude=set())
    factory = lambda name: StubTagger(scores)  # noqa: E731
    code = main(["--images", tmp, "--trigger", "woomixchar", "--concept", "a girl"],
                tagger_factory=factory)
    check(code == 0, "the CLI completes")
    captions = sorted(f for f in os.listdir(tmp) if f.endswith(".txt") and f != "notes.txt")
    check(captions == ["one.txt", "two.txt"], "one caption per image")
    body = open(os.path.join(tmp, "one.txt"), encoding="utf-8").read().strip()
    check(body.startswith("woomixchar, a girl,"), "the caption starts with trigger and concept")
    check(not os.path.exists(os.path.join(tmp, "notes.txt.txt")), "non-images are ignored")
    manifest = json.load(open(os.path.join(tmp, "dataset-manifest.json"), encoding="utf-8"))
    check(manifest["images"] == 2, "the manifest counts the images")
    check(manifest["trigger"] == "woomixchar", "the manifest records the trigger")
    check(len(manifest["entries"]) == 2, "the manifest lists every image")

    # resume must not rewrite an existing caption
    with open(os.path.join(tmp, "one.txt"), "w", encoding="utf-8") as f:
        f.write("hand written\n")
    main(["--images", tmp, "--trigger", "woomixchar", "--resume"], tagger_factory=factory)
    check(open(os.path.join(tmp, "one.txt"), encoding="utf-8").read() == "hand written\n",
          "resume leaves an existing caption alone")

    # dry run writes nothing
    before = set(os.listdir(tmp))
    main(["--images", tmp, "--trigger", "t", "--dry-run"], tagger_factory=factory)
    check(set(os.listdir(tmp)) == before, "dry run writes nothing")

    # civitai archive
    zip_path = os.path.join(tmp, "out.zip")
    main(["--images", tmp, "--trigger", "woomixchar", "--zip", zip_path], tagger_factory=factory)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    check("one.png" in names and "one.txt" in names, "the archive pairs images with captions")
    check("trigger.txt" in names, "the archive carries the trigger word")
    check("dataset-manifest.json" in names, "the archive carries the manifest")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if fail == 0:
    print("DATASET PREP: PASS")
    sys.exit(0)
print("DATASET PREP: %d FAILED" % fail)
sys.exit(1)
