# Anima generation stack — auto-install handoff

Everything the bundle needs to put an **Anima max-generation stack** into a
SwarmUI or ComfyUI install automatically. Read this before touching the files.

## What was added

| File | Purpose |
|---|---|
| `anima-manifest.json` | Pinned model files: URL, byte length, SHA-256, and where each lands in SwarmUI vs ComfyUI. The only place download details live. |
| `presets.json` | SwarmUI generator presets, including the Anima set. **Export data, not live storage** — import it in the UI. |
| `ANIMA-HANDOFF.md` | This file. |
| `TOOLS/Ensure-AnimaSwarmUI.ps1` | The installer. Detects a frontend, downloads only what is missing, verifies SHA-256. |
| `TOOLS/Ensure-Tools.ps1` | Calls the installer on every run, before its early return. |

## How the auto-install works

`Ensure-Tools.ps1` runs the installer unconditionally. The installer then:

1. **Finds frontends.** A folder is SwarmUI if it holds `SwarmUI.sln` +
   `Data\Backends.fds` + a `Models` folder. A folder is ComfyUI if it holds
   `main.py` + `comfy\samplers.py` + a `models` folder. Detection uses
   `SWARMUI_ROOT` / `COMFYUI_ROOT` (process or user scope), then a
   directory-name scan of the profile and each fixed drive.
2. **Resolves SwarmUI's own layout from `Data\Settings.fds`** (`ModelRoot`,
   `SDModelFolder`, `SDVAEFolder`, `SDClipFolder`). It never guesses.
3. **Downloads only what is missing.** Presence is by SHA-256, not by name, so
   a file that already exists under a different filename or subfolder counts.
4. **Copies the presets** to `<SwarmUI>\Data\presets-uabs-anima.json` and adds
   `PRESETS-IMPORT.md` next to them.
5. **Records the run** in
   `%LOCALAPPDATA%\Ultimate-AI-Starter-Bundle\anima-swarmui.json`.

A machine with no SwarmUI or ComfyUI is a **no-op** (prints one line, exits 0),
so wiring it into the general tool flow is safe.

## What gets installed

Default (about 7.3 GB, only the missing parts):

- `Anima-2.9B-preview-v1.safetensors` — the 2.9B layer-expanded Anima, bf16. **5.84 GB**
- `qwen_3_06b_base.safetensors` — Qwen3 0.6B text encoder. **1.19 GB**
  (alias `qwen_3_600m.safetensors` is accepted as the same bytes)
- `qwen_image_vae.safetensors` — Qwen Image VAE. **0.25 GB**

Optional, only with `-IncludeOptional` (~3.1 GB extra):

- `Anima-2.9B-preview-v1_int8_convrot.safetensors` — int8 quantized, for small VRAM.

## Running it by hand

```powershell
# See what would happen, download nothing
.\TOOLS\Ensure-AnimaSwarmUI.ps1 -CheckOnly

# Install (idempotent; already-verified files are skipped)
.\TOOLS\Ensure-AnimaSwarmUI.ps1

# Also install the int8 variant, or force re-download of everything
.\TOOLS\Ensure-AnimaSwarmUI.ps1 -IncludeOptional
.\TOOLS\Ensure-AnimaSwarmUI.ps1 -Force

# Point at a frontend explicitly
.\TOOLS\Ensure-AnimaSwarmUI.ps1 -SwarmUIRoot 'S:\Misc\SwarmUI'
```

Downloads resume (`curl.exe -C -`) and stage to a `.part` file, so an
interrupted run is safe to repeat. A file is only moved into place after both
its byte length and SHA-256 match the manifest.

## Important facts for the agent

- **`rk_beta`, `beta57`, `res_2s`, `res_2m`, `bong_tangent` cannot be used from
  SwarmUI.** They are RES4LYF sampler/scheduler names; RES4LYF exposes its own
  nodes rather than KSampler names, and SwarmUI's sampler list is a hardcoded
  array in `ComfyUIBackendExtension.cs`. Installing RES4LYF does not make them
  selectable, so the bundle ships none of them. The presets use the closest
  things SwarmUI exposes (`er_sde`, `beta`, `res_multistep`, `linear_quadratic`),
  plus the newer `seeds_2`, `sa_solver_pece` and `exp_heun_2_x0` that are
  already in the installed ComfyUI backend.
- **The Anima preset numbers come from the model card** (`euler` / `er_sde` /
  `res_multistep` with `sgm_uniform` / `beta` / `linear_quadratic`, 28–50 steps,
  CFG 3.5–5). Anima's training data carries **no score tags**, so the Anima
  negatives have none.
- **Comfy Desktop installs are skipped.** That app keeps its models in a
  separate `ComfyUI-Shared` tree and manages them itself; dropping files into
  the instance's `models` folder would strand them.
- **A ComfyUI nested inside a SwarmUI root is treated as that SwarmUI's
  backend** — models go in SwarmUI's `Models` tree, not twice.

## What remains for the operator

1. Import the presets in SwarmUI: **Presets tab → Import → `presets-uabs-anima.json`**
   (it is copied next to the install for convenience).
2. First generation: pick `Anima-2.9B-preview-v1` as the model, the
   `Anima - Author Daily` or `Anima - Official Best` preset, and confirm the
   text encoder and VAE resolve.
3. The old `woomixAnima_v15` checkpoint is a merge, not the 2.9B base. Keep it,
   but the 2.9B base is the current reference.

## Updating

To move to a new Anima release: update `anima-manifest.json` (URL, bytes,
SHA-256) and re-run the installer. Do not add model files to the bundle itself
— the manifest is the source of truth and the bundle stays small.
