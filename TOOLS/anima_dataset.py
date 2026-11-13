"""Build an Anima LoRA dataset from a folder, with or without an image tagger.

Input is a folder, usually an imgbrd-grabber export: images plus optional matching
.txt files of booru tags. Output is a new folder holding copies of the images with one
converted caption each, a review file, and a manifest. Source images and captions are
never modified.

Three modes:
  auto     use an existing .txt when there is one, tag the image when there is not
  convert  existing captions only - no tagger, no ONNX Runtime, no weights needed
  retag    tagger first, existing tags as the baseline; nothing is dropped silently

Commands:
  build     (default) write the dataset; every flag below belongs to it
  inspect   read-only report on a source folder: pairs, sizes, duplicates, tags
  validate  check a finished dataset folder or archive before it is uploaded

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
ORDERS = ("anima", "source")
ZIP_LAYOUTS = ("flat", "tree", "kohya")

# `artist:name` style prefixes that booru exporters can write in front of a tag.
NAMESPACES = {
    "artist": "artist", "creator": "artist",
    "character": "character", "char": "character",
    "copyright": "copyright", "series": "copyright", "parody": "copyright",
    "meta": "meta", "metadata": "meta",
    "general": "", "tag": "", "species": "",
    "rating": "rating",
}
# Booru ratings to the safety tags Anima was trained with.
RATINGS = {"g": "safe", "general": "safe", "safe": "safe",
           "s": "sensitive", "sensitive": "sensitive",
           "q": "nsfw", "questionable": "nsfw",
           "e": "explicit", "explicit": "explicit"}
SAFETY_TAGS = {"safe", "sensitive", "nsfw", "explicit"}
# Tags that describe the post or the upload, not the picture. They carry no visual
# signal, so a caption is better without them.
BOOKKEEPING = {
    "tagme", "commentary", "commentary request", "english commentary", "chinese commentary",
    "korean commentary", "japanese commentary", "symbol-only commentary", "partial commentary",
    "mixed-language commentary", "commentary typo", "translated", "translation request",
    "partially translated", "check translation", "hard-translated", "bad id", "bad pixiv id",
    "bad twitter id", "bad link", "bad source", "md5 mismatch", "duplicate", "revision",
    "resized", "upscaled", "image sample", "paid reward", "paid reward available",
    "patreon reward", "fanbox reward", "fantia reward", "gumroad reward", "skeb commission",
    "commission", "third-party edit", "third-party source", "second-party source",
    "non-web source", "source request", "artist request", "character request",
    "copyright request", "annotation request", "source larger", "source smaller",
    "variant set", "making-of available", "video available", "has bad revision",
    "has downscaled revision", "has cropped revision", "has glazed revision",
}
QUALITY_HEAD = {"masterpiece", "best quality", "good quality", "normal quality", "low quality",
                "worst quality", "highres", "absurdres", "incredibly absurdres", "lowres"}
YEAR_TAG = re.compile(r"^year \d{4}$")
COUNT_TAG = re.compile(r"^(?:\d\+?(?:girl|boy|other)s?|solo|solo focus|multiple girls|"
                       r"multiple boys|no humans)$")
# What a manifest written before these settings existed was built with.
LEGACY_SETTINGS = {"order": "source", "keep_bookkeeping": True, "drop_rating": False,
                   "min_side": 0, "keep_duplicates": True}


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


def split_namespace(raw: str) -> tuple[str, str]:
    """`artist:foo` to ("artist", "foo"). A tag with no known prefix is returned whole,
    so `:3`, `re:zero` and `nier:automata` stay ordinary tags."""
    text = raw.strip().strip(",").strip()
    head, sep, rest = text.partition(":")
    key = head.strip().lower()
    if sep and rest.strip() and key in NAMESPACES:
        return NAMESPACES[key], rest.strip()
    return "", text


def read_caption_detail(path: str) -> tuple[list[str], list[str], dict[str, str]]:
    """Booru caption file to tags, plus what each prefixed tag was declared to be.

    Returns (tags, problems, kinds). `kinds` maps a tag to artist, character,
    copyright, meta or safety when the source said so. Artists come out as `@name`,
    the form Anima reads, and a rating becomes its safety tag.
    """
    tags, problems = [], []
    kinds: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        problems.append("caption was not valid UTF-8; unreadable bytes replaced")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        return [], ["caption file is empty"], {}
    raw_tags = []
    for raw in re.split(r"[,\n]", text):
        if not raw.strip():
            continue
        kind, body = split_namespace(raw)
        tag = normalize_tag(body)
        if not tag:
            continue
        if kind == "rating":
            tag = RATINGS.get(tag)
            if not tag:
                continue
            kind = "safety"
        elif kind == "artist" or tag.startswith("@"):
            tag = "@" + tag.lstrip("@ ").strip()
            kind = "artist"
        if kind:
            kinds.setdefault(tag, kind)
        raw_tags.append(tag)
    tags = normalize_tags(raw_tags)
    if not tags:
        problems.append("caption produced no usable tags")
    return tags, problems, kinds


def is_bookkeeping(tag: str) -> bool:
    return tag in BOOKKEEPING or tag.endswith(" request") or tag.endswith(" commentary")


def order_tags(tags: list[str], kinds: dict[str, str], categories: dict[str, int]) -> list[str]:
    """Anima's caption order: quality and meta, safety, subject count, character,
    series, artist, then everything else. The sort is stable, so tags keep their
    relative order inside a section."""
    def section(tag: str) -> int:
        kind = kinds.get(tag, "")
        category = categories.get(tag.replace(" ", "_"), CAT_GENERAL) if categories else CAT_GENERAL
        if SCORE_TAG.match(tag) or tag in QUALITY_HEAD or YEAR_TAG.match(tag) or kind == "meta":
            return 0
        if kind == "safety":
            return 1
        if COUNT_TAG.match(tag):
            return 2
        if kind == "character" or category == CAT_CHARACTER:
            return 3
        if kind == "copyright" or category == CAT_COPYRIGHT:
            return 4
        if kind == "artist" or category == CAT_ARTIST or tag.startswith("@"):
            return 5
        return 6
    return sorted(tags, key=section)


def refine_tags(tags: list[str], kinds: dict[str, str], args) -> tuple[list[str], list[str]]:
    """Drop what the settings say to drop before the caption policy runs.

    Returns (kept, notes). Every removal is written down: nothing leaves a caption
    without a line in the review file saying so.
    """
    notes = []
    kept = list(tags)
    if not args.keep_bookkeeping:
        gone = [t for t in kept if is_bookkeeping(t)]
        if gone:
            kept = [t for t in kept if t not in gone]
            notes.append("dropped %d post-bookkeeping tag(s): %s" % (len(gone), ", ".join(gone)))
    if args.drop_rating:
        kept = [t for t in kept if kinds.get(t) != "safety"]
    if args.drop_artists:
        gone = [t for t in kept if kinds.get(t) == "artist"]
        if gone:
            kept = [t for t in kept if t not in gone]
            notes.append("dropped artist tag(s): %s" % ", ".join(gone))
    if args.type == "style":
        gone = [t for t in kept if kinds.get(t) in ("character", "copyright")]
        if gone:
            kept = [t for t in kept if t not in gone]
            notes.append("style: dropped %s" % ", ".join(gone))
    return kept, notes


def read_caption_file(path: str) -> tuple[list[str], list[str]]:
    """Booru caption file to tags. Returns (tags, problems)."""
    tags, problems, _kinds = read_caption_detail(path)
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

def load_categories(name: str = "eva02") -> dict[str, int]:
    """Tag categories from the tagger's CSV, without loading any model.

    Convert mode has no ONNX Runtime and no weights, but the CSV alone is enough to
    know that `hatsune_miku` is a character tag and `1girl` is not.
    """
    _, csv_path = find_model_files(name)
    if not os.path.isfile(csv_path):
        return {}
    out = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[row["name"]] = int(row["category"])
    except (OSError, ValueError, KeyError):
        return {}
    return out


STOPWORDS = {"a", "an", "the", "of", "and", "or", "with", "in", "on", "for", "to",
             "art", "style", "image", "illustration", "original", "clean"}


def concept_words(concept: str) -> list[str]:
    """Words from the concept sentence that can be matched against tags."""
    words = re.findall(r"[a-z0-9]+", concept.lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def apply_policy(tags: list[str], categories: dict[str, int], concept_type: str,
                 trigger: str, drop_artists: bool, concept: str = "") -> tuple[list[str], list[str]]:
    """Tags kept for this kind of LoRA, and tags whose category could not be checked.

    character - everything is signal.
    style     - copyright/character tags force an identity onto every image, so they
                go when their category is known. Unknown categories are returned as
                `unclassified` so the caller can warn instead of pretending.
    outfit/   - the subject varies, the concept does not: keep everything.
    object

    Tags matching a word from the concept are moved to the front: the concept says
    what this LoRA is about, so what it names matters more than the rest.
    """
    out = [t for t in tags if not trigger or t != normalize_tag(trigger)]
    unclassified: list[str] = []
    if concept_type == "style":
        if not categories:
            # No category table at all: we cannot tell a character name from a pose.
            # Keep everything and say so, rather than claiming a filter we did not do.
            unclassified = list(out)
        else:
            out = [t for t in out
                   if categories.get(t.replace(" ", "_"), CAT_GENERAL)
                   not in (CAT_COPYRIGHT, CAT_CHARACTER)]
    if drop_artists:
        out = [t for t in out if categories.get(t.replace(" ", "_"), CAT_GENERAL) != CAT_ARTIST]
    words = set(concept_words(concept))
    if words:
        named = [t for t in out if words & set(re.findall(r"[a-z0-9]+", t))]
        rest = [t for t in out if t not in named]
        out = named + rest
    return out, unclassified


def compose_caption(tags: list[str], concept_type: str, trigger: str, concept: str,
                    append_concept: bool) -> str:
    """Trigger first, then the concept if asked, then tags.

    The trigger is used for every concept type: a style LoRA wants its trigger word
    exactly as much as a character LoRA does.
    """
    parts = []
    if trigger:
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


def image_dimensions(path: str) -> tuple[int, int]:
    """Pixel size from the header, with the EXIF rotation applied. (0, 0) if unknown."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size
            orientation = 1
            try:
                orientation = int(im.getexif().get(0x0112, 1) or 1)
            except Exception:  # noqa: BLE001 - a broken EXIF block is not a broken image
                orientation = 1
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            return int(width), int(height)
    except Exception:  # noqa: BLE001
        return 0, 0


def dhash(path: str) -> int | None:
    """64-bit difference hash: the same picture re-encoded or resized lands within a
    few bits. None when Pillow is missing or the file does not decode."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            small = im.convert("L").resize((9, 8), Image.BILINEAR)
            pixels = small.tobytes()
    except Exception:  # noqa: BLE001
        return None
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def flat_names(rels: list[str], prefix: str = "") -> dict[str, str]:
    """Archive names with no folders. A name used twice gets its folder path folded in,
    so `a/x.png` and `b/x.png` stay two files."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for rel in rels:
        stem, ext = os.path.splitext(os.path.basename(rel))
        name = stem
        if name.lower() in taken:
            folder = re.sub(r"[\\/]+", "_", os.path.dirname(rel)).strip("_")
            name = "%s_%s" % (folder, stem) if folder else stem
        base, counter = name, 2
        while name.lower() in taken:
            name = "%s_%d" % (base, counter)
            counter += 1
        taken.add(name.lower())
        out[rel] = prefix + name + ext
    return out


def load_previous(out: str) -> tuple[dict[str, dict], dict]:
    """Entries and settings from the last run in this output folder.

    Resume uses these to tell "already done" apart from "the input changed under us".
    An unreadable or missing manifest returns nothing, which resume treats as untrusted.
    """
    path = os.path.join(out, "dataset-manifest.json")
    if not os.path.isfile(path):
        return {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        entries = {e["out_image"]: e for e in data.get("entries", []) if e.get("out_image")}
        return entries, data.get("settings") or {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}


def config_signature(args) -> dict:
    """Everything that changes what a caption says.

    Resume refuses to mix captions built under different settings, because a caption
    from another `--type` or `--threshold` is not the caption this run would produce.
    """
    return {"mode": args.mode, "type": args.type, "trigger": normalize_tag(args.trigger),
            "concept": args.concept.strip(), "append_concept": args.append_concept,
            "threshold": args.threshold, "character_threshold": args.character_threshold,
            "max_tags": args.max_tags, "exclude": sorted(args.exclude),
            "drop_artists": args.drop_artists, "tagger": args.tagger,
            "order": args.order, "keep_bookkeeping": args.keep_bookkeeping,
            "drop_rating": args.drop_rating, "min_side": args.min_side,
            "keep_duplicates": args.keep_duplicates}


def build_entries(args, tagger_factory=None) -> tuple[list[dict], list[str]]:
    """One entry per image: where its caption came from and what it says.

    The output folder, the review file, the manifest and the zip all read this list,
    so a caption in the archive can never disagree with the caption on disk.

    Resume is only trusted when the previous manifest exists, matches the current
    settings, and recorded a successful entry whose source image *and* source caption
    are unchanged. Anything else is recomputed from the source, and a caption the user
    edited by hand is kept but reported as a conflict.

    The tagger is initialised only when at least one image actually needs inference,
    after every kind is known - including images that only need it because they changed.
    """
    images = list_images(args.images, skip=args.out)
    if args.limit:
        images = images[: args.limit]
    problems: list[str] = []

    # Decode each source once, up front: the tagger would otherwise raise on a corrupt
    # file before anything had a chance to report it.
    readable = {path: image_is_readable(path) for path in images}

    prev, prev_settings = ({}, {})
    if args.resume:
        prev, prev_settings = load_previous(args.out)
        existing = []
        if os.path.isdir(args.out):
            existing = [f for _root, _dirs, files in os.walk(args.out)
                        for f in files if f.lower().endswith(".txt")]
        if existing and not prev:
            raise SystemExit(
                "--resume needs a readable dataset-manifest.json in %s; without it there is no "
                "way to tell an edited caption from a stale one. Rerun with --force to rebuild "
                "from the sources, or --out to a new folder." % args.out)
        signature = config_signature(args)
        if prev_settings:
            # A manifest from before a setting existed was built with the old behaviour.
            prev_settings = {**LEGACY_SETTINGS, **prev_settings}
        if prev_settings and prev_settings != signature:
            changed = sorted(k for k in set(prev_settings) | set(signature)
                             if prev_settings.get(k) != signature.get(k))
            raise SystemExit(
                "the captions in %s were built with different settings (%s); rerun with --force "
                "to rebuild them under the current settings." % (args.out, ", ".join(changed)))

    taken: set[str] = set()
    plan = []
    # Of several byte-identical images, export the one whose caption says the most.
    digests = {path: sha256_of(path) for path in images}
    keeper: dict[str, str] = {}
    richness: dict[str, int] = {}
    for path in images:
        if not readable[path][0]:
            continue
        txt = os.path.splitext(path)[0] + ".txt"
        tags = len(read_caption_detail(txt)[0]) if os.path.isfile(txt) else -1
        if digests[path] not in keeper or tags > richness[digests[path]]:
            keeper[digests[path]] = path
            richness[digests[path]] = tags
    for path in images:
        rel = out_rel_for(path, args.images, taken)
        txt = os.path.splitext(path)[0] + ".txt"
        out_caption_rel = os.path.splitext(rel)[0] + ".txt"
        has_txt = os.path.isfile(txt)
        digest = digests[path]
        caption_digest = sha256_of(txt) if has_txt else None
        if args.mode == "convert":
            base = "grabber" if has_txt else "missing-caption"
        elif args.mode == "retag":
            base = "tag"
        else:  # auto
            base = "grabber" if has_txt else "tag"

        previous = prev.get(rel)
        out_caption_path = os.path.join(args.out, out_caption_rel)
        kind = base
        if args.resume and os.path.isfile(out_caption_path) and previous:
            unchanged = (previous.get("source_sha256") == digest
                         and previous.get("source_caption_sha256") == caption_digest)
            status = previous.get("status")
            if status == "conflict":
                # Unresolved stays unresolved: rerunning must not overwrite the caption you
                # kept. Only editing that caption settles it; --force rebuilds from source.
                kept_tags, _ = read_caption_file(out_caption_path)
                still_mine = kept_tags and ", ".join(kept_tags) == (previous.get("caption") or "").strip()
                kind = "conflict" if still_mine else "resolved"
            elif status != "ok":
                kind = base          # a failed entry is never reused, however often it is rerun
            elif unchanged:
                kind = "resumed"
            else:
                kept_tags, _ = read_caption_file(out_caption_path)
                if kept_tags and ", ".join(kept_tags) != (previous.get("caption") or "").strip():
                    kind = "conflict"
                else:
                    kind = base
        size = image_dimensions(path) if readable[path][0] else (0, 0)
        skip = ""
        if readable[path][0]:
            if keeper.get(digest) != path and not args.keep_duplicates:
                skip = "duplicate"
            elif args.min_side and size != (0, 0) and min(size) < args.min_side:
                skip = "too-small"
        plan.append({"path": path, "rel": rel, "txt": txt, "has_txt": has_txt,
                     "out_caption": out_caption_rel, "kind": "skip" if skip else kind,
                     "sha": digest, "caption_sha": caption_digest, "previous": previous,
                     "readable": readable[path], "size": size, "skip": skip,
                     "duplicate_of": os.path.relpath(keeper.get(digest, path), args.images)})

    needs_inference = any(item["kind"] == "tag" for item in plan)
    tagger = None
    categories = load_categories(args.tagger)
    if needs_inference:
        factory = tagger_factory or Tagger
        try:
            tagger = factory(args.tagger)
            categories = {name: cat for name, cat in getattr(tagger, "tags", [])} or categories
        except SystemExit as exc:
            if args.mode == "retag":
                raise
            problems.append("tagger unavailable (%s); those images were left uncaptioned" % exc)
            for item in plan:
                if item["kind"] == "tag":
                    item["kind"] = "missing-caption"

    entries: list[dict] = []
    unclassified_seen: set[str] = set()
    for item in plan:
        path, rel = item["path"], item["rel"]
        previous = item["previous"] or {}
        entry = {"image": os.path.basename(path), "source_path": path,
                 "out_rel": rel, "out_caption": item["out_caption"],
                 "caption": "", "tags": [], "origin": "", "notes": [],
                 "status": "ok", "source_sha256": item["sha"],
                 "source_caption_sha256": item["caption_sha"], "unclassified": [],
                 "width": item["size"][0], "height": item["size"][1]}

        if not item["readable"][0]:
            entry["status"] = "unreadable"
            entry["notes"].append(item["readable"][1])
            problems.append("%s: %s" % (entry["image"], item["readable"][1]))
            entries.append(entry)
            continue

        if item["skip"] == "duplicate":
            entry["status"] = "duplicate"
            entry["notes"].append("same bytes as %s" % item["duplicate_of"])
            entries.append(entry)
            continue
        if item["skip"] == "too-small":
            entry["status"] = "too-small"
            entry["notes"].append("%dx%d is under --min-side %d" % (item["size"] + (args.min_side,)))
            entries.append(entry)
            continue

        if item["kind"] == "resumed":
            tags, cap_problems = read_caption_file(os.path.join(args.out, item["out_caption"]))
            entry.update({"tags": tags, "origin": "resumed", "notes": cap_problems,
                          "caption": ", ".join(tags)})
            if not tags:
                entry["status"] = "empty-caption"
                problems.append("%s: the resumed caption in the output folder is empty" % entry["image"])
            entries.append(entry)
            continue

        if item["kind"] == "conflict":
            # Your caption, a source that moved under it: keep the work, say so, ship nothing.
            kept_tags, _ = read_caption_file(os.path.join(args.out, item["out_caption"]))
            entry.update({"tags": kept_tags, "origin": "grabber",
                          "caption": ", ".join(kept_tags), "status": "conflict"})
            entry["notes"].append("you edited this caption and its source changed too")
            problems.append(
                "%s: you edited this caption and its source changed too; kept your caption, "
                "excluded from the archive - resolve and rerun" % entry["image"])
            entries.append(entry)
            continue

        if item["kind"] == "resolved":
            # You edited a conflicted caption, which is the one way to settle it by hand.
            tags, _ = read_caption_file(os.path.join(args.out, item["out_caption"]))
            entry.update({"tags": tags, "origin": "manual", "caption": ", ".join(tags)})
            entry["notes"].append("you resolved an earlier conflict")
            entries.append(entry)
            continue

        if previous:
            if previous.get("source_sha256") != item["sha"]:
                entry["notes"].append("source image changed since the last run")
            if previous.get("source_caption_sha256") != item["caption_sha"]:
                entry["notes"].append("source caption changed since the last run")
            if previous.get("status") != "ok":
                entry["notes"].append("rebuilt after an earlier failure (%s)"
                                      % previous.get("status"))

        existing_tags: list[str] = []
        kinds: dict[str, str] = {}
        if item["has_txt"]:
            existing_tags, cap_problems, kinds = read_caption_detail(item["txt"])
            entry["notes"].extend(cap_problems)
            problems.extend("%s: %s" % (os.path.basename(item["txt"]), p) for p in cap_problems)

        if item["kind"] == "missing-caption":
            entry["status"] = "missing-caption"
            problems.append("%s: no caption (convert mode does not invent one)" % entry["image"])
            entries.append(entry)
            continue

        if item["kind"] == "tag" and tagger is None:
            entry["status"] = "missing-caption"
            problems.append("%s: caption needs regenerating but no tagger is loaded" % entry["image"])
            entries.append(entry)
            continue

        if item["kind"] == "tag":
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

        tags, refine_notes = refine_tags(tags, kinds, args)
        entry["notes"].extend(refine_notes)
        tags, unclassified = apply_policy(tags, categories, args.type, args.trigger,
                                          args.drop_artists, args.concept)
        if args.order == "anima":
            tags = order_tags(tags, kinds, categories)
        entry["unclassified"] = unclassified
        if unclassified:
            unclassified_seen.update(unclassified)
        caption = compose_caption(tags, args.type, args.trigger, args.concept, args.append_concept)
        entry.update({"tags": tags, "caption": caption, "origin": origin})
        if not caption:
            entry["status"] = "empty-caption"
            problems.append("%s: caption ended up empty" % entry["image"])
        entries.append(entry)

    # Every entry that claims success is checked here, once. Images were decoded up
    # front; a manually resolved conflict is not proof that its caption is usable.
    for entry in entries:
        if entry["status"] != "ok":
            continue
        if not entry["caption"].strip():
            entry["status"] = "empty-caption"
            problems.append("%s: caption is empty" % entry["image"])

    if unclassified_seen:
        problems.append(
            "style mode could not classify %d tag(s) (%s); they were kept and listed in the review file"
            % (len(unclassified_seen), ", ".join(sorted(unclassified_seen)[:8])))
    return entries, problems


def write_dataset(entries: list[dict], args) -> tuple[list[str], list[str]]:
    """Copy images and write converted captions into the output folder.

    A destination is refreshed whenever its bytes no longer match the source, so
    `--force` after editing an image cannot leave the old picture next to the new
    caption. Writes go through a temporary file and a replace, so an interrupted run
    never leaves a half-written pair behind.
    """
    written, problems = [], []
    for entry in entries:
        if entry["status"] != "ok":
            continue
        image_out = os.path.join(args.out, entry["out_rel"])
        caption_out = os.path.join(args.out, entry["out_caption"])
        os.makedirs(os.path.dirname(image_out) or args.out, exist_ok=True)
        try:
            stale = True
            if os.path.exists(image_out):
                try:
                    stale = sha256_of(image_out) != entry["source_sha256"]
                except OSError:
                    stale = True
            if stale:
                if os.path.exists(image_out):
                    entry["notes"].append("image refreshed from source")
                shutil.copy2(entry["source_path"], image_out + ".part")
                os.replace(image_out + ".part", image_out)
            with open(caption_out + ".part", "w", encoding="utf-8", newline="\n") as f:
                f.write(entry["caption"] + "\n")
            os.replace(caption_out + ".part", caption_out)
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
    small = [e for e in ok if e.get("width") and min(e["width"], e["height"]) < 768]
    if small:
        warnings.append("%d image(s) are under 768 px on the short side; they train soft "
                        "(pass --min-side 768 to leave them out)" % len(small))
    return warnings


def size_summary(entries: list[dict]) -> dict:
    sides = sorted(min(e["width"], e["height"]) for e in entries if e.get("width"))
    if not sides:
        return {}
    shape = {"portrait": 0, "square": 0, "landscape": 0}
    for e in entries:
        if not e.get("width"):
            continue
        ratio = e["width"] / e["height"]
        shape["square" if 0.9 <= ratio <= 1.1 else "portrait" if ratio < 0.9 else "landscape"] += 1
    return {"short_side_min": sides[0], "short_side_median": sides[len(sides) // 2],
            "short_side_max": sides[-1], "under_768": sum(1 for v in sides if v < 768),
            "under_1024": sum(1 for v in sides if v < 1024), "shape": shape}


def build_manifest(args, entries: list[dict], weights_sha: str | None = None) -> dict:
    ok = [e for e in entries if e["status"] == "ok"]
    layout = getattr(args, "zip_layout", "flat")
    prefix = ""
    if layout == "kohya":
        prefix = "%d_%s/" % (max(1, int(getattr(args, "repeats", 1))),
                             re.sub(r"[^A-Za-z0-9 _-]+", "", normalize_tag(args.trigger) or "concept"))
    names = {} if layout == "tree" else flat_names([e["out_rel"] for e in ok], prefix)
    for entry in entries:
        zip_image = names.get(entry["out_rel"], entry["out_rel"]).replace(os.sep, "/")
        entry["zip_image"] = zip_image
        entry["zip_caption"] = os.path.splitext(zip_image)[0] + ".txt"
    excluded: dict[str, int] = {}
    for entry in entries:
        if entry["status"] != "ok":
            excluded[entry["status"]] = excluded.get(entry["status"], 0) + 1
    return {
        "tool": "comfy-forge anima_dataset",
        "manifest_version": 2,
        "zip_layout": layout,
        "excluded": excluded,
        "sizes": size_summary(ok),
        "mode": args.mode,
        "type": args.type,
        "trigger": args.trigger,
        "concept": args.concept,
        "append_concept": bool(args.append_concept),
        "tagger": args.tagger if args.mode in ("auto", "retag") else None,
        "tagger_weights_sha256": weights_sha,
        "settings": config_signature(args),
        "threshold": args.threshold,
        "character_threshold": args.character_threshold,
        "max_tags": args.max_tags,
        "images_seen": len(entries),
        "images_exported": len(ok),
        "sources": {s: sum(1 for e in ok if e["origin"] == s)
                    for s in ("grabber", "tagger", "grabber+tagger", "resumed", "manual")},
        "entries": [{"image": e["image"], "out_image": e["out_rel"],
                     "out_caption": e["out_caption"], "caption": e["caption"],
                     "tags": e["tags"], "origin": e["origin"], "status": e["status"],
                     "notes": e["notes"], "source_sha256": e["source_sha256"],
                     "source_caption_sha256": e["source_caption_sha256"],
                     "unclassified": e.get("unclassified", []),
                     "width": e.get("width", 0), "height": e.get("height", 0),
                     "zip_image": e["zip_image"], "zip_caption": e["zip_caption"]}
                    for e in entries],
        "warnings": dataset_warnings(entries),
    }


def export_zip(out_dir: str, manifest: dict, zip_path: str) -> str:
    """Archive each image with the caption the manifest says belongs to it."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest["entries"]:
            if entry["status"] != "ok":
                continue
            zf.write(os.path.join(out_dir, entry["out_image"]),
                     entry.get("zip_image") or entry["out_image"])
            zf.writestr(entry.get("zip_caption") or entry["out_caption"], entry["caption"] + "\n")
        if manifest.get("trigger"):
            zf.writestr("trigger.txt", manifest["trigger"] + "\n")
        zf.writestr("dataset-manifest.json", json.dumps(manifest, indent=2))
    return zip_path


def verify_zip(zip_path: str, manifest: dict) -> list[str]:
    """Reopen the archive and check every pair against the manifest.

    Captions are compared as text and images by hash: a file with the right name and
    the wrong bytes is exactly the failure a name check cannot see.
    """
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for entry in manifest["entries"]:
            if entry["status"] != "ok":
                continue
            image_name = (entry.get("zip_image") or entry["out_image"]).replace("\\", "/")
            caption_name = (entry.get("zip_caption") or entry["out_caption"]).replace("\\", "/")
            if image_name not in names:
                problems.append("missing image in archive: " + image_name)
            else:
                digest = hashlib.sha256(zf.read(image_name)).hexdigest()
                if digest != entry["source_sha256"]:
                    problems.append("image bytes differ from the source: " + image_name)
            if caption_name not in names:
                problems.append("missing caption in archive: " + caption_name)
                continue
            body = zf.read(caption_name).decode("utf-8").strip()
            if body != entry["caption"].strip():
                problems.append("caption mismatch in archive: " + caption_name)
    return problems


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ inspect

def inspect_folder(folder: str, near: bool = True, near_limit: int = 4000) -> dict:
    """Read-only report on a source folder. Decides nothing and writes nothing."""
    images = list_images(folder)
    by_hash: dict[str, list[str]] = {}
    unreadable, missing_caption, sizes = [], [], []
    tag_counts: dict[str, int] = {}
    namespaces: dict[str, int] = {}
    bookkeeping: dict[str, int] = {}
    caption_problems, thin = [], []
    hashes: list[tuple[str, int]] = []
    captioned = 0
    for path in images:
        rel = os.path.relpath(path, folder)
        ok, why = image_is_readable(path)
        if not ok:
            unreadable.append({"image": rel, "error": why})
            continue
        by_hash.setdefault(sha256_of(path), []).append(rel)
        width, height = image_dimensions(path)
        if width:
            sizes.append({"image": rel, "width": width, "height": height})
        if near and len(images) <= near_limit:
            value = dhash(path)
            if value is not None:
                hashes.append((rel, value))
        txt = os.path.splitext(path)[0] + ".txt"
        if not os.path.isfile(txt):
            missing_caption.append(rel)
            continue
        captioned += 1
        tags, problems, kinds = read_caption_detail(txt)
        for problem in problems:
            caption_problems.append({"caption": os.path.relpath(txt, folder), "problem": problem})
        if 0 < len(tags) < 4:
            thin.append(rel)
        for kind in kinds.values():
            namespaces[kind] = namespaces.get(kind, 0) + 1
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if is_bookkeeping(tag):
                bookkeeping[tag] = bookkeeping.get(tag, 0) + 1

    image_stems = {os.path.splitext(path)[0].lower() for path in images}
    orphans = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.isfile(os.path.join(root, d, "dataset-manifest.json"))]
        for name in sorted(files):
            if name.lower().endswith(".txt"):
                full = os.path.join(root, name)
                if os.path.splitext(full)[0].lower() not in image_stems:
                    orphans.append(os.path.relpath(full, folder))

    duplicates = [group for group in by_hash.values() if len(group) > 1]
    exact = {name for group in duplicates for name in group}
    near_pairs = []
    if hashes:
        for i in range(len(hashes)):
            for j in range(i + 1, len(hashes)):
                distance = bin(hashes[i][1] ^ hashes[j][1]).count("1")
                if distance <= 4 and not (hashes[i][0] in exact and hashes[j][0] in exact):
                    near_pairs.append({"a": hashes[i][0], "b": hashes[j][0], "distance": distance})
    entries = [{"width": s["width"], "height": s["height"]} for s in sizes]
    usable = len(images) - len(unreadable)
    common = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    baked = [tag for tag, count in common if captioned >= 5 and count >= captioned * 0.8]

    mode = "convert" if not missing_caption else "auto"
    command = 'python TOOLS/anima_dataset.py --images "%s" --mode %s --trigger <token> --zip <name>.zip' % (folder, mode)
    advice = []
    if missing_caption:
        advice.append("%d image(s) have no caption: --mode auto tags them (needs the tagger), "
                      "--mode convert leaves them out and says so" % len(missing_caption))
    if duplicates:
        advice.append("%d exact duplicate group(s) are left out automatically" % len(duplicates))
    if near_pairs:
        advice.append("%d near-duplicate pair(s): keep the better of each by hand" % len(near_pairs))
    small = sum(1 for s in sizes if min(s["width"], s["height"]) < 768)
    if small:
        advice.append("%d image(s) are under 768 px on the short side: add --min-side 768 to leave them out" % small)
    if bookkeeping:
        advice.append("post-bookkeeping tags (%s) are dropped automatically" % ", ".join(sorted(bookkeeping)[:6]))
    if baked:
        advice.append("in at least 80%% of captions: %s. If one of these is the concept itself, "
                      "leave it; if not, --exclude it or it gets baked in" % ", ".join(baked[:8]))
    return {
        "schema": 1, "folder": os.path.abspath(folder),
        "images": len(images), "usable": usable, "captioned": captioned,
        "missing_caption": missing_caption, "orphan_captions": orphans,
        "unreadable": unreadable, "caption_problems": caption_problems,
        "exact_duplicates": duplicates,
        "near_duplicates": near_pairs if hashes else None,
        "sizes": size_summary(entries), "thin_captions": thin,
        "tags": {"unique": len(tag_counts), "top": [{"tag": t, "count": c} for t, c in common[:30]],
                 "in_most_captions": baked, "declared_kinds": namespaces,
                 "bookkeeping": bookkeeping},
        "advice": advice, "next": command,
    }


def print_inspect(report: dict) -> None:
    print("%s" % report["folder"])
    print("  images %d, usable %d, captioned %d, no caption %d, unreadable %d"
          % (report["images"], report["usable"], report["captioned"],
             len(report["missing_caption"]), len(report["unreadable"])))
    if report["orphan_captions"]:
        print("  captions with no image: %d" % len(report["orphan_captions"]))
    sizes = report["sizes"]
    if sizes:
        print("  short side min/median/max %d/%d/%d, under 768: %d, under 1024: %d"
              % (sizes["short_side_min"], sizes["short_side_median"], sizes["short_side_max"],
                 sizes["under_768"], sizes["under_1024"]))
        print("  shape " + ", ".join("%s %d" % kv for kv in sizes["shape"].items()))
    print("  exact duplicate groups %d, near-duplicate pairs %s"
          % (len(report["exact_duplicates"]),
             "not checked" if report["near_duplicates"] is None else len(report["near_duplicates"])))
    print("  unique tags %d; top: %s" % (report["tags"]["unique"],
          ", ".join("%s (%d)" % (t["tag"], t["count"]) for t in report["tags"]["top"][:10])))
    for line in report["advice"]:
        print("  - " + line)
    print("next: " + report["next"])


# ----------------------------------------------------------------- validate

def validate_dataset(path: str, trigger: str = "") -> dict:
    """Check a finished dataset, as a folder or as the archive that gets uploaded.

    Every image needs one non-empty caption with the same name, every caption needs
    its image, and when a manifest is present every image must still be the file the
    manifest recorded. Nothing is repaired.
    """
    import io

    problems: list[str] = []
    files: dict[str, object] = {}
    is_zip = os.path.isfile(path)
    if is_zip:
        try:
            archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            return {"schema": 1, "path": os.path.abspath(path), "kind": "archive", "pairs": 0,
                    "status": "FAIL", "problems": ["not a readable zip: %s" % exc]}
        bad = archive.testzip()
        if bad:
            problems.append("corrupt member in archive: " + bad)
        for name in archive.namelist():
            if not name.endswith("/"):
                files[name] = name
        read = archive.read
    elif os.path.isdir(path):
        archive = None
        for root, _dirs, names in os.walk(path):
            for name in names:
                full = os.path.join(root, name)
                files[os.path.relpath(full, path).replace(os.sep, "/")] = full

        def read(key):
            with open(files[key], "rb") as handle:
                return handle.read()
    else:
        return {"schema": 1, "path": os.path.abspath(path), "kind": "missing", "pairs": 0,
                "status": "FAIL", "problems": ["no such folder or archive"]}

    manifest = None
    if "dataset-manifest.json" in files:
        try:
            manifest = json.loads(read("dataset-manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            problems.append("dataset-manifest.json does not parse: %s" % exc)
    trigger = normalize_tag(trigger or (manifest or {}).get("trigger") or "")

    images = sorted(n for n in files if os.path.splitext(n)[1].lower() in IMAGE_EXT)
    captions = {n for n in files if n.lower().endswith(".txt")
                and os.path.basename(n).lower() not in ("trigger.txt",)}
    folders = {os.path.dirname(n) for n in images}
    if is_zip and len(folders) > 1:
        problems.append("images sit in %d different folders; an upload trainer expects the pairs "
                        "together" % len(folders))
    seen_stems: dict[str, str] = {}
    hashes: dict[str, str] = {}
    pairs = 0
    for name in images:
        stem = os.path.splitext(name)[0]
        if stem.lower() in seen_stems:
            problems.append("%s and %s share one caption name" % (seen_stems[stem.lower()], name))
        seen_stems[stem.lower()] = name
        data = read(name)
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            problems.append("%s is a byte-for-byte duplicate of %s" % (name, hashes[digest]))
        hashes.setdefault(digest, name)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            problems.append("%s does not decode: %s" % (name, exc))
        caption_name = stem + ".txt"
        if caption_name not in captions:
            problems.append("%s has no caption" % name)
            continue
        text = read(caption_name).decode("utf-8", errors="replace").strip()
        if not text:
            problems.append("%s is empty" % caption_name)
            continue
        if trigger and normalize_tag(text.split(",")[0]) != trigger:
            problems.append("%s does not start with the trigger '%s'" % (caption_name, trigger))
        pairs += 1
    stems = {os.path.splitext(n)[0] for n in images}
    for name in sorted(captions):
        if os.path.splitext(name)[0] not in stems and os.path.basename(name) != "_review.tsv":
            problems.append("%s has no image" % name)

    if manifest:
        key = "zip_image" if is_zip else "out_image"
        recorded = [e for e in manifest.get("entries", []) if e.get("status") == "ok"]
        for entry in recorded:
            name = (entry.get(key) or entry.get("out_image") or "").replace("\\", "/")
            if name not in files:
                problems.append("manifest lists %s but it is not here" % name)
            elif hashlib.sha256(read(name)).hexdigest() != entry.get("source_sha256"):
                problems.append("%s is not the image the manifest recorded" % name)
        if len(recorded) != len(images):
            problems.append("manifest records %d pair(s), found %d image(s)" % (len(recorded), len(images)))
    if not images:
        problems.append("no images found")
    if archive is not None:
        archive.close()
    return {"schema": 1, "path": os.path.abspath(path), "kind": "archive" if is_zip else "folder",
            "pairs": pairs, "images": len(images), "trigger": trigger or None,
            "manifest": bool(manifest), "status": "FAIL" if problems else "PASS",
            "problems": problems}


# --------------------------------------------------------------------- main

def main_inspect(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="anima_dataset.py inspect",
                                 description="Report on a source folder. Writes nothing.")
    ap.add_argument("--images", required=True)
    ap.add_argument("--no-near", action="store_true", help="skip the near-duplicate pass")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.images):
        raise SystemExit("not a folder: " + args.images)
    report = inspect_folder(args.images, near=not args.no_near)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_inspect(report)
    return 0 if report["usable"] else 2


def main_validate(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="anima_dataset.py validate",
                                 description="Check a dataset folder or archive. Changes nothing.")
    ap.add_argument("path", help="dataset folder or .zip")
    ap.add_argument("--trigger", default="", help="expect every caption to start with this")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = validate_dataset(args.path, args.trigger)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("%s  %s  %d pair(s)" % (report["status"], report["path"], report["pairs"]))
        for problem in report["problems"]:
            print("  - " + problem)
    return 0 if report["status"] == "PASS" else 2


def main(argv: list[str] | None = None, tagger_factory=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "inspect":
        return main_inspect(argv[1:])
    if argv and argv[0] == "validate":
        return main_validate(argv[1:])
    if argv and argv[0] == "build":
        argv = argv[1:]
    ap = argparse.ArgumentParser(
        description="Build an Anima LoRA dataset from a folder.",
        epilog="Other commands: `inspect --images DIR` reports on a source folder; "
               "`validate PATH` checks a finished folder or archive. "
               "Exit codes: 0 clean, 1 archive does not match, 2 something was left out or failed.")
    ap.add_argument("--images", required=True, help="source folder (read-only)")
    ap.add_argument("--out", default=None,
                    help="output dataset folder (default: <images>/anima-dataset)")
    ap.add_argument("--mode", default="auto", choices=MODES,
                    help="auto: use existing .txt, tag what has none; convert: existing only; "
                         "retag: tagger merges into existing tags")
    ap.add_argument("--type", default="character", choices=TYPES)
    ap.add_argument("--trigger", default="",
                    help="caption token for the concept; used for every --type, style included")
    ap.add_argument("--concept", default="", help="one sentence describing the shared idea")
    ap.add_argument("--append-concept", action="store_true",
                    help="add the concept sentence to every caption")
    ap.add_argument("--tagger", default="eva02", choices=sorted(MODELS))
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--character-threshold", type=float, default=0.50)
    ap.add_argument("--max-tags", type=int, default=40)
    ap.add_argument("--exclude", default="", help="comma-separated tags to drop")
    ap.add_argument("--drop-artists", action="store_true")
    ap.add_argument("--order", default="anima", choices=ORDERS,
                    help="anima: quality, safety, count, character, series, artist, general; "
                         "source: keep the order the tags came in")
    ap.add_argument("--keep-bookkeeping", action="store_true",
                    help="keep post-bookkeeping tags such as 'commentary request' and 'bad id'")
    ap.add_argument("--drop-rating", action="store_true",
                    help="drop the safety tag made from a rating: prefix")
    ap.add_argument("--min-side", type=int, default=0,
                    help="leave out images whose short side is under this many pixels")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="keep byte-identical images instead of exporting only the first")
    ap.add_argument("--resume", action="store_true", help="keep captions already in --out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="write into a non-empty --out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--zip", dest="zip_path", default=None, help="write and verify an upload archive")
    ap.add_argument("--zip-layout", default="flat", choices=ZIP_LAYOUTS,
                    help="flat: every pair at the archive root (Civitai upload); tree: keep source "
                         "folders; kohya: one '<repeats>_<trigger>' folder")
    ap.add_argument("--repeats", type=int, default=10, help="repeat count for --zip-layout kohya")
    ap.add_argument("--json", action="store_true",
                    help="print one JSON result on stdout; the readable report goes to stderr")
    args = ap.parse_args(argv)
    args.exclude = {normalize_tag(t) for t in args.exclude.split(",") if t.strip()}
    say = (lambda *a: print(*a, file=sys.stderr)) if args.json else print

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

    def result(code: int, **extra) -> int:
        if args.json:
            print(json.dumps({
                "schema": 1, "exit": code, "out": os.path.abspath(args.out),
                "dry_run": bool(args.dry_run), "images_seen": manifest["images_seen"],
                "images_exported": manifest["images_exported"], "excluded": manifest["excluded"],
                "sources": manifest["sources"], "sizes": manifest["sizes"],
                "warnings": manifest["warnings"], "problems": problems, **extra,
            }, indent=2, ensure_ascii=False))
        return code

    if args.dry_run:
        for entry in entries:
            say("%-44s %-16s %s" % (entry["out_rel"], entry["status"], entry["caption"][:70]))
        say("\n%d seen, %d exportable, nothing written (--dry-run)"
            % (len(entries), manifest["images_exported"]))
        return result(0, captions=[{"image": e["out_rel"], "status": e["status"],
                                    "caption": e["caption"]} for e in entries])

    written, write_problems = write_dataset(entries, args)
    problems.extend(write_problems)
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "dataset-manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    review_path = write_review(entries, args.out)

    if manifest["warnings"]:
        say("Warnings:")
        for warning in manifest["warnings"]:
            say("  -", warning)

    archive = None
    if args.zip_path:
        export_zip(args.out, manifest, args.zip_path)
        zip_problems = verify_zip(args.zip_path, manifest)
        if zip_problems:
            say("archive does NOT match the dataset:")
            for problem in zip_problems:
                say("  -", problem)
            problems.extend(zip_problems)
            return result(1, archive={"path": os.path.abspath(args.zip_path), "verified": False})
        archive = {"path": os.path.abspath(args.zip_path), "layout": args.zip_layout,
                   "verified": bool(manifest["images_exported"]),
                   "pairs": manifest["images_exported"]}
        if manifest["images_exported"]:
            say("archive verified:", args.zip_path)
        else:
            say("archive written but holds no usable pairs:", args.zip_path)

    say("\n%d seen, %d exported to %s" % (len(entries), len(written), args.out))
    if manifest["excluded"]:
        say("left out: " + ", ".join("%s %d" % kv for kv in sorted(manifest["excluded"].items())))
    say("review:", review_path)
    extra = {"manifest": manifest_path, "review": review_path, "archive": archive}
    # An exact duplicate that was left out is the tool doing its job, not a failure.
    if problems:
        say("\n%d problem(s):" % len(problems))
        for problem in problems[:20]:
            say("  -", problem)
        return result(2, **extra)
    return result(0, **extra)


if __name__ == "__main__":
    sys.exit(main())
