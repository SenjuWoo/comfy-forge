# Captioning a dataset for Anima LoRA training

`TOOLS/anima_dataset.py` turns a folder of untagged images into training captions for
Anima, without training anything itself. Training happens on Civitai; this produces
the captions that training reads.

## Install a tagger (once)

```
powershell -File TOOLS/Install-Tagger.ps1              # eva02, best quality, ~1.2 GB
powershell -File TOOLS/Install-Tagger.ps1 -Name swinv2  # half the download, slightly weaker
```

Weights are Apache-2.0 (SmilingWolf WD14 v3) and land in
`%LOCALAPPDATA%\comfy-kit\taggers`, outside the repository. Nothing is added to
ComfyUI's environment: the tagger runs on the ONNX Runtime ComfyUI already ships.

## Caption a folder

```
python TOOLS/anima_dataset.py ^
  --images "S:\path\to\untagged" ^
  --trigger woomixchar ^
  --concept "an original anime illustration, clean lineart" ^
  --tagger eva02
```

Each image gets a matching `.txt` beside it:

```
woomixchar, an original anime illustration, clean lineart, 1girl, solo, red eyes,
school uniform, cherry blossoms, long hair, upper body, white hair, outdoors
```

Trigger first (the token the LoRA learns), then your concept sentence, then tags sorted
by confidence. That order matches how Anima reads a caption through its Qwen encoder.

## Options that matter

| flag | why you would use it |
|---|---|
| `--trigger` | the unique word for the concept; empty means tags only |
| `--concept` | one sentence describing what every image shares |
| `--threshold` | general tag cutoff, default 0.35; raise it for a cleaner, shorter caption |
| `--character-threshold` | cutoff for copyright/character tags, default 0.50 |
| `--max-tags` | cap per caption, default 40 |
| `--exclude` | comma-separated tags to strip, e.g. `watermark,signature` |
| `--out` | write captions to a separate folder instead of beside the images |
| `--resume` | skip images that already have a caption |
| `--dry-run` | print captions and write nothing |
| `--zip out.zip` | also build a Civitai-ready archive: images, captions, `trigger.txt`, manifest |

## What it warns you about

The manifest lists warnings, and they are the ones that quietly ruin a LoRA:

- **A tag in almost every caption** (`1girl`, `looking at viewer`, your background) —
  if it is not true of the concept, training bakes it in and the LoRA stops responding
  to prompts. Fix by editing those captions or re-running with `--exclude`.
- **Captions with almost no tags** — usually a flat or cropped image; caption it by hand.
- **No confidence at all** — the image probably is not what you think it is.

## Civitai training with the archive

1. `--zip` produces `out.zip` with each image next to its `.txt`.
2. Upload that zip in Civitai's LoRA trainer, set the trigger word from `trigger.txt`.
3. Use the concept sentence from your manifest as the sample prompt during training.

Nothing is uploaded by this tool. The archive is a local file you hand over yourself.

## Limits worth knowing

- The tagger is a **booru tagger**: it describes what it sees in danbooru vocabulary.
  It does not know your character's name, backstory, or which details are the point.
  Edit the captions that matter; the tool gets you to 90%, not 100%.
- It reads one image at a time; no batch cropping, no upscaling, no dedupe.
- WD14 v3 identifies style and content, not identity. Two different characters with
  the same tag set produce the same caption — add distinguishing tags by hand.
- The `eva02` model is more accurate and ~2× slower than `swinv2`; both run in about a
  second per image on a 4080 and a few seconds on CPU.
