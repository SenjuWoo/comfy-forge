"""Build an Anima LoRA dataset from a folder, with or without an image tagger.

Input is a folder, usually an imgbrd-grabber export: images plus optional matching
.txt files of booru tags. Output is a new folder holding copies of the images with one
converted caption each, a review file, and a manifest. Source images and captions are
never modified.

Three modes:
  auto     use an existing .txt when there is one, tag the image when there is not
  convert  existing captions only - no tagger, no ONNX Runtime, no weights needed
  retag    tagger first, existing tags as the baseline; nothing is dropped silently

Nothing here trains a LoRA or uploads anything.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# WD14 v3 category column: 0 general, 1 artist, 3 copyright, 4 character, 9 rating.
CAT_GENERAL, CAT_ARTIST, CAT_COPYRIGHT, CAT_CHARACTER, CAT_RATING = 0, 1, 3, 4, 9
MODES = ("auto", "convert", "retag")
TYPES = ("character", "style", "outfit", "object")
MODELS = {
    "eva02": ("SmilingWolf/wd-eva02-large-tagger-v3", 448),
    "swinv2": ("SmilingWolf/wd-swinv2-tagger-v3", 448),
}
SCORE_TAG = re.compile(r"^score_\d+$")


def tagger_dir() -> str:
    root = os.environ.get("COMFY_KIT_TAGGER_DIR")
    if root:
        return root
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "comfy-kit", "taggers")


def find_model_files(name: str) -> tuple[str, str]:
    repo, _ = MODELS[name]
    d = os.path.join(tagger_dir(), repo)
    return os.path.join(d, "model.onnx"), os.path.join(d, "selected_tags.csv")


class Tagger:
    """WD14 v3 on ONNX Runtime. Imported lazily so convert mode needs nothing."""

    def __init__(self, name: str = "eva02"):
        import onnxruntime as ort

        onnx_path, csv_path = find_model_files(name)
        if not os.path.isfile(onnx_path):
            raise SystemExit(
                "tagger weights not found: %s\nrun: powershell -File TOOLS/Install-Tagger.ps1 -Name %s"
                % (onnx_path, name))
        self.name = name
        self.session = ort.InferenceSession(
            onnx_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.side = int(shape[1]) if isinstance(shape[1], int) else MODELS[name][1]
        self.tags = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.tags.append((row["name"], int(row["category"])))

    def predict(self, path: str) -> dict[str, float]:
        import numpy as np
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(path))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        w, h = img.size
        side = max(w, h)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(img, ((side - w) // 2, (side - h) // 2))
        canvas = canvas.resize((self.side, self.side), Image.BICUBIC)
        pixels = np.asarray(canvas, dtype="uint8")[:, :, ::-1]  # WD14 reads BGR
        batch = pixels[None, ...]
        # The value range stays 0-255; normalising makes this model answer
        # "monochrome, no humans" for a colour image.
        if "float" in self.session.get_inputs()[0].type:
            batch = batch.astype("float32")
        scores = self.session.run(None, {self.input_name: batch})[0][0]
        return {name: float(score) for (name, _), score in zip(self.tags, scores)}


# ---------------------------------------------------------------- tag handling

def normalize_tag(raw: str) -> str:
    """Booru tag to Anima caption text: spaces, lowercase, score_7 left alone."""
    tag = raw.strip().strip(",").strip()
    if not tag:
        return ""
    tag = re.sub(r"\s+", " ", tag)
    if SCORE_TAG.match(tag):
        return tag
    return re.sub(r"\s+", " ", tag.replace("_", " ")).strip().lower()


def normalize_tags(raw_tags: list[str]) -> list[str]:
    """Normalise, drop empties, dedupe case-insensitively, keep first-seen order."""
    seen = set()
    out = []
    for raw in raw_tags:
        tag = normalize_tag(raw)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def read_caption_file(path: str) -> tuple[list[str], list[str]]:
    """Booru caption file to tags. Returns (tags, problems)."""
    problems = []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        problems.append("caption was not valid UTF-8; unreadable bytes replaced")
    if not text.strip():
        return [], ["caption file is empty"]
    raw = [t for t in re.split(r"[,\n]", text) if t.strip()]
    tags = normalize_tags(raw)
    if not tags:
        problems.append("caption produced no usable tags")
    return tags, problems


def tags_from_scores(scores: dict[str, float], categories: dict[str, int],
                     threshold: float, character_threshold: float,
                     exclude: set[str], max_tags: int) -> list[str]:
    keep = []
    for name, score in scores.items():
        category = categories.get(name, CAT_GENERAL)
        if category == CAT_RATING:
            continue
        limit = character_threshold if category in (CAT_COPYRIGHT, CAT_CHARACTER) else threshold
        if score < limit:
            continue
        tag = normalize_tag(name)
        if tag in exclude or name in exclude:
            continue
        keep.append((tag, score))
    keep.sort(key=lambda item: item[1], reverse=True)
    return [tag for tag, _ in keep[:max_tags]]


# ------------------------------------------------------------ caption policies

def apply_policy(tags: list[str], categories: dict[str, int], concept_type: str,
                 trigger: str, drop_artists: bool) -> list[str]:
    """What survives for this kind of LoRA, before any trigger is added.

    character - everything is signal: keep all tags.
    style     - identity tags force a character onto every image, so copyright and
                character-category tags go; artists go only when asked.
    outfit/   - the subject varies, the concept does not: keep everything.
    object
    """
    out = list(tags)
    if concept_type == "style":
        out = [t for t in out if categories.get(t.replace(" ", "_"), CAT_GENERAL)
               not in (CAT_COPYRIGHT, CAT_CHARACTER)]
    if drop_artists:
        out = [t for t in out if categories.get(t.replace(" ", "_"), CAT_GENERAL) != CAT_ARTIST]
    if trigger:
        out = [t for t in out if t != normalize_tag(trigger)]
    return out


def compose_caption(tags: list[str], concept_type: str, trigger: str, concept: str,
                    append_concept: bool) -> str:
    """Trigger first for the concept kinds, then the concept if asked, then tags."""
    parts = []
    if trigger and concept_type in ("character", "outfit", "object"):
        parts.append(normalize_tag(trigger))
    if append_concept and concept:
        parts.append(concept.strip().rstrip(","))
    parts.extend(tags)
    if not parts and concept:
        parts.append(concept.strip().rstrip(","))
    return ", ".join(parts)


# --------------------------------------------------------------- dataset build

def list_images(folder: str, skip: str | None = None) -> list[str]:
    """Every image under `folder`, never descending into the output dataset."""
    skip_abs = os.path.abspath(skip) if skip else None
    found = []
    for root, dirs, files in os.walk(folder):
        if skip_abs:
            dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) != skip_abs]
        # A previous export is not source material: never caption our own output.
        dirs[:] = [d for d in dirs
                   if not os.path.isfile(os.path.join(root, d, "dataset-manifest.json"))]
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in IMAGE_EXT:
                found.append(os.path.join(root, name))
    return found


def out_rel_for(source: str, root: str, taken: set[str]) -> str:
    """Relative output path, disambiguated when two images would share one caption.

    `same.png` and `same.jpg` are different images but both want `same.txt`, so the
    second one is renamed to `same_jpg.jpg` - the pairing has to stay 1:1.
    """
    rel_dir = os.path.relpath(os.path.dirname(source), root)
    rel_dir = "" if rel_dir == "." else rel_dir
    stem, ext = os.path.splitext(os.path.basename(source))
    candidate = os.path.join(rel_dir, stem + ext)
    caption = os.path.join(rel_dir, stem + ".txt")
    if caption.lower() in taken:
        candidate = os.path.join(rel_dir, "%s_%s%s" % (stem, ext.lstrip(".").lower(), ext))
        caption = os.path.join(rel_dir, "%s_%s.txt" % (stem, ext.lstrip(".").lower()))
    taken.add(caption.lower())
    return candidate


def image_is_readable(path: str) -> tuple[bool, str]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


def build_entries(args, tagger_factory=None) -> tuple[list[dict], list[str]]:
    """One entry per image: where its caption came from and what it says.

    The output folder, the review file, the manifest and the zip all read this list,
    so a caption in the archive can never disagree with the caption on disk.
    """
    images = list_images(args.images, skip=args.out)
    if args.limit:
        images = images[: args.limit]
    problems: list[str] = []
    entries: list[dict] = []

    tagger = None
    categories: dict[str, int] = {}
    if args.mode in ("auto", "retag"):
        factory = tagger_factory or Tagger
        try:
            tagger = factory(args.tagger)
            categories = {name: cat for name, cat in getattr(tagger, "tags", [])}
        except SystemExit as exc:
            if args.mode == "retag":
                raise
            problems.append("tagger unavailable (%s); only existing captions were used" % exc)
            tagger = None

    taken: set[str] = set()
    for path in images:
        rel = out_rel_for(path, args.images, taken)
        txt = os.path.splitext(path)[0] + ".txt"
        has_txt = os.path.isfile(txt)
        out_caption_rel = os.path.splitext(rel)[0] + ".txt"
        entry = {"image": os.path.basename(path), "source_path": path,
                 "out_rel": rel, "out_caption": out_caption_rel,
                 "caption": "", "tags": [], "origin": "", "notes": [], "status": "ok"}

        # Resume: an existing caption in the output folder wins over doing the work again.
        out_caption_path = os.path.join(args.out, out_caption_rel)
        if args.resume and os.path.isfile(out_caption_path):
            tags, cap_problems = read_caption_file(out_caption_path)
            entry.update({"tags": tags, "origin": "resumed", "notes": cap_problems,
                          "caption": ", ".join(tags)})
            entries.append(entry)
            continue

        existing_tags: list[str] = []
        if has_txt:
            existing_tags, cap_problems = read_caption_file(txt)
            entry["notes"].extend(cap_problems)
            problems.extend("%s: %s" % (os.path.basename(txt), p) for p in cap_problems)

        if args.mode == "convert":
            if not has_txt:
                entry["status"] = "missing-caption"
                problems.append("%s: no caption (convert mode does not invent one)" % entry["image"])
                entries.append(entry)
                continue
            tags, origin = existing_tags, "grabber"
        elif args.mode == "retag" or (args.mode == "auto" and not has_txt):
            if tagger is None:
                entry["status"] = "missing-caption"
                problems.append("%s: no caption and no tagger" % entry["image"])
                entries.append(entry)
                continue
            ok, why = image_is_readable(path)
            if not ok:
                entry["status"] = "unreadable"
                entry["notes"].append(why)
                problems.append("%s: %s" % (entry["image"], why))
                entries.append(entry)
                continue
            predicted = tags_from_scores(tagger.predict(path), categories, args.threshold,
                                         args.character_threshold, args.exclude, args.max_tags)
            if existing_tags:
                added = [t for t in predicted if t not in existing_tags]
                entry["notes"].append("added %d tag(s): %s" % (len(added), ", ".join(added) or "none"))
                room = max(0, args.max_tags - len(existing_tags))
                tags = normalize_tags(existing_tags + predicted[:room])
                origin = "grabber+tagger"
            else:
                tags, origin = predicted, "tagger"
        else:
            tags, origin = existing_tags, "grabber"

        tags = apply_policy(tags, categories, args.type, args.trigger, args.drop_artists)
        caption = compose_caption(tags, args.type, args.trigger, args.concept, args.append_concept)
        entry.update({"tags": tags, "caption": caption, "origin": origin})
        if not caption:
            entry["status"] = "empty-caption"
            problems.append("%s: caption ended up empty" % entry["image"])
        entries.append(entry)
    return entries, problems


def write_dataset(entries: list[dict], args) -> tuple[list[str], list[str]]:
    """Copy images and write converted captions into the output folder."""
    written, problems = [], []
    for entry in entries:
        if entry["status"] != "ok":
            continue
        image_out = os.path.join(args.out, entry["out_rel"])
        caption_out = os.path.join(args.out, entry["out_caption"])
        os.makedirs(os.path.dirname(image_out) or args.out, exist_ok=True)
        try:
            if not os.path.exists(image_out):
                if args.link:
                    try:
                        os.link(entry["source_path"], image_out)
                    except OSError:
                        shutil.copy2(entry["source_path"], image_out)
                else:
                    shutil.copy2(entry["source_path"], image_out)
            with open(caption_out, "w", encoding="utf-8", newline="\n") as f:
                f.write(entry["caption"] + "\n")
            written.append(entry["out_rel"])
        except OSError as exc:
            problems.append("%s: %s" % (entry["out_rel"], exc))
    return written, problems


def review_rows(entries: list[dict]) -> list[dict]:
    return [{"image": e["out_rel"], "origin": e["origin"] or e["status"],
             "tags": len(e["tags"]), "caption": e["caption"],
             "notes": "; ".join(e["notes"]) or (e["status"] if e["status"] != "ok" else "")}
            for e in entries]


def write_review(entries: list[dict], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "_review.tsv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["image", "origin", "tags", "caption", "notes"],
                                delimiter="\t")
        writer.writeheader()
        for row in review_rows(entries):
            writer.writerow(row)
    return path


def dataset_warnings(entries: list[dict]) -> list[str]:
    """Tags that get baked in, thin captions, and images that were left out."""
    warnings = []
    ok = [e for e in entries if e["status"] == "ok"]
    counts: dict[str, int] = {}
    for entry in ok:
        for tag in entry["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    for tag, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        if count >= max(5, int(len(ok) * 0.8)):
            warnings.append("tag '%s' appears in %d of %d captions - if that is not always true, "
                            "it teaches the LoRA to bake it in" % (tag, count, len(ok)))
    for entry in ok:
        if not entry["tags"]:
            warnings.append("%s got no tags - caption it by hand" % entry["out_rel"])
        elif len(entry["tags"]) < 4:
            warnings.append("%s has only %d tags - check it by eye" % (entry["out_rel"], len(entry["tags"])))
    for entry in entries:
        if entry["status"] != "ok":
            warnings.append("%s was not included (%s)" % (entry["image"], entry["status"]))
    return warnings


def build_manifest(args, entries: list[dict], weights_sha: str | None = None) -> dict:
    ok = [e for e in entries if e["status"] == "ok"]
    return {
        "tool": "comfy-kit anima_dataset",
        "mode": args.mode,
        "type": args.type,
        "trigger": args.trigger,
        "concept": args.concept,
        "append_concept": bool(args.append_concept),
        "tagger": args.tagger if args.mode in ("auto", "retag") else None,
        "tagger_weights_sha256": weights_sha,
        "threshold": args.threshold,
        "character_threshold": args.character_threshold,
        "max_tags": args.max_tags,
        "images_seen": len(entries),
        "images_exported": len(ok),
        "sources": {s: sum(1 for e in ok if e["origin"] == s)
                    for s in ("grabber", "tagger", "grabber+tagger", "resumed")},
        "entries": [{"image": e["image"], "out_image": e["out_rel"],
                     "out_caption": e["out_caption"], "caption": e["caption"],
                     "tags": e["tags"], "origin": e["origin"], "status": e["status"],
                     "notes": e["notes"]} for e in entries],
        "warnings": dataset_warnings(entries),
    }


def export_zip(out_dir: str, manifest: dict, zip_path: str) -> str:
    """Archive each image with the caption the manifest says belongs to it."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest["entries"]:
            if entry["status"] != "ok":
                continue
            zf.write(os.path.join(out_dir, entry["out_image"]), entry["out_image"])
            zf.writestr(entry["out_caption"], entry["caption"] + "\n")
        if manifest.get("trigger"):
            zf.writestr("trigger.txt", manifest["trigger"] + "\n")
        zf.writestr("dataset-manifest.json", json.dumps(manifest, indent=2))
    return zip_path


def verify_zip(zip_path: str, manifest: dict) -> list[str]:
    """Reopen the archive and check every pair against the manifest."""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for entry in manifest["entries"]:
            if entry["status"] != "ok":
                continue
            if entry["out_image"] not in names:
                problems.append("missing image in archive: " + entry["out_image"])
            if entry["out_caption"] not in names:
                problems.append("missing caption in archive: " + entry["out_caption"])
                continue
            body = zf.read(entry["out_caption"]).decode("utf-8").strip()
            if body != entry["caption"].strip():
                problems.append("caption mismatch in archive: " + entry["out_caption"])
    return problems


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main(argv: list[str] | None = None, tagger_factory=None) -> int:
    ap = argparse.ArgumentParser(description="Build an Anima LoRA dataset from a folder.")
    ap.add_argument("--images", required=True, help="source folder (read-only)")
    ap.add_argument("--out", default=None,
                    help="output dataset folder (default: <images>/anima-dataset)")
    ap.add_argument("--mode", default="auto", choices=MODES,
                    help="auto: use existing .txt, tag what has none; convert: existing only; "
                         "retag: tagger merges into existing tags")
    ap.add_argument("--type", default="character", choices=TYPES)
    ap.add_argument("--trigger", default="",
                    help="word for the concept; used for character/outfit/object")
    ap.add_argument("--concept", default="", help="one sentence describing the shared idea")
    ap.add_argument("--append-concept", action="store_true",
                    help="add the concept sentence to every caption")
    ap.add_argument("--tagger", default="eva02", choices=sorted(MODELS))
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--character-threshold", type=float, default=0.50)
    ap.add_argument("--max-tags", type=int, default=40)
    ap.add_argument("--exclude", default="", help="comma-separated tags to drop")
    ap.add_argument("--drop-artists", action="store_true")
    ap.add_argument("--link", action="store_true", help="hard-link images instead of copying")
    ap.add_argument("--resume", action="store_true", help="keep captions already in --out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="write into a non-empty --out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--zip", dest="zip_path", default=None, help="write and verify a Civitai archive")
    args = ap.parse_args(argv)
    args.exclude = {normalize_tag(t) for t in args.exclude.split(",") if t.strip()}

    if not os.path.isdir(args.images):
        raise SystemExit("not a folder: " + args.images)
    args.out = args.out or os.path.join(args.images, "anima-dataset")
    if os.path.abspath(args.out) == os.path.abspath(args.images):
        raise SystemExit("--out must differ from --images; sources are read-only")
    if (os.path.isdir(args.out) and os.listdir(args.out)
            and not args.force and not args.resume and not args.dry_run):
        raise SystemExit("output folder is not empty: %s (pass --force to replace, "
                         "--resume to keep existing captions)" % args.out)

    entries, problems = build_entries(args, tagger_factory)
    weights_sha = None
    if args.mode in ("auto", "retag") and not args.dry_run:
        onnx_path, _ = find_model_files(args.tagger)
        weights_sha = sha256_of(onnx_path) if os.path.isfile(onnx_path) else None
    manifest = build_manifest(args, entries, weights_sha)

    if args.dry_run:
        for entry in entries:
            print("%-44s %-16s %s" % (entry["out_rel"], entry["status"], entry["caption"][:70]))
        print("\n%d seen, %d exportable, nothing written (--dry-run)"
              % (len(entries), manifest["images_exported"]))
        return 0

    written, write_problems = write_dataset(entries, args)
    problems.extend(write_problems)
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "dataset-manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    review_path = write_review(entries, args.out)

    if manifest["warnings"]:
        print("Warnings:")
        for warning in manifest["warnings"]:
            print("  -", warning)

    if args.zip_path:
        export_zip(args.out, manifest, args.zip_path)
        zip_problems = verify_zip(args.zip_path, manifest)
        if zip_problems:
            print("archive does NOT match the dataset:")
            for problem in zip_problems:
                print("  -", problem)
            return 1
        print("archive verified:", args.zip_path)

    print("\n%d seen, %d exported to %s" % (len(entries), len(written), args.out))
    print("review:", review_path)
    if problems:
        print("\n%d problem(s):" % len(problems))
        for problem in problems[:20]:
            print("  -", problem)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
