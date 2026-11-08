# Building an Anima LoRA dataset from a folder

`TOOLS/anima_dataset.py` turns a folder of images — usually an imgbrd-grabber export —
into a training dataset for Anima. It never trains and never uploads. Source images and
their `.txt` captions are **read-only**; everything it produces lands in a new folder.

## The short version

```
python TOOLS/anima_dataset.py --images "S:\my\shots" --out "S:\my\shots-dataset" ^
  --trigger mychar --concept "an original anime illustration" --zip mychar.zip
```

- If an image already has a `.txt` beside it (grabber output), that caption is converted.
- If it has none, the tagger writes one.
- Output: copies of the images, one converted `.txt` each, `_review.tsv`, and
  `dataset-manifest.json`. `--zip` adds a verified Civitai archive.

## Install the tagger (once, only needed for uncaptioned images)

```
powershell -File TOOLS/Install-Tagger.ps1              # eva02, best quality, ~1.2 GB
powershell -File TOOLS/Install-Tagger.ps1 -Name swinv2  # half the download, slightly weaker
```

Apache-2.0 weights (SmilingWolf WD14 v3), stored in `%LOCALAPPDATA%\comfy-kit\taggers`.
Nothing is added to ComfyUI's Python environment — inference runs on the ONNX Runtime it
already ships. **Convert mode needs none of this.**

## Modes

| mode | what it does | needs tagger |
|---|---|---|
| `auto` (default) | converts existing `.txt`; tags the images that have none | only if captions are missing |
| `convert` | existing captions only; reports every image that has none | no |
| `retag` | tagger adds to existing tags as a baseline; nothing is dropped silently | yes |

`retag` keeps your tags and appends new predictions, recording how many it added. It never
replaces a tag you already have.

## Concept types

`--type` changes which tags survive, because the four kinds of LoRA need different captions:

| type | caption policy |
|---|---|
| `character` (default) | trigger + every tag; anything that varies is still described |
| `style` | trigger is **not** added; copyright and character tags are dropped so a made-up identity is not forced onto every image |
| `outfit` | trigger + every tag, so the outfit stays fixed while subject, pose and background stay described |
| `object` | same as outfit |

The concept sentence is **not** pasted into every caption by default — that teaches the
LoRA that the sentence is part of every image. Add `--append-concept` if you really want it.

## Tag conversion

Booru tags become Anima caption text: `long_hair` → `long hair`, lowercased, whitespace
collapsed, duplicates removed after normalisation, and `score_7` keeps its underscore
because that is the exception Anima expects. Commas and newlines both split a caption file.

## Review before you train

`_review.tsv` opens in any spreadsheet: image, where the caption came from (`grabber`,
`tagger`, `grabber+tagger`, `resumed`), tag count, the final caption, and notes. Edit the
caption in the output folder and re-run with `--resume` — your edit is kept.

## The archive

`--zip` writes images and their captions, `trigger.txt`, and the manifest. It is then
**reopened and verified against the manifest** — every pair must exist and every caption
must match, or the run fails instead of handing you a broken archive.

## Options that matter

| flag | why |
|---|---|
| `--out` | output folder; defaults to `<images>/anima-dataset` and must differ from `--images` |
| `--mode` | `auto`, `convert`, `retag` |
| `--type` | `character`, `style`, `outfit`, `object` |
| `--threshold` | general tag cutoff, default 0.35 |
| `--character-threshold` | copyright/character cutoff, default 0.50 |
| `--max-tags` | cap per caption, default 40 |
| `--exclude` | tags to strip, e.g. `watermark,signature` |
| `--drop-artists` | remove artist tags (artist LoRA bleed) |
| `--link` | hard-link images instead of copying (same volume only) |
| `--resume` | keep captions already in the output folder and record them |
| `--force` | write into a non-empty output folder |
| `--dry-run` | print the plan, write nothing |
| `--zip` | write and verify the Civitai archive |

## What it warns you about

- **A tag in ~every caption** (`1girl`, `long hair`, the same background): training bakes
  it in and the LoRA stops responding to prompts. Fix the captions or `--exclude` it.
- **Captions with almost no tags** — usually a flat or cropped image.
- **Images with no confident tags** — probably not the image you thought.
- **Skipped images** — unreadable or missing captions; a partial run exits non-zero rather
  than claiming success.

## Limits worth knowing

- The tagger is a **booru tagger**: it describes what it sees in danbooru vocabulary. It
  does not know your character's name or which details are the point. It gets you to 90%.
- WD14 v3 identifies style and content, not identity. Two different characters with the
  same tags get the same caption; add the distinguishing tags by hand.
- `style` mode can only drop character/copyright tags when a tagger supplied the
  categories. In `convert` mode no category data exists, so nothing is filtered by type —
  check `_review.tsv`.
- No near-duplicate detection, no cropping, no upscaling.
- `eva02` is more accurate and roughly 2× slower than `swinv2`; both are about a second
  per image on a 4080 and a few seconds on CPU.
