# Music: ACE-Step 1.5 text-to-music

comfy-forge runs ACE-Step 1.5 through ComfyUI's built-in support: you describe a style,
optionally give lyrics, and get a song file back. It does not train anything and does not
upload anything. Sound effects and game audio production are Asset Forge's job; this is
for full songs and instrumentals.

## Quick start

```powershell
# 1. see what would be downloaded (nothing is fetched yet), then fetch one file, ~9.3 GB
powershell -File TOOLS\Install-MusicModels.ps1 -WhatIf
powershell -File TOOLS\Install-MusicModels.ps1 -Set aio

# 2. start ComfyUI (or SwarmUI's backend), then ask whether it is ready
powershell -File TOOLS\music.ps1 check --variant aio

# 3. make a song
powershell -File TOOLS\music.ps1 generate --tags "lo-fi hip hop, mellow rhodes, soft drums, vinyl crackle" `
  --instrumental --duration 90 --bpm 82 --key "A minor"
```

A download connection that stays under 5 MB/s for 30 s is dropped and resumed on a new one (up to five times, then one last try with no floor so a slow line still finishes); `-MinSpeedMB`, `-SlowSeconds` and `-Reconnects` change that, `-MinSpeedMB 0` turns it off.

The file lands in `out\music\`. The seed is printed; pass `--seed N` to reproduce a take.
`image-stack.ps1 status` ends with a `music` line that lists the variants whose files are on disk.

Use `TOOLS\music.ps1` (or `python TOOLS/music.py`) rather than `image-stack.ps1`: PowerShell
would bind `--out`, `--format` and `--seed` to that script's own parameters.

## Variants

All variants are in `presets/music/` as the stock Comfy-Org templates, and `TOOLS/music.py`
builds the same graph without a UI. Sampler values below are what each template sets.

| `--variant` | files (GB) | steps / cfg | notes |
|---|---|---|---|
| `aio` (default) | one checkpoint, 9.34 | 8 / 1 | simplest install; saves FLAC |
| `turbo` | diffusion 4.46 + encoders 1.11 + 3.45 + VAE 0.31 | 8 / 1 | same turbo model, split files |
| `turbo-4b` | diffusion 4.46 + encoders 1.11 + 7.80 + VAE 0.31 | 8 / 1 | larger text encoder |
| `xl-turbo` | XL diffusion 9.29 + encoders 1.11 + 7.80 + VAE 0.31 | 8 / 1 | XL model, 4B encoder |
| `xl-sft` | XL SFT 9.29 + encoders 1.11 + 7.80 + VAE 0.31 | 50 / 7 | the slow, high-step XL path |
| `xl-base` | XL base 9.29 + encoders 1.11 + 7.80 + VAE 0.31 | 50 / 6 | the slow, high-step XL path |

Sizes are the byte counts published on the Hugging Face repo, in GiB. `python TOOLS/music.py variants`
prints the exact file names. Files shared between variants (VAE, 0.6B encoder) are downloaded once.
Upstream does not publish VRAM requirements for the full pipeline; the Comfy docs only say a
configuration under 4 GB VRAM exists that runs the diffusion model without the language-model
planner. Which variant sounds best has not been judged here: that is for your ears, and the table
says only what each template sets.

## Writing a prompt

* `--tags` is the style caption: genre, mood, instruments, vocals, production. Long, concrete
  sentences work (the shipped templates use whole paragraphs).
* Lyrics go in `--lyrics "..."` or `--lyrics-file song.txt` (UTF-8). Mark sections with
  `[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Outro]` on their own lines, as the
  templates do. `--language` takes the codes the node lists (`en`, `ja`, `zh`, `ko`, `es`, `fr`, ...).
* `--instrumental` sends `[Instrumental]` as the lyrics. That is the upstream ACE-Step convention;
  it is not checked against a render here.
* `--duration` is seconds (1 to 600; the templates use 120). `--bpm`, `--key` (`E minor`,
  `Bb major`) and `--time-signature` (2, 3, 4, 6) steer the plan, they are not hard constraints.
* `--no-audio-codes` skips the language-model planning pass: faster, and the node documents it as lower quality.
* `--steps`, `--cfg`, `--shift` override the variant's values. Leave them alone unless you are comparing.
* `--format flac|mp3|opus` (mp3 defaults to quality V0, opus to 128k). `aio` and `xl-sft` default to FLAC, the rest to MP3.

`python TOOLS/music.py build --tags "..." --out graph.json` writes the ComfyUI API graph without
contacting anything, for an agent or script that submits it itself.

## What it does not do

* **Cover, repaint, extend and stem extraction.** ComfyUI's native support is text-to-music. The
  official launch post said cover and repaint were not yet supported, and these templates contain none.
  Third-party custom nodes claim more; they are a separate, unreviewed install and are not used here.
* **Reference-audio timbre.** ComfyUI has an experimental `Set Reference Audio` node; it is not wired in.
* **The LLM-assisted template** (`audio_ace_step_1_5_split_llm.json`) writes the prompt with a Gemini
  cloud node, which needs an account and sends your text off the machine. It is not shipped.
* Training or LoRA for ACE-Step.

## Verified and not verified

| claim | how it was checked |
|---|---|
| the generator's graph has the template's nodes, wiring, sampler, shift, loader files and encoder defaults | `TESTS/test_music.py`, all six variants, compared against the shipped JSON |
| the installer's URLs and file list equal what the templates ask for | `TESTS/test_music.py` |
| ComfyUI 0.44.0 accepts the graph's node classes and every widget value, for all six variants and all three save formats; it rejected only the model files that are not installed, and it rejected a bad key and a bad `format.quality` | graphs submitted to a headless ComfyUI 0.44.0 and read back from `/prompt` |
| `generate`, `check` and the refusal paths (no backend, file missing, bad arguments) | `TESTS/test_music.py` drives real processes against a stub backend |
| a real backend renders audible music, `aio` | `TESTS/integration_music.py` on an RTX 4080 SUPER, ComfyUI 0.44.0: 30.0 s FLAC at 48 kHz, RMS 0.170, peak 0.992, about 9.5 s end to end; a 75 s vocal take rendered in about 6 s |
| the other five variants render | **not run**: their weights are not installed here |
| download integrity | each file's byte length is checked against the published size; upstream publishes no checksums |
| a stuck download recovers | `TESTS/test_music_install.py`: a stub server trickles the first connection; the installer drops it after `-SlowSeconds`, resumes from the partial file on a new connection and finishes at the exact size. A short file is deleted, an HTTP error stops at once |
