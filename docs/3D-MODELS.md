# 3D models: image → 3D, and which one you actually want

comfy-forge does not download 3D weights for you. This page is the decision guide plus
the one command that fetches everything, if you want it.

The GLB these templates write is a sculpt, about 700,000 triangles. That file fails
the game-ready gate. After it exists, run `TOOLS/game-ready.ps1` on the `.glb`.
The default cap is 20,000 triangles. Rules: [GAME-READY.md](GAME-READY.md).

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

Measured on this machine on 2026-11-12 (see the verification table below): all three run,
all three produce the asset they promise. The output-quality ranking stands — Pixal3D over
Idol for anything you ship — and it is no longer a claim, it's an observation.

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

## Verified on this machine (RTX 4080 SUPER, 2026-11-12)

All three templates were run end-to-end with `TOOLS/Install-3DModels.ps1 -Set all`; nothing
failed. This supersedes every "UNVERIFIED" estimate above — the file lists, runtime and
output descriptions below are measured, not guessed.

| template | input | output | runtime |
|---|---|---|---|
| Idol | polar_bear_bass_player.png (1.2 MB) | `.spz` 3.1 MB + `.glb` 20.4 MB, 847,804 tris, 423,642 verts, **no textures embedded (unlit vertex colours)**, orbit MP4 520 KB / 1024² | ≈ 3.5 min |
| Pixal3D (single) | viking_wolf_rune_axe.png (1.1 MB) | `.glb` 61.5 MB, 699,878 tris, 419,622 verts, **1 material: pbrMetallicRoughness + occlusion + normal, 3 embedded PNGs (4096² base color + normal, 2048² rough/metal/AO)** | ≈ 4 min |
| Pixal3D (multi-view) | character_turnaround_4view_sheet.png (14 MB, white 4-view mech sheet) | `.glb` 699,770 tris, 439,883 verts, same material structure as single-view, same texture sizes | ≈ 4.5 min |

Visual read (from the extracted frames, not a viewer score): the Idol bear render is
surprisingly coherent from front, profile and back — fur silhouette, sunglasses, red guitar —
but it is splat-quality noise converted to an 847k-tri unlit mesh: great for orbit previews,
not engine-ready. Both Pixal3D models read as real PBR assets: dense but complete
double-sided mesh with unwrapped UVs; the axe albedo atlas is properly packed; the multi-view
atlas correctly captures the uniform white/gray mech (monochrome content — fine as an asset,
not a stress test for color fidelity). Multi-view's full-body consistency claim is by design,
not measured here.

The two Pixal3D `.glb` files weigh ~60 MB each and are about 700,000 triangles.
That count is the sculpt. It does not pass the game-ready gate. Run
`TOOLS/game-ready.ps1` on the `.glb`. The default hard cap is 20,000 triangles
and a 1024 texture. See [GAME-READY.md](GAME-READY.md).

Stock template settings were kept. The upstream templates already carry their tuning
(`RescaleCFG` 0.5/0.7, `CFGOverride` 1/0.769/0.667, flow shift 5, euler + `ModelSamplingSD3`).
Measured evidence does not justify changing them; the upgrade path for quality is
multi-view over single-view inputs, not knob-turning.

Nothing in the runs needed custom seeds: all three workflows run `fixed` seeds, so results
reproduce exactly on the same inputs.

## Running it

1. Load a preprocessed image in `LoadImage`. The subject should sit on a clean or
   removable backdrop; BiRefNet handles backdrop removal if `auto_remove_background`
   is on.
2. Open ComfyUI, drag one of the `presets/3d/*.json` files onto the canvas.
3. Queue. All branches active in the Idol workflow run orbit video, `.spz` and `.glb`;
   the Pixal3D workflows run shape → upsample → texture stages and export `.glb` plus
   the PBR maps as images.

## State of verification

All verified on this machine on 2026-11-12 (`RTX 4080 SUPER`):

- Model downloads: `-Set all` fetched every file cleanly; the installer's `-WhatIf` path
  also verified (it reported "have" for every entry afterwards).
- Renders: all three runs completed successfully; outputs recorded in the verification
  table above.
- The default-image inputs (`polar_bear_bass_player.png`, `viking_wolf_rune_axe.png`,
  `character_turnaround_4view_sheet.png`) ship with the ComfyUI template library's
  upstream MIT repository; fetch them once from there (same URL base listed in
  `presets/3d/NOTICE.md`) into your ComfyUI `input/` folder.

Each of these is a download you opt into; `TOOLS/Install-3DModels.ps1 -WhatIf` shows the
exact file list before you commit.
