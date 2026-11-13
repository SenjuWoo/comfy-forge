# Anima generation stack — handoff

Everything needed to put an **Anima max-generation stack** into a SwarmUI or
ComfyUI install. Read this before touching the files.

## What is here

| File | Purpose |
|---|---|
| `anima-manifest.json` | Pinned model files: URL, byte length, SHA-256, and where each lands in SwarmUI vs ComfyUI. The only place download details live. |
| `presets.json` | SwarmUI generator presets, including the Anima set. **Export data, not live storage** — import it in the UI. |
| `ANIMA-HANDOFF.md` | This file. |
| `TOOLS/Ensure-AnimaSwarmUI.ps1` | The installer. Detects a frontend, downloads only what is missing, verifies SHA-256. |
| `TOOLS/image-stack.ps1` | The user-facing CLI. `install` calls the installer for you. |
| `TOOLS/merge_anima.py` | Offline checkpoint merge. See `docs/MERGE.md`. |

## How the install works

1. **Finds frontends.** A folder is SwarmUI if it holds `SwarmUI.sln` +
   `Data\Backends.fds` + a `Models` folder. A folder is ComfyUI if it holds
   `main.py` + `comfy\samplers.py` + a `models` folder. Detection uses
   `SWARMUI_ROOT` / `COMFYUI_ROOT` (process or user scope), then a bounded
   directory-name scan, then the path the previous run recorded.
2. **Resolves SwarmUI's own layout from `Data\Settings.fds`** (`ModelRoot`,
   `SDModelFolder`, `SDVAEFolder`, `SDClipFolder`). It never guesses.
3. **Downloads only what is missing.** Presence is by SHA-256, not by name, so
   a file that already exists under a different filename or subfolder counts.
4. **Copies the presets** to `<SwarmUI>\Data\presets-uabs-anima.json` and adds
   `PRESETS-IMPORT.md` next to them.
5. **Records the run** in
   `%LOCALAPPDATA%\Ultimate-AI-Starter-Bundle\anima-swarmui.json` (override with
   `-StateDir`).

A machine with no SwarmUI or ComfyUI is a **no-op** (prints one line, exits 0).

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
.\TOOLS\Ensure-AnimaSwarmUI.ps1 -SwarmUIRoot '<SwarmUI>'
```

Downloads resume (`curl.exe -C -`) and stage to a `.part` file, so an
interrupted run is safe to repeat. A file is only moved into place after both
its byte length and SHA-256 match the manifest.

## Important facts for an agent

- **RES4LYF names DO work in SwarmUI, once the pack is installed.** Earlier notes
  in this file claimed the opposite; that was wrong. RES4LYF appends `beta57`,
  `bong_tangent`, `res_2s`, `res_2m`, `rk_beta` to ComfyUI's
  `SCHEDULER_NAMES` / `SAMPLER_NAMES`, and
  `ComfyUIBackendExtension.AssignValuesFromRaw` merges whatever `KSampler`
  reports (`SamplerParam` / `SchedulerParam`, "(New in KS)") into SwarmUI's own
  dropdowns at startup. Verified: schedulers went 9 → 11 and samplers 45 → 64
  after installing RES4LYF and restarting.
  `install` clones it into `custom_nodes`; **restart SwarmUI afterwards**, because
  the list is read when the backend connects.
- **`anima_beta57` is a HaDeS alias, not a ComfyUI name.** It is implemented in
  `anima_ksampler_nodes.py` as `beta_scheduler(alpha=0.5, beta=0.7)`, which is
  exactly what RES4LYF registers as **`beta57`**. In SwarmUI, use `beta57`.
  The HaDeS KSampler node keeps the `anima_beta57` spelling for its own list.
- **The Anima preset numbers come from the model card** (`euler` / `er_sde` /
  `res_multistep` with `sgm_uniform` / `beta` / `linear_quadratic`, 28–50 steps,
  CFG 3.5–5). Anima's training data carries **no score tags**, so the Anima
  negatives have none.
- **SwarmUI rejects `vae: "Automatic"`.** The presets carry it as "let the server
  choose"; the CLI drops it before sending. In the UI, pick a real VAE.
- **New model files need a refresh.** SwarmUI caches its model list at startup;
  `API/TriggerRefresh` picks up a file added while it runs.
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
3. `woomixAnima_v15` is a **different lineage** (28 blocks against the base's
   40), so it is not a valid merge donor for the 2.9B base and the merger will
   refuse it. Use a fine-tune of the same base.

## Updating

To move to a new Anima release: update `anima-manifest.json` (URL, bytes,
SHA-256) and re-run the installer. The manifest is the source of truth and no
model files are committed to this repo.
