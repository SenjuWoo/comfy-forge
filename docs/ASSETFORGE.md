# Using comfy-forge with AssetForge

[AssetForge](https://github.com/SenjuWoo/AssetForge) is a standalone, agent-friendly
toolkit for producing, validating and packaging game assets — images, textures, meshes
and engine-ready exports. It is a separate repository and a separate install; this page
only explains how the two are meant to work together.

comfy-forge renders. AssetForge orchestrates. Neither forks, wraps or replaces the other.

## What AssetForge does with comfy-forge

These recipes use this repository:

| recipe | what it runs | needs |
|---|---|---|
| `comfy-generate` | `TOOLS\image-stack.ps1 generate` | SwarmUI installed with a checkpoint; it is started when it is down |
| `comfy-import` | reads an existing render into an asset | nothing beyond the file |
| `audio-music` | `TOOLS\music.py generate` | a ComfyUI backend and ACE-Step weights |

`TOOLS\game-ready.ps1` goes the other way: it hands a generated `.glb` to Asset
Forge's `mesh-gameready` recipe. `python TOOLS/model3d.py generate ... --game-ready`
does the whole trip: picture (or prompt) to a `.glb` to that recipe.

## The contract

What Asset Forge, or any other caller, can rely on:

| Command | Arguments that stay | Result |
|---|---|---|
| `image-stack.ps1 generate` | `-Prompt -Preset -Model -Width -Height -Seed -Count -Steps -Cfg -Out -Port`; newer: `-Negative -InitImage -Creativity -Mask -Lora -Transparent -Tileable -Upscale -Set -Json -NoStart` | images in `-Out`, a `.json` sidecar per image, with `-Json` a closing line `{"files","seed","seeds","model","preset","width","height"}`; exit 0 or 1 |
| `music.py generate` | `--url --out --timeout --seed` and the prompt flags | closing line `{"variant","seed","seeds","files","takes"}`; exit 0, 1, 2, 3 |
| `model3d.py generate` | `--image` / `--views` / `--prompt`, `--variant --name --out --seed --game-ready --budget --url --no-start --timeout` | closing line `{"variant","name","files","model","splat","game_ready","game_ready_error","triangles","game_ready_triangles","image"}`; exit 0, 1, 2, 3 |
| `image-stack.ps1 capabilities` | none | one JSON object: `backend`, `lanes.image|model3d|music|dataset|merge|mcp` each with `ready`, `command`, `fix` |
| `image-stack.ps1 start` / `stop` | `-Wait <seconds>` | exit 0 when the backend answers / is gone |
| `image-stack.ps1 python` | none | one line: the interpreter of the stack's ComfyUI |

Rules that hold across them:

- `-Port` (images) and `--url` (music, 3D) name a server the caller runs. comfy-forge then never starts another one, and never sends the job anywhere else.
- Without them, a backend that is down is started headless and left running; `stop` ends only a backend comfy-forge started. `-NoStart`, `--no-start` or `COMFY_FORGE_NO_AUTOSTART=1` turn that off.
- A backend started by `start` does not inherit the caller's output pipe, so a caller that captures the command's output gets control back when the command returns.
- `-Model` may be empty or absent: the preset's model, then one of the preset's family, is used and printed.
- A raw `.glb` from `model3d.py` is valid glTF (unit normals) before it is handed on.

AssetForge only delegates a render and keeps the result inside the asset it is building.
It never installs models, downloads weights or edits comfy-forge's config.

## Why this pairing works

If you already use comfy-forge for Anima renders, AssetForge adds the asset side: a project
layout, a job history, image prep recipes (tileable padding, packing, validation), and
exports to Roblox / Godot / generic. You get generated textures *inside* a validated asset
instead of a folder of loose PNGs you then copy by hand.

## Make comfy-forge ready first

AssetForge cannot render until comfy-forge can. Do this once, in order:

```powershell
# 1. see what is installed and running
powershell -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 status

# 2. install anything missing (models, node pack, python modules)
powershell -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 install

# 3. optional: start SwarmUI now (headless) instead of on the first render
powershell -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 start
```

SwarmUI has to have been launched once by hand (`launch-windows.bat`), so that it has
built itself and installed its ComfyUI backend. After that `generate` starts it when it
is not answering (port 7801 unless `Data\Settings.fds` says otherwise); with `-NoStart`
or `-Port` it refuses at once with a message that names the fix, rather than hanging
an AssetForge job.

## Point AssetForge at this copy

Current AssetForge builds read `COMFY_FORGE_ROOT` (environment variable) or
`integrations.comfy_forge` in a project's `project.json`. The older names
`COMFY_KIT_ROOT` and `integrations.comfy_kit` still work; when both variables are
set, `COMFY_FORGE_ROOT` wins. The repo was renamed `comfy-kit` → `comfy-forge` in
v0.6.0, so prefer the new names.

Required markers: `TOOLS/image-stack.ps1` and `presets/presets.json`; the music
recipe also needs `TOOLS/music.py` (0.8.0 or later).

The durable option is the environment variable:

```powershell
setx COMFY_FORGE_ROOT "<path to this comfy-forge folder>"
```

Per-project, in AssetForge's `project.json`:

```json
{
  "integrations": {
    "comfy_forge": "<path to this comfy-forge folder>"
  }
}
```

Use forward slashes or escaped backslashes — project.json is JSON.

## A typical session

```bat
assetforge.cmd init C:\my\asset-project --name steel-panel
cd C:\my\asset-project
assetforge.cmd run comfy-generate --set "prompt=seamless weathered steel, top-down, tileable" --set width=1024 --set height=1024
assetforge.cmd run texture-pack --input occlusion=ao.png --input roughness=rough.png --input metallic=metal.png
assetforge.cmd export ASSET --target godot
```

Seeds are reproducible: `--set seed=12345` gives you the same image back.

## Things AssetForge will not do for you

- Install or download model weights (nothing here happens without you running `install`).
- Pick a preset for you. The default is `Anima - Author Daily (Euler + SGM)`; give it a different one with `--set preset="<name>"` (`TOOLS\image-stack.ps1 presets` lists them).
- Verify quality. It validates the export, not the art — look at the image.

## Verified, and not

| Check | Result |
|---|---|
| AssetForge `audio-music` drives `music.py generate` | PASS — run end to end against the real tool through SwarmUI's backend (see [MUSIC.md](MUSIC.md)) |
| AssetForge delegates a render through `image-stack.ps1 generate` | PASS (0.11.0) — `comfy-generate` run end to end: detect, generate through the running SwarmUI, thumbnail, record; job SUCCEEDED |
| `model3d.py --game-ready` hands a generated `.glb` to `mesh-gameready` | PASS on a Pixal3D chest (699,350 → 17,000 triangles, 1024 textures). One axe mesh failed Asset Forge's own glTF validation of its output (zero tangents after decimation); reported to Asset Forge |
| Markers this repo exposes (`TOOLS/image-stack.ps1`, `presets/presets.json`) | present |
| `image-stack.ps1 status` / `presets` / `generate -Out` | PASS in this repo's test suite |

To verify the pairing yourself once SwarmUI is running:

```bat
assetforge.cmd providers
assetforge.cmd run comfy-generate --set "prompt=solid mid-grey, flat, tileable" --set count=1 --set width=512 --set height=512
```

An empty or failing render means comfy-forge is the thing to fix — see
[docs/MERGE.md](MERGE.md) for model and preset specifics.

## Exit codes AssetForge sees

`image-stack.ps1` exits 0 on success and 1 on any refusal (no preset, no model, SwarmUI not
running, nothing to merge). `music.py` exits 0 done, 1 failed, 2 bad arguments, 3 no backend,
and ends with one JSON line listing the files. AssetForge surfaces a nonzero exit as a recipe
failure with the tool's output attached.

## Which tool for which job

The decision guide for the two tools — deterministic/QA-gated AssetForge pipelines vs
this repo's generative AI lanes (renders, LoRA captions, image→3D), and when to use
which for 2D sprites, procedural props, texture pipelines, engine exports — lives in
**[PRODUCTION-WORKFLOWS.md](PRODUCTION-WORKFLOWS.md)**. Short version: **AssetForge for
repetitive/QA-gated exports and procedural props; comfy-forge for generative art, AI
renders and image→3D; the two in sequence when a 3D asset starts from a concept image.**

A generated `.glb` is not game-ready. About 700,000 triangles fails. One command:

```powershell
powershell -ExecutionPolicy Bypass -File TOOLS\game-ready.ps1 -Input model.glb
```

Default cap: 20,000 triangles. Rules: [GAME-READY.md](GAME-READY.md).
