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

# 2. start SwarmUI (or a standalone ComfyUI), then ask whether it is ready
powershell -File TOOLS\music.ps1 check

# 3. make three takes and keep the one you like
powershell -File TOOLS\music.ps1 generate --tags "lo-fi hip hop, mellow rhodes, soft drums, vinyl crackle" `
  --instrumental --duration 90 --bpm 82 --key "A minor" --count 3 --name lofi-study
```

The backend is found on its own: `--url` if you pass it, else `COMFYUI_URL`, else the first of
port 8188 (standalone ComfyUI) and 7820-7832 (SwarmUI's ComfyUI backend) that answers.
`check --variant all` lists which variants are installed. `image-stack.ps1 status` ends with a
`music` line that lists the variants whose files are on disk.

Files land in `out\music\` under the folder you run from (`--out` changes it). An existing file is
never overwritten; a clash gets a `-1` suffix.

Use `TOOLS\music.ps1` (or `python TOOLS/music.py`) rather than `image-stack.ps1`: PowerShell
would bind `--out`, `--format` and `--seed` to that script's own parameters.

A download connection that stays under 5 MB/s for 30 s is dropped and resumed on a new one (up to
five times, then one last try with no floor so a slow line still finishes); `-MinSpeedMB`,
`-SlowSeconds` and `-Reconnects` change that, `-MinSpeedMB 0` turns it off.

## Takes, and getting back to one

Music is generative: the same prompt gives a different song for every seed, so render a few and
pick.

* `--count N` queues N takes at once. Seeds count up from `--seed`, or are random and printed.
* `--name boss-theme` names the files (`boss-theme_00001.flac`, ...).
* Every take gets a sidecar, `<file>.json`, with the caption, lyrics, variant, seed, tempo, key,
  sampler values, ComfyUI version and render time.
* `generate --from <file>.json` renders that take again. The same seed and settings give the same
  audio, sample for sample (checked live). Add flags to change one thing (`--duration 150`,
  `--variant xl-sft`, `--format mp3`), or `--reseed --count 4` for new takes with the same settings.
  A take made with one variant does not carry its step count into another; only values you set by
  hand are carried over.
* ACE-Step sometimes finishes early and fills the rest with silence. When ffmpeg is on PATH each take
  is checked; `music_seconds` in the sidecar says where the music ends, and a take that goes quiet
  more than 3 s (or 10%) early prints a warning. Pick another seed, or ask for a shorter duration.

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
Upstream publishes no VRAM figure for the ComfyUI pipeline. `aio` runs comfortably on a 16 GB card
(about 3-9 s per 30-40 s take on an RTX 4080 SUPER). Which variant sounds best has not been judged
here: that is for your ears, and the table says only what each template sets.

## Writing a prompt

The guidance below follows ACE-Step's own songwriting and inference guides.

* `--tags` (or `--tags-file`) is the style caption, and it matters most. Be specific and combine
  genre, mood, instruments, texture (warm, airy, punchy), era and vocal type: "sad piano ballad with a
  breathy female vocal" beats "a sad song". Avoid contradictions ("classical strings" with "hardcore
  metal"); if you want a change, describe it ("soft strings at first, building into metal"). Leave
  tempo and key out of the caption: `--bpm` and `--key` carry them.
* Lyrics go in `--lyrics "..."` or `--lyrics-file song.txt` (UTF-8). Put section tags on their own
  lines: `[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Outro]`, and also `[Build]`,
  `[Drop]`, `[Breakdown]`, `[Guitar Solo]`, `[Fade Out]`. Short hints work inside a tag
  (`[Chorus - anthemic]`). Lines of 6-10 syllables, a blank line between sections, CAPITALS for
  shouted lines and (parentheses) for backing vocals. Keep it under 4096 characters; longer lyrics
  print a warning.
* `--instrumental` sends `[Instrumental]` as the lyrics, which is how ACE-Step's inference guide
  asks for an instrumental.
* `--duration` is seconds (1 to 600). Allow room for the lyrics: two verses and two choruses need
  about 120-150 s, add a bridge for 180-240 s, intro and outro take 5-10 s each. Slow tempos need
  more time for the same words. Instrumentals work well at 30-180 s.
* `--bpm` (slow 60-80, mid 90-120, fast 130-180), `--key` (`E minor`, `Bb major`) and
  `--time-signature` (4 for most music, 3 for a waltz, 6 for a swing feel) steer the plan; they are
  not hard constraints. `--language` takes the codes the node lists (`en`, `ja`, `zh`, `ko`, `es`,
  `fr`, ...); anything else is refused before a render.
* `--no-audio-codes` skips the language-model planning pass: faster, and the node documents it as lower quality.
* `--steps`, `--cfg`, `--shift` override the variant's values. Leave them alone unless you are comparing.
* `--format flac|mp3|opus` (mp3 defaults to quality V0, opus to 128k). `aio` and `xl-sft` default to FLAC, the rest to MP3.

`python TOOLS/music.py build --tags "..." --out graph.json` writes the ComfyUI API graph without
contacting anything, for an agent or script that submits it itself.

## Asset Forge

Asset Forge's `audio-music` recipe calls this tool: it renders several takes, scores each against
the caption, prefers a take that fills the asked length, then trims, levels and encodes the best one.
For a track you made here, `assetforge run audio-process --input source=<track.flac> --set loudness=-14 --set channels=2`
levels and encodes it, and `export --target minecraft|godot|roblox` packages it.

## What it does not do

* **Cover, repaint, extend and stem extraction.** ComfyUI's native support is text-to-music. The
  official launch post said cover and repaint were not yet supported, these templates contain none,
  and ComfyUI's ACE-Step nodes are unchanged in 0.45.0. Third-party custom nodes claim more; they are
  a separate, unreviewed install and are not used here.
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
| `generate`, `check`, `--count`, `--name`, sidecars, `--from`/`--reseed`, backend discovery, no-overwrite, non-ASCII paths on a cp1252 console, and the refusal paths | `TESTS/test_music.py` drives real processes against a stub backend |
| a real backend renders audible music, `aio` | `TESTS/integration_music.py` on an RTX 4080 SUPER, ComfyUI 0.44.0, through SwarmUI 0.9.8.4's backend found without `--url`: 30.0 s FLAC at 48 kHz, RMS 0.170, peak 0.992; the same seed rendered twice decodes to identical samples |
| `--count 2`, `--from` replay, MP3 output and the early-stop warning on real takes | run live through SwarmUI: a 40 s take that went silent at 34.1 s was flagged |
| Asset Forge `audio-music` works with this tool | its recipe run end to end against the real `music.py`: 2 takes, best finished to OGG |
| the other five variants render | **not run**: their weights are not installed here |
| download integrity | each file's byte length is checked against the published size; upstream publishes no checksums |
| a stuck download recovers | `TESTS/test_music_install.py`: a stub server trickles the first connection; the installer drops it after `-SlowSeconds`, resumes from the partial file on a new connection and finishes at the exact size. A short file is deleted, an HTTP error stops at once |
