# comfy-forge agent runbook

comfy-forge keeps one image stack working (SwarmUI or ComfyUI with Anima models and node packs), merges checkpoints into real files, runs image-to-3D templates, and prepares tagged image folders for LoRA training elsewhere. It does not train and does not upload.

## Cold start

```text
powershell -File TOOLS\image-stack.ps1 status      what is installed and running
powershell -File TOOLS\image-stack.ps1 help        every verb
python TOOLS/anima_dataset.py inspect --images DIR --json
```

## Jobs and their owners

| Job | Command | Guide |
|---|---|---|
| Tagged folder to training archive | `python TOOLS/anima_dataset.py inspect\|build\|validate` | `docs/DATASET.md` |
| Install or repair models and node packs | `TOOLS\image-stack.ps1 install` | `docs/UPDATING.md` |
| Render from a preset | `TOOLS\image-stack.ps1 generate -Prompt ... -Preset ...` | `README.md` |
| Merge checkpoints | `TOOLS\image-stack.ps1 merge -Donors a,b` | `docs/MERGE.md` |
| Image to 3D, then game-ready | `presets/3d/`, `TOOLS\game-ready.ps1` (delegates to Asset Forge) | `docs/3D-MODELS.md`, `docs/GAME-READY.md` |
| Wire an agent to ComfyUI | `TOOLS\image-stack.ps1 mcp` | `README.md` |

## Dataset work

1. `inspect` first. It is read-only and its last line is the build command that fits the folder.
2. Build with `--mode convert` when every image already has a `.txt`: no tagger, no model, deterministic text work. Existing tags are kept; nothing is replaced by generated prose.
3. Read the result (`--json`, or `_review.tsv`). Exit `2` means images were left out; each has a status and a reason.
4. `validate` the archive before handing it over.

Call the Python tool directly, or `TOOLS\dataset.ps1` from PowerShell. Do not route dataset arguments through `image-stack.ps1`: PowerShell binds `--out` and `--mode` to that script's own parameters.

## Sources of truth

- Version: `VERSION`. The top `CHANGELOG.md` heading, `STATE.md` and the release tag must match exactly (`TESTS/Test-Release.ps1`).
- Node-pack pins: `config/dependencies.lock.json` (`TOOLS/validate-lock.py`).
- 3D generation settings: `presets/3d/generation.json`.
- Progress, evidence and what is unverified: `STATE.md`.

## Boundaries

- Source image folders are read-only; output goes to a new folder.
- Models, merges and node packs: never overwrite a file the tool does not own; `status` reports drift and resets nothing.
- No training, no upload, no account action.
- A structural check is not a render. Say which tests ran and which need a running backend.

## Tests

`TOOLS/preflight.ps1` runs every local check. The dataset suites need only Python and Pillow:

```text
python TESTS/test_dataset_prep.py
python TESTS/test_dataset_pipeline.py
```
