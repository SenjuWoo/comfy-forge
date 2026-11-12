# 3D models: image → 3D, and which one you actually want

comfy-forge does not download 3D weights for you. This page is the decision guide plus
the one command that fetches everything, if you want it.

Three templates, all present in `presets/3d/` (copies of the upstream ComfyUI workflow
templates, MIT — see `presets/3d/NOTICE.md` for provenance):

| | **Idol** (fine-tuned TripoSplat) | **Pixal3D** — single image | **Pixal3D** — multi-view |
|---|---|---|---|
| file | `3d_idol_image_to_gaussian_splat.json` | `3d_pixal3d_trellis2_image_to_model.json` | `3d_pixal3d_multi_views.json` |
| input | 1 image | 1 image | a turnaround sheet (4 views) |
| output | 3D Gaussian splat (`.spz`), plus an optional `.glb` via SplatToMesh (res 384 — very heavy) | textured `.glb` mesh, UV-unwrapped, plus PBR maps (base color, normal, metallic, roughness, AO) | same as single-view, but the whole body stays consistent, not just the front |
| download | ~3.5 GB | ~9.8 GB | ~8 GB |
| compute | lightest (706 MB UNET) | heaviest (int8 convrot UNET, ~5.2 GB) | heaviest (multi-view int8 UNET, ~5.2 GB) |
| models | `idol_fp16`, `idol_decoder_fp16`, `flux2-vae`, `dino_v3_vit_h_bf16`, BiRefNet | `pixal3d_int8_convrot`, `trellis_2_shape/texture_vae_bf16`, `dino_v3_L_naf_fp32`, MoGe (`moge_2_vitl_normal_fp16`), BiRefNet | `pixal3d_multiview_int8_convrot`, same VEAs + DINO, BiRefNet |
| license | Apache-2.0 | MIT | MIT |

## Honest evaluation

None of these has been run on this machine yet, so quality claims below come from the
workflow notes, not from measuring faces on your render. The compute ranking is solid
(file sizes are in the workflow notes and on the model cards); the output-quality ranking
is what the upstream templates and the community report. Treat the first run as the test —
this page gets updated after it.

**Which is better overall: Pixal3D.** Idol loses to it on every axis you actually ship —
Idol gives you a splat, great for previews and AR fly-throughs, but it is not a textured,
assembled, UV-unwrapped mesh with PBR maps, and its `.glb` conversion at 384 px is heavy
enough to be punishing on purpose-built game assets. Pixal3D outputs the asset: base color,
normal, AO, metallic, roughness, unwrap, decimate, `.glb`, done.

## Which way for which workflow

| your workflow | use |
|---|---|
| one concept image → a game prop / item (box, sword, bag, rock, small creature) | **Pixal3D single image** — fastest path to a rigless `.glb` with PBR maps |
| a hero character built from a front/side/back sheet | **Pixal3D multi-view** — single-view guesses the back; a turnaround sheet removes the guess |
| statues, furniture, background props you'll bake lighting on | **Pixal3D single image** — AO + normal bake cleanest from single image |
| 2D concept art for you to look at in 3D while iterating | **Idol** — fast, cheap, good silhouette read; don't ship it as-is |
| AR/VR splat previews, Gaussian-splat pipelines | **Idol** — `.spz` is the target format Idol produces natively |
| vehicles / very hard-surface, small-tolerance parts | **neither single-image is reliable** — model it in Blender, or use a turnaround sheet (Pixal3D multi-view) and remesh after |
| textures only (albedo/roughness from an image, no geometry) | **not what these do** — see [docs/MERGE.md](MERGE.md) for render pipelines |
| skinned / animated characters | **none of these** — rigging is out of scope for every 3D image-to-model route in this repo |
| need consistent back + sides with no turnaround sheet | generate a turnaround sheet first (the `Character Turnaround Sheet Generator` template in the ComfyUI template library), then **Pixal3D multi-view** |

Rule of thumb: **props → Pixal3D single; characters → turnaround sheet → Pixal3D multi-view;
previews → Idol.** If you can only afford to download one stack, download Pixal3D.

## What the three actually need installed

A ComfyUI build with TRELLIS.2 support (DiT TRELLIS.2 nodes and the Pixal3D conditioning
nodes — both ship in recent ComfyUI; this machine's install has them). The image-to-model
workflow also uses MoGe for depth/FOV estimation and has a switch to run stock TRELLIS.2
instead of Pixal3D — set `Switch: Remove background` / the Pixal3D-Trellis toggle to
`true` for stock TRELLIS.2 (requires `trellis_2_int8_convrot.safetensors`, ~4.9 GB, also
MIT).

## Installing the weights

```powershell
# see what would be downloaded, and where
powershell -File TOOLS\Install-3DModels.ps1 -Set all -WhatIf

# or fetch one stack
powershell -File TOOLS\Install-3DModels.ps1 -Set pixal3d
```

`-Set` accepts `idol`, `pixal3d`, `trellis2` or `all`. Files land in the `models` folder
of your ComfyUI (discovered automatically; pass `-ComfyRoot` to override). Downloads are
resumed with `curl -C -`; a completed file is never re-fetched. Nothing is downloaded
here — `-WhatIf` only lists. No hashes are published upstream for these files, so the
script verifies by download completing cleanly, not by checksum; if a file is corrupt,
delete it and re-run.

## Where things land

```
ComfyUI/
└── models/
    ├── diffusion_models/   idol_fp16.safetensors, pixal3d_int8_convrot.safetensors,
    │                       pixal3d_multiview_int8_convrot.safetensors,
    │                       trellis_2_int8_convrot.safetensors (trellis2 set only)
    ├── vae/                idol_decoder_fp16.safetensors, flux2-vae.safetensors,
    │                       trellis_2_shape_vae_bf16.safetensors,
    │                       trellis_2_texture_vae_bf16.safetensors
    ├── clip_vision/        dino_v3_vit_h_bf16.safetensors (Idol),
    │                       dino_v3_L_naf_fp32.safetensors (Pixal3D)
    ├── geometry_estimation/ moge_2_vitl_normal_fp16.safetensors (Pixal3D)
    └── background_removal/ birefnet.safetensors (both)
```

## Running it

1. Load a preprocessed image in `LoadImage`. The subject should sit on a clean or
   removable backdrop; BiRefNet handles backdrop removal if `auto_remove_background`
   is on.
2. Open ComfyUI, drag one of the `presets/3d/*.json` files onto the canvas.
3. Queue. All branches active in the Idol workflow run orbit video, `.spz` and `.glb`;
   the Pixal3D workflows run shape → upsample → texture stages and export `.glb` plus
   the PBR maps as images.

## State of verification

- Workflows saved in this install's `user/default/workflows/` (copied into the repo
  from `presets/3d/` — these are the copies your install holds).
- Model downloads: NOT verified on this machine (nothing was fetched).
- Renders: NOT verified on this machine (SwarmUI/ComfyUI is not running).
- The comparison above is from the upstream workflow notes and model cards.

Each of these is a download you opt into; `TOOLS/Install-3DModels.ps1 -WhatIf` shows the
exact file list before you commit.
