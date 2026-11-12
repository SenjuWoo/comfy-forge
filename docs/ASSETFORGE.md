# Using comfy-forge with AssetForge

[AssetForge](https://github.com/SenjuWoo/AssetForge) is a standalone, agent-friendly
toolkit for producing, validating and packaging game assets — images, textures, meshes
and engine-ready exports. It is a separate repository and a separate install; this page
only explains how the two are meant to work together.

comfy-forge renders. AssetForge orchestrates. Neither forks, wraps or replaces the other.

## What AssetForge does with comfy-forge

Two recipes delegate to this repository:

| recipe | what it runs | needs |
|---|---|---|
| `comfy-generate` | `TOOLS\image-stack.ps1 generate` | SwarmUI running, models installed |
| `comfy-import` | reads an existing render into an asset | nothing beyond the file |

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
powershell -File TOOLS\image-stack.ps1 status

# 2. install anything missing (models, node pack, python modules)
powershell -File TOOLS\image-stack.ps1 install

# 3. start SwarmUI — AssetForge calls the same backend comfy-forge does
& "<your SwarmUI folder>\launch-windows.bat"

# 4. confirm it answers before handing work to AssetForge
powershell -File TOOLS\image-stack.ps1 status
```

`generate` refuses to run when SwarmUI is not answering on port 7801, so a missing backend
fails fast with that message rather than hanging an AssetForge job.

## Point AssetForge at this copy

AssetForge looks for comfy-forge in these places (order as configured on the AssetForge
side; COMFY_KIT_ROOT and `integrations.comfy_kit` are the reliable ones):

1. `COMFY_KIT_ROOT` (environment variable) — still named COMFY_KIT_ROOT in v0.6.0 for AssetForge compatibility,
2. `integrations.comfy_kit` in its project's `project.json`,
3. a default `~/comfy-kit` checkout on some AssetForge builds — the repo was renamed, so prefer 1 or 2,
4. declared roots in its config.

The folder and repo were renamed `comfy-kit` → `comfy-forge` in v0.6.0; AssetForge itself is
a separate project, so its own default paths are unchanged and may still point at the old
name. `COMFY_KIT_ROOT` remains the variable AssetForge reads today.

Required markers: `TOOLS/image-stack.ps1` and `presets/presets.json`.

The durable option is the environment variable:

```powershell
setx COMFY_KIT_ROOT "<path to this comfy-forge folder>"
```

Per-project, in AssetForge's `project.json`:

```json
{
  "integrations": {
    "comfy_kit": "<path to this comfy-forge folder>"
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
- Start SwarmUI. If it is not running, the render step fails with comfy-forge's own message.
- Pick a preset for you. The default is `Anima - Author Daily (Euler + SGM)`; give it a different one with `--set preset="<name>"` (`TOOLS\image-stack.ps1 presets` lists them).
- Verify quality. It validates the export, not the art — look at the image.

## Verified, and not

| Check | Result |
|---|---|
| AssetForge delegates a render through `image-stack.ps1 generate` | UNVERIFIED — AssetForge itself has never been run on this machine; SwarmUI/ComfyUI is running and this repo's render path works independently |
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

comfy-forge's CLI exits 0 on success and 1 on any refusal (no preset, no model, SwarmUI not
running, nothing to merge). AssetForge surfaces those as recipe failures with the CLI's
output attached, so `--json` is the way to drive it from a script.

## Which tool for which job

The decision guide for the two tools — deterministic/QA-gated AssetForge pipelines vs
this repo's generative AI lanes (renders, LoRA captions, image→3D), and when to use
which for 2D sprites, procedural props, texture pipelines, engine exports — lives in
**[PRODUCTION-WORKFLOWS.md](PRODUCTION-WORKFLOWS.md)**. Short version: **AssetForge for
repetitive/QA-gated exports and procedural props; comfy-forge for generative art, AI
renders and image→3D; the two in sequence when a 3D asset starts from a concept image.**

A generated `.glb` is not game-ready. About 700,000 triangles fails. One command:

```powershell
powershell -File TOOLS\game-ready.ps1 -Input model.glb
```

Default cap: 20,000 triangles. Rules: [GAME-READY.md](GAME-READY.md).
