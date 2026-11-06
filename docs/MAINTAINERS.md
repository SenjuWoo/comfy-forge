# Maintainer map

Short version: one owner per job, one source of truth per fact. If a change does
not fit one of these, it probably belongs in none of them.

## Who owns what

| Concern | File |
|---|---|
| User-facing CLI (`status/install/presets/generate/merge/mcp`) | `TOOLS/image-stack.ps1` |
| Model download + frontend discovery | `TOOLS/Ensure-AnimaSwarmUI.ps1` |
| Model install transaction (stage → verify → publish → rollback) | `TOOLS/model-install-lib.ps1` |
| Merge math, tensor roles, writer | `TOOLS/merge_anima.py` |
| Mirroring into ComfyUI by file identity | `TOOLS/mirror-lib.ps1` |
| Dependency resolution (`requirements.txt` + `pyproject.toml`) | `TOOLS/check_deps.py` |
| Pinned third-party revisions | `config/dependencies.lock.json` |
| Lock validation | `TOOLS/validate-lock.py` |
| All local checks + release verdict | `TOOLS/preflight.ps1` |
| Live GPU proof | `TESTS/integration_live.py` |
| MCP proof | `TESTS/test_mcp_handshake.py` |

Versioning: `VERSION` is the only canonical version. `CHANGELOG.md` must have a
matching heading and `STATE.md` must name the same version — `TESTS/Test-Version.ps1`
fails otherwise.

## Rules that exist because they were violated

1. **Only diffusion tensors merge.** Encoder, VAE, adapters, `.scale_weight` and
   unknown keys are copied from the base. `tensor_role()` in `merge_anima.py` is
   the single classifier; do not add a second one.
2. **A merge may only delete the staging file it created.** Its name is unique per
   run. Never clean up `<output>.part` — that is somebody else's file.
3. **A download never overwrites a user's checkpoint without a backup**, and a
   failed publication restores the previous file.
4. **A lock pin must resolve in the repository the lock names.** `TOOLS/validate-lock.py`
   asks GitHub. This is how a ComfyUI commit was once recorded as a HaDeS pin.
5. **`Test-NodePack`-style "the folder exists" is not installed.** States are
   `locked | drift | dirty | foreign-repo | unverifiable | missing`; a dirty
   checkout is never reset.
6. **UNVERIFIED is not PASS.** Exit code 3 from any validator means the check
   could not run, and CI turns it into a warning rather than a green tick.
7. **The installer must not depend on helpers that may not exist.** The fallback
   path defines only `Write-UabsStep/Ok/Info/Warn/Bad`.

## Full checkpoint vs diffusion-only

`merge_anima.py --report` writes a small JSON summary; `image-stack.ps1` uses
`full_checkpoint` to choose the model folder:

- `models/checkpoints` — has encoder + VAE, loadable by `CheckpointLoaderSimple`.
- `models/diffusion_models` — UNET only, needs a diffusion loader.

Putting a full checkpoint in `diffusion_models` makes it invisible to
`CheckpointLoaderSimple` while still "existing on disk".

## Verify before releasing

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS/preflight.ps1
```

Then, with a backend running:

```powershell
& $env:COMFY_PYTHON TESTS\integration_live.py --url http://127.0.0.1:7821
& $env:COMFY_PYTHON TESTS\test_mcp_handshake.py --workflow
```

The preflight deliberately blocks on any UNVERIFIED check: a release is not
eligible until every check either passed or was replaced with real evidence.

## Known limits

- The four-way merge graph is not executed end-to-end by CI; it needs the 5.6 GB
  donor set and minutes of GPU work.
- Standalone (non-SwarmUI) ComfyUI install has code paths but no clean-machine
  proof.
- The HaDeS save node is exercised through the CLI, not through the UI graph.
