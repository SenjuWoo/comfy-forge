# Game-ready meshes

Engines count **triangles**. A quad is 2 triangles. A generated axe at about 700,000 triangles is a sculpt. It is not a game asset, and it **fails** this gate.

The one command below ships a handheld prop (an axe, a sword, a tool) at **20,000 triangles or fewer**, with textures at **1024**. You do not pick a number. A result over the cap fails. There is no slack past the cap.

## One command

From this repo:

```powershell
powershell -File TOOLS\game-ready.ps1 -Input model.glb
```

That writes `model-gameready.glb` next to the input. Asset Forge runs Blender. This repo does not decimate.

The same cap, from Asset Forge:

```bat
assetforge.cmd run mesh-gameready --input mesh=model.glb
```

A `.spz` is a preview. Pass the `.glb`.

## The three budgets

| Budget | Triangles | Texture | When |
| --- | ---: | ---: | --- |
| `game` (default) | 20,000 | 1024 | Use this. It is the Roblox MeshPart ceiling, and the ceiling this pipeline uses for a handheld prop in Unity, Godot, and Unreal. |
| `hero` | 30,000 | 2048 | Close-up only. You have to ask for it. It does **not** pass a Roblox mesh import. |
| `accessory` | 4,000 | 1024 | A Roblox accessory worn on an avatar. Marketplace accessories cannot exceed 4,000 triangles. |

```powershell
powershell -File TOOLS\game-ready.ps1 -Input model.glb -Budget hero
powershell -File TOOLS\game-ready.ps1 -Input model.glb -Budget accessory
```

Leave `-Tris` and `-Texture` alone. `0` means "use the budget". A triangle count above the budget is rejected before Blender starts. `50000` and `700000` are not budgets.

Blender is asked for 85% of the cap so a little overshoot still lands at or under it. The written file is what gets checked. 20,000 passes. 20,001 fails.

## What you do not change

The generator's 700,000 decimate widget is the sculpt, so the texture bake stays sharp. Sampler, CFG, flow shift, and the 4096 bake stay on the stock values locked in `presets/3d/generation.json`. Shrinking that widget to chase a smaller file changes the look. Run this command after the GLB exists.

This gate is one prop mesh. It does not guess a full-character budget.

## Why these numbers

Checked 2026-11-12:

- Roblox, [general mesh specifications](https://create.roblox.com/docs/art/modeling/specifications) (updated 2026-11-11): an individual mesh cannot exceed **20,000** triangles.
- Roblox, [rigid accessory specifications](https://create.roblox.com/docs/art/accessories/specifications) (updated 2026-06-16): a worn accessory cannot exceed **4,000** triangles. Marketplace accessory textures cannot exceed 2048. This pipeline uses 1024 on that budget.
- Unity's modeling-performance and mesh-LOD docs do not publish one prop triangle count. Fewer polygons run faster, and automatic LOD roughly halves each level.
- Godot's 3D performance docs do not publish a prop triangle cap. Avoid triangles that are smaller than a pixel.
- Unreal LOD groups step down by about half. Nanite is for dense environment meshes. It is not a reason to ship a 700,000-triangle weapon.

20,000 is the default because it is the strict official cap that also fits a handheld weapon everywhere else. 30,000 exists only when you say `hero`.
