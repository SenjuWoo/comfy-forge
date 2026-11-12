# Which tool for which job: AssetForge vs comfy-forge

Two separate tools, two separate approaches to "game assets". Both are installed for a
reason; using the wrong one for a job means fighting the workflow. This is the decision.

| | **AssetForge** | **comfy-forge** (this repo) |
|---|---|---|
| approach | deterministic pipelines: explicit parameters, presets, QA gates | generative AI: Anima/Flux renders, diffusion 3D, vision tagging |
| inputs | `.blend`, `.glb`, images, specs | images, tags, LoRA sources |
| outputs | game-ready files with QA evidence (reports, review sheets) | images, captions, datasets, raw `.glb`/`.spz` |
| failure mode | a job fails loudly with exit code 5, never silently ships | model output quality varies per prompt/seed; review is on you |
| best for | repetition, consistency, budgets, exports | novelty, speed, concept → first draft |

AssetForge deliberately does **not** call generative models except through comfy-forge
(`comfy-generate`, `comfy-import`). It evaluates every result it produces: `blender-asset-qa`
fails a job on any error (warnings must be justified in writing), the Khronos glTF
Validator result is recorded in provenance, and QA review sheets are written even on
failure. That is the "more finetuned, less AI sloppy" path — for assets you ship
hundreds of times.

## Recommended by workflow

**Roblox / low-poly props (crates, barrels, fences, emblems, kit pieces)** → **AssetForge**
(`blender-pipeline`, `blender-optimize --set budget=roblox`, `blender-retopo-uv`).
Parametric, consistent silhouettes, deterministic, LODs built by the pipeline.
*Use comfy-forge only for the texture sources it cannot invent (see below).*

**Stylized/painterly textures and material maps (albedo, ORM)** → **comfy-forge**
(`image-stack.ps1 generate`, Anima presets) to produce the renders, then **AssetForge**
(`texture-pack` for ORM, `image-prep` for sizing/webp, `glb-optimize` to pack it into the
asset). comfy-forge owns generation; AssetForge packages and validates it.

**One concept image → a game asset (hero rock, statue, sword)** → **comfy-forge** (`presets/3d/`,
Pixal3D — see [3D-MODELS.md](3D-MODELS.md)) for the raw mesh, then **AssetForge**
(`glb-inspect` → `blender-retopo-uv` for clean UVs → `glb-optimize`) to make it
engine/game ready. Raw Pixal3D output is 700k+ triangles and needs retopology before it
goes anywhere near a Roblox/Godot project.

**Character with a turnaround sheet** → **comfy-forge** (Pixal3D multi-view from the
[turnaround generator](https://github.com/Comfy-Org/workflow_templates)) then the same
AssetForge tail. This is the only route in either tool for consistent character meshes,
and realistic silhouette fidelity.

**Sprites / icon atlases / UI textures for a 2D game** → **AssetForge**
(`sprite-atlas`, `image-prep`) — deterministic, hashed, capped, no AI at all.

**Exploring looks fast (concept art, moodboards, Anima renders, LoRA-ready captions)**
→ **comfy-forge**. Not AssetForge's job; nothing deterministic about art direction.

**Maximum-batch, repetitive export flows** (e.g. 40 prop variants off one preset)
→ **AssetForge**. A deterministic pipeline is the only sane way to batch; generative
pipelines drift per seed.

**QA'd deliverables with an audit trail** → **AssetForge**. It records budgets, QA
numbers, provenance and (optionally) `GLB-014` validation alongside the asset.

## The recommended pattern

```
Need an asset?
  ├── can a preset produce it?          → ASSETFORGE (blender-pipeline)
  ├── is it a texture/material look?     → comfy-forge generate → ASSETFORGE texture-pack → comfy-forge/AssetForge export
  ├── is it a mesh from a concept image? → comfy-forge presets/3d (Pixal3D) → ASSETFORGE glb-optimize/blender-retopo-uv → export
  └── is it sprites/UI/2D?               → ASSETFORGE sprite-atlas / image-prep
```

Neither tool replaces the other. AssetForge is the **production and QA lane**; comfy-forge
is the **generative AI lane**. In the asset's lifecycle, AssetForge is usually the last
word before the engine; comfy-forge is the first word after "I want".

## See also

- [ASSETFORGE.md](ASSETFORGE.md) — setting AssetForge up next to this repo
- [3D-MODELS.md](3D-MODELS.md) — Idol / Pixal3D / single vs multi-view, verified on this machine
- AssetForge docs: `docs/RECIPES.md`, `docs/PROVIDERS.md`, `docs/UABS-INTEGRATION.md`
  (read in the AssetForge folder, or on its public repo)
