# comfy-forge agent runbook

comfy-forge is the generative lane for an agent that can only write text and code: it turns a prompt into an image, a song or a 3D model through a local SwarmUI/ComfyUI, and hands the result to Asset Forge when it has to become a game asset. It also keeps that stack installed (Anima models, node packs), merges checkpoints into real files, and prepares tagged image folders for LoRA training elsewhere. It does not train and does not upload.

## Cold start

```text
powershell -File TOOLS\image-stack.ps1 capabilities   one JSON object: every lane, ready or not, the command for each
powershell -File TOOLS\image-stack.ps1 status         the same for a person
powershell -File TOOLS\image-stack.ps1 --help         every verb and option
```

No server has to be running. `generate`, `model3d.py generate` and `music.py generate` start SwarmUI headless (no window, no browser) when nothing answers, and leave it running for the next job. `image-stack.ps1 stop` ends a backend this tool started and never one a person started. `-NoStart` / `--no-start` turn the automatic start off.

## Jobs and their owners

| Job | Command | Guide |
|---|---|---|
| Image from text | `TOOLS\image-stack.ps1 generate -Prompt ... -Out DIR -Json` | `docs/IMAGES.md` |
| Edit an image, inpaint, LoRA, transparent sprite, tileable texture, upscale | the same verb with `-InitImage`, `-Mask`, `-Lora`, `-Transparent`, `-Tileable`, `-Upscale`, `-Set` | `docs/IMAGES.md` |
| 3D model from a picture | `python TOOLS/model3d.py generate --image F --name N --out DIR --game-ready` | `docs/3D-MODELS.md` |
| 3D model from text | `python TOOLS/model3d.py generate --prompt "..." --name N --out DIR --game-ready` | `docs/3D-MODELS.md` |
| Character mesh from a turnaround | `python TOOLS/model3d.py generate --variant pixal3d-multi --image sheet.png` or `--views front,left,back,right` | `docs/3D-MODELS.md` |
| Generated `.glb` to a mesh under the triangle budget | `--game-ready` above, or `TOOLS\game-ready.ps1 -Input model.glb` (Asset Forge does the work) | `docs/GAME-READY.md` |
| Song or instrumental | `python TOOLS/music.py check\|generate` (`TOOLS\music.ps1`) | `docs/MUSIC.md` |
| Start or stop the backend by hand | `TOOLS\image-stack.ps1 start` / `stop` | `README.md` |
| Tagged folder to training archive | `python TOOLS/anima_dataset.py inspect\|build\|validate` | `docs/DATASET.md` |
| Install or repair models and node packs | `TOOLS\image-stack.ps1 install` | `docs/UPDATING.md` |
| Fetch 3D or music weights | `TOOLS\Install-3DModels.ps1 -WhatIf`, `TOOLS\Install-MusicModels.ps1 -WhatIf`, then `-Set ...` | `docs/3D-MODELS.md`, `docs/MUSIC.md` |
| Merge checkpoints | `TOOLS\image-stack.ps1 merge -Donors a,b` | `docs/MERGE.md` |
| Wire an agent to ComfyUI | `TOOLS\image-stack.ps1 mcp` | `README.md` |

## Reading results

Every generating command ends with one JSON line and writes a `.json` sidecar beside each file it saves.

- `generate -Json`: `files`, `seed`, `seeds`, `model`, `preset`, `width`, `height`.
- `model3d.py generate`: `model` (the `.glb`), `splat`, `files`, `triangles`, `game_ready`, `game_ready_triangles`, `game_ready_error`, `image`.
- `music.py generate`: `files`, `seeds`, `takes`.

Exit codes: `image-stack.ps1` 0 done, 1 refused or failed. `model3d.py` and `music.py` 0 done, 1 failed, 2 bad arguments, 3 no backend. `model3d.py --game-ready` exits 1 when only the game-ready step failed; the closing line still names the model that was made.

Nobody looks at the picture, the mesh or the song for you. Open the file before you call it good.

## Image work

1. `generate` picks the model: `-Model` (a full name or a bare file name), else the preset's own, else `COMFY_FORGE_MODEL`, else the first model SwarmUI lists for the preset's family. It prints the choice. A name that fits two files is refused.
2. A preset is sampler settings, not a model: an Anima preset with an SDXL model is noise. Keep the family the same.
3. `-Set key=value` sends any SwarmUI parameter by its id (`aspectratio=16:9`); list the ids with SwarmUI's `ListT2IParams` API.
4. Call `image-stack.ps1` itself for images. Do not route image arguments through `music.ps1`, `model3d.ps1` or `dataset.ps1`.

## 3D work

1. `check --variant all` says which variants have their weights. Weights are opt-in downloads; never download without the operator's go-ahead.
2. The graph that runs is the template file under `presets/3d/`, converted at run time. Change a widget in the template and `TESTS/test_3d_generation.py` fails; that is the point. Do not retune sampler, CFG, the 700,000 decimate or the bake size to get a smaller file.
3. The `.glb` a template writes is a sculpt of about 700,000 triangles. `--game-ready` (or `TOOLS\game-ready.ps1`) is how it becomes a game mesh; Asset Forge owns that step and this repo ships no second optimizer.
4. `--prompt` renders one picture first (same rules as image work) and adds framing words for a single object on a plain background; `--raw-prompt` sends the text as written.
5. Rigging, animation and full-character budgets are not here.

## Dataset work

1. `inspect` first. It is read-only and its last line is the build command that fits the folder.
2. Build with `--mode convert` when every image already has a `.txt`: no tagger, no model, deterministic text work. Existing tags are kept; nothing is replaced by generated prose.
3. Read the result (`--json`, or `_review.tsv`). Exit `2` means images were left out; each has a status and a reason.
4. `validate` the archive before handing it over.

Call the Python tool directly, or `TOOLS\dataset.ps1` from PowerShell. Do not route dataset arguments through `image-stack.ps1`: PowerShell binds `--out` and `--mode` to that script's own parameters.

## Music work

1. `check` first (`--variant all` lists every variant). It finds the backend itself: `--url`, `COMFYUI_URL`, then ports 8188 and 7820-7832. Exit 3 means no backend, 1 means a node or model file is missing (it names the installer set).
2. `build` writes the API graph with no backend, for review or for another submitter.
3. `generate` prints one JSON line last (`files`, `seeds`, `takes`); every audio file has a `<file>.json` sidecar. Reproduce or vary a take with `--from <sidecar>` (plus `--reseed` for new seeds). Warnings (early silence, long lyrics) go to stderr.
4. With no `--url` and nothing answering, `generate` starts the backend headless. An explicit `--url` or `COMFYUI_URL` is never replaced by another server.
5. Call `TOOLS\music.ps1` or `music.py` directly. Do not route music arguments through `image-stack.ps1` (`--out`, `--seed`, `--format` bind to its parameters).
6. The six `presets/music/` files are upstream templates; `TESTS/test_music.py` fails if the generator drifts from them. Change a variant by changing both together.
7. Weights are opt-in downloads (up to ~28 GB for `all`). Never download without the operator's go-ahead; `-WhatIf` lists the files.
8. Sound effects and game-audio production belong to Asset Forge, not here. Asset Forge's `audio-music` recipe calls `music.py generate`; keep its flags and the closing JSON line compatible.

## Working with Asset Forge

- Asset Forge calls `image-stack.ps1 generate` (`comfy-generate`) and `music.py generate` (`audio-music`). Keep `-Prompt -Preset -Model -Width -Height -Seed -Count -Steps -Cfg -Out -Port` and the closing JSON lines compatible.
- `-Port` names a SwarmUI the caller runs; `generate` then never starts one of its own.
- comfy-forge calls Asset Forge only through `TOOLS\game-ready.ps1` (`mesh-gameready`). Do not edit Asset Forge from here: record a defect with the bundle's `field_report.py asset-forge`.
- Details and what is verified: `docs/ASSETFORGE.md`.

## Sources of truth

- Version: `VERSION`. The top `CHANGELOG.md` heading, `STATE.md` and the release tag must match exactly (`TESTS/Test-Release.ps1`).
- Node-pack pins: `config/dependencies.lock.json` (`TOOLS/validate-lock.py`).
- 3D generation settings: `presets/3d/generation.json`; the templates beside it are what runs.
- Progress, evidence and what is unverified: `STATE.md`. Open defects reported by other tools: `FIELD-REPORTS.md` (local, when present).

## Boundaries

- Source image folders are read-only; output goes to a new folder.
- Models, merges and node packs: never overwrite a file the tool does not own; `status` reports drift and resets nothing.
- No training, no upload, no account action.
- A structural check is not a render. Say which tests ran and which need a running backend.

## Tests

`TOOLS/preflight.ps1` runs every local check. These need only Python (and Pillow for the dataset suites):

```text
python TESTS/test_generate.py         # start/stop/generate/capabilities against a stand-in backend
python TESTS/test_model3d.py          # templates to API graphs, lock, installer, CLI, glTF repair
python TESTS/test_dataset_prep.py
python TESTS/test_dataset_pipeline.py
python TESTS/test_dataset_regressions.py
python TESTS/test_mcp_config.py       # mcp -Write keeps the rest of an agent config
python TESTS/test_installers.py       # weight installers: root discovery, missing drive
python TESTS/test_music.py            # templates vs generator vs installer, stub backend
python TESTS/integration_music.py     # needs a backend and the weights; exit 3 = unverified
```
