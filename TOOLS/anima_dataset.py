"""Caption a folder of images for Anima LoRA training on Civitai.

Point it at a folder and a concept; it writes a .txt next to every image with the
trigger word, your concept phrase, and the booru tags a WD14 v3 tagger is confident
about. No training happens here and nothing is uploaded.

The tagger runs on ONNX Runtime, which ComfyUI already ships, so this adds no
dependency to your install. Weights are Apache-2.0 and live outside the repository.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import zipfile

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# WD14 v3 category column: 0 general, 3 copyright, 4 character, 9 rating.
KEEP_CATEGORIES = {0, 3, 4}
MODELS = {
    # name: (repo, input side) - the repo path matches TOOLS/Install-Tagger.ps1
    "eva02": ("SmilingWolf/wd-eva02-large-tagger-v3", 448),
    "swinv2": ("SmilingWolf/wd-swinv2-tagger-v3", 448),
}


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
    """WD14 v3 tagger on ONNX Runtime. Loaded lazily so tests never need weights."""

    def __init__(self, name: str = "eva02", providers: list[str] | None = None):
        import onnxruntime as ort
        from PIL import Image  # noqa: F401  (import here so --help works without PIL)

        onnx_path, csv_path = find_model_files(name)
        if not os.path.isfile(onnx_path):
            raise SystemExit(
                "tagger weights not found: %s\nrun: image-stack.ps1 taggers install %s" % (onnx_path, name))
        self.name = name
        self.session = ort.InferenceSession(
            onnx_path, providers=providers or ["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.side = int(shape[1]) if isinstance(shape[1], int) else MODELS[name][1]
        self.tags = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.tags.append((row["name"], int(row["category"])))

    def predict(self, path: str) -> dict[str, float]:
        import numpy as np
        from PIL import Image

        img = Image.open(path).convert("RGB")
        w, h = img.size
        side = max(w, h)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(img, ((side - w) // 2, (side - h) // 2))
        canvas = canvas.resize((self.side, self.side), Image.BICUBIC)
        pixels = np.asarray(canvas, dtype="uint8")[:, :, ::-1]   # WD14 reads BGR
        batch = pixels[None, ...]
        # Exports differ on dtype; the value range stays 0-255 either way (scaling it
        # to 0-1 makes this model answer "monochrome, no humans" for a colour photo).
        if "float" in self.session.get_inputs()[0].type:
            batch = batch.astype("float32")
        scores = self.session.run(None, {self.input_name: batch})[0][0]
        return {name: float(score) for (name, _), score in zip(self.tags, scores)}


def iter_images(folder: str):
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if os.path.isfile(path) and os.path.splitext(entry)[1].lower() in IMAGE_EXT:
            yield path


def filter_scores(scores: dict[str, float], categories: dict[str, int],
                  threshold: float, character_threshold: float,
                  exclude: set[str], max_tags: int) -> list[tuple[str, float]]:
    """Tags worth writing: confident, from a useful category, minus the blacklist."""
    keep = []
    for name, score in scores.items():
        category = categories.get(name, 0)
        if category not in KEEP_CATEGORIES:
            continue
        limit = character_threshold if category in (3, 4) else threshold
        if score < limit:
            continue
        clean = name.replace("_", " ").strip()
        if clean in exclude or name in exclude:
            continue
        keep.append((clean, score))
    keep.sort(key=lambda item: item[1], reverse=True)
    return keep[:max_tags]


def assemble_caption(trigger: str, concept: str, tags: list[tuple[str, float]]) -> str:
    """Trigger first, then the concept sentence, then tags - the order Anima reads best."""
    parts = []
    if trigger:
        parts.append(trigger.strip())
    if concept:
        parts.append(concept.strip().rstrip(","))
    parts.extend(name for name, _ in tags)
    return ", ".join(p for p in parts if p)


def dataset_warnings(entries: list[dict], image_total: int) -> list[str]:
    """Things that quietly ruin a LoRA: leaked tags, empty captions, short captions."""
    warnings = []
    counts: dict[str, int] = {}
    for entry in entries:
        for tag in entry["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    if image_total:
        for tag, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]:
            if count >= max(5, int(image_total * 0.8)):
                warnings.append(
                    "tag '%s' appears in %d of %d captions - if that is not always true, it teaches "
                    "the LoRA to bake it in" % (tag, count, image_total))
    for entry in entries:
        if not entry["tags"]:
            warnings.append("%s got no confident tags - caption it by hand" % entry["file"])
        elif len(entry["tags"]) < 4:
            warnings.append("%s has only %d tags - check it by eye" % (entry["file"], len(entry["tags"])))
    return warnings


def build_manifest(args, entries: list[dict], tagger_name: str, weights_sha: str | None) -> dict:
    return {
        "tool": "comfy-kit anima_dataset",
        "trigger": args.trigger,
        "concept": args.concept,
        "tagger": tagger_name,
        "tagger_weights_sha256": weights_sha,
        "threshold": args.threshold,
        "character_threshold": args.character_threshold,
        "max_tags": args.max_tags,
        "exclude": sorted(args.exclude),
        "images": len(entries),
        "entries": entries,
        "warnings": dataset_warnings(entries, len(entries)),
    }


def write_caption(image_path: str, text: str, out_dir: str | None) -> str:
    target_dir = out_dir or os.path.dirname(image_path)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.splitext(os.path.basename(image_path))[0] + ".txt")
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.strip() + "\n")
    return target


def build_zip(folder: str, manifest: dict, out_zip: str) -> str:
    """One archive with images, captions and the trigger - what Civitai training expects."""
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in iter_images(folder):
            zf.write(path, os.path.basename(path))
            stem = os.path.splitext(os.path.basename(path))[0]
            txt = os.path.join(folder, stem + ".txt")
            if os.path.isfile(txt):
                zf.write(txt, stem + ".txt")
        if manifest.get("trigger"):
            zf.writestr("trigger.txt", manifest["trigger"] + "\n")
        zf.writestr("dataset-manifest.json", json.dumps(manifest, indent=2))
    return out_zip


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main(argv: list[str] | None = None, tagger_factory=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", required=True, help="folder of untagged images")
    ap.add_argument("--trigger", default="", help="unique word the LoRA should learn, e.g. woomixchar")
    ap.add_argument("--concept", default="", help="one sentence describing the shared idea")
    ap.add_argument("--out", default=None, help="write .txt here instead of beside the images")
    ap.add_argument("--tagger", default="eva02", choices=sorted(MODELS), help="eva02 = best, swinv2 = half the download")
    ap.add_argument("--threshold", type=float, default=0.35, help="general tag cutoff")
    ap.add_argument("--character-threshold", type=float, default=0.50, help="copyright/character cutoff")
    ap.add_argument("--max-tags", type=int, default=40)
    ap.add_argument("--exclude", default="", help="comma-separated tags to drop")
    ap.add_argument("--resume", action="store_true", help="skip images that already have a caption")
    ap.add_argument("--dry-run", action="store_true", help="print captions, write nothing")
    ap.add_argument("--limit", type=int, default=0, help="preview N images")
    ap.add_argument("--zip", dest="zip_path", default=None, help="also write a Civitai-ready archive")
    ap.add_argument("--manifest", default=None, help="manifest path (default: <images>/dataset-manifest.json)")
    args = ap.parse_args(argv)
    args.exclude = {t.strip() for t in args.exclude.split(",") if t.strip()}

    if not os.path.isdir(args.images):
        raise SystemExit("not a folder: " + args.images)
    images = list(iter_images(args.images))
    if args.limit:
        images = images[: args.limit]
    if not images:
        raise SystemExit("no images found in " + args.images)

    tagger = None
    factory = tagger_factory
    if factory is None:
        def factory(name):  # noqa: ANN001
            return Tagger(name)
    tagger = factory(args.tagger)
    categories = {name: cat for name, cat in getattr(tagger, "tags", [])}

    entries = []
    for path in images:
        stem = os.path.splitext(os.path.basename(path))[0]
        if args.resume and not args.out:
            existing = os.path.join(os.path.dirname(path), stem + ".txt")
            if os.path.isfile(existing) and os.path.getsize(existing) > 3:
                entries.append({"file": os.path.basename(path), "tags": [], "skipped": True})
                continue
        if tagger is None:
            raise SystemExit("no tagger available")
        scores = tagger.predict(path)
        tags = filter_scores(scores, categories, args.threshold, args.character_threshold,
                             args.exclude, args.max_tags)
        caption = assemble_caption(args.trigger, args.concept, tags)
        entries.append({"file": os.path.basename(path), "tags": [t for t, _ in tags],
                        "caption": caption})
        if args.dry_run:
            print("%s: %s" % (os.path.basename(path), caption))
        else:
            write_caption(path, caption, args.out)

    weights_sha = None
    if tagger is not None and not args.dry_run:
        onnx_path, _ = find_model_files(args.tagger)
        weights_sha = sha256_of(onnx_path) if os.path.isfile(onnx_path) else None
    manifest = build_manifest(args, [e for e in entries if not e.get("skipped")], args.tagger, weights_sha)

    if args.dry_run:
        print("\n%d image(s), nothing written (--dry-run)" % len(entries))
        return 0

    manifest_path = args.manifest or os.path.join(args.images, "dataset-manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if manifest["warnings"]:
        print("\nWarnings:")
        for warning in manifest["warnings"]:
            print("  -", warning)
    if args.zip_path:
        build_zip(args.images, manifest, args.zip_path)
        print("archive:", args.zip_path)
    print("\n%d captioned, manifest %s" % (len(entries), manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
