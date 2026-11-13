# STATE

**Goal** — a beginner-usable Anima stack for SwarmUI/ComfyUI: install models and
node packs, render from presets, and merge checkpoints into real, loadable files.

**Version** — see `VERSION` (0.8.0). Since 0.5.1 the tag, VERSION, changelog
heading and STATE must agree exactly, enforced by `TESTS/Test-Release.ps1` and the
`release-verify` workflow that runs on every tag.

**Done**

- Four-way merge keeps every donor's weight when an anchor donor substitutes for
  a missing ancestor; shares are 31.48/27.78/22.22/18.52 at scale 1.
- `--dtype` converts only tensors that are actually blended: integer counters,
  bool masks, `.scale_weight`, protected adapters and encoder/VAE tensors keep
  their original dtype. Unsupported safetensors dtypes abort instead of being
  guessed. Verified by 56 assertions in `TESTS/test_merge.py`.
- Merge writes to a unique staging file, validates every tensor's byte length,
  reopens the file with the safetensors library, and only then publishes it; a
  failed verification leaves an existing destination untouched.
- Mirror verified by Windows file identity, not by name; copies verified by
  SHA-256; a failed replacement restores the file it displaced.
- Node-pack dependencies resolve from `requirements.txt` **and** `pyproject.toml`
  (HaDeS ships only the latter), constraints preserved, pip called with argv,
  torch never replaced. `TOOLS/check_deps.py`.
- Model downloads keep an existing mismatched checkpoint as a `.replaced-*`
  backup and restore it if publication fails (`TESTS/Test-ModelInstall.ps1`).
- Node packs are pinned by commit in `config/dependencies.lock.json`; `status`
  reports drift and never resets a checkout.
- Shipped workflows validate structurally, and block-count lineage is checked
  when models are present (UNVERIFIED otherwise).
- Live integration proven: `TESTS/integration_live.py` submitted an API-format
  graph to ComfyUI 0.44.0, with 25 HaDeS nodes loaded and `beta57` selecting,
  and returned a non-degenerate 512×512 image.

- The shipped workflow is the one under test: `tests/integration_merge_graph.py`
  converts `workflows/anima-4way-merge.json` itself (same loaders, weights,
  scale and scheduler), runs it, and proves save -> reload -> render. Verified:
  4.18 GB, 685 tensors, render mean 157.0, range 0-255.
- Merge artifacts are owned: a run refuses to overwrite an existing model,
  checks free space before a cross-volume copy, and removes every file it
  created on any failure path (`TESTS/test_merge_artifacts.py`).

**Next**

**Unverified** — the standalone (non-SwarmUI) ComfyUI install on a clean
machine. The code paths exist and are exercised on this machine, but no clean
environment has been used, so this stays UNVERIFIED rather than claimed.
PowerShell 7 beyond CI is also unverified; local checks run on 5.1.
- v0.8.0: ACE-Step 1.5 music. `TOOLS/music.py` (+ `music.ps1`) generates through ComfyUI's native nodes; six variants in `presets/music/` (upstream Comfy-Org templates, MIT); `TOOLS/Install-MusicModels.ps1` size-verifies the weights; `status` reports ready variants. Verified: `TESTS/test_music.py` (generator vs templates vs installer, stub-backend CLI runs); all six graphs x flac/mp3/opus accepted by a headless ComfyUI 0.44.0 except for the uninstalled model files; a bad key and a bad `format.quality` were rejected. **Not run: any real render.** The weights (aio is 10,025,478,736 bytes) have not been downloaded on this machine; `python TESTS/integration_music.py` is the live check and exits 3 until they are.
- v0.7.0: dataset pipeline end to end. `anima_dataset.py inspect` (read-only folder report with exact and near duplicates, sizes, tag statistics, next command), `validate` (folder or archive), booru prefix handling (`artist:` to `@name`, `rating:` to a safety tag), post-bookkeeping tags dropped and listed, Anima section order, duplicate and `--min-side` filtering, flat upload archive, `--json`. `TESTS/test_dataset_pipeline.py` drives real processes through a folder named with a space, `!`, parentheses and a non-ASCII letter. Not run: an actual upload, and the tagger path on a real collection in this release.
- v0.6.2: 3D generation widgets locked in `presets/3d/generation.json` (stock knobs; the 700,000 decimate value is a sculpt and fails the game-ready gate). `TOOLS/game-ready.ps1` delegates to Asset Forge `mesh-gameready`. Default budget `game` hard-fails above 20,000 triangles. comfy-forge does not ship a second optimizer.
- v0.6.1: 3D templates verified end-to-end (idol/pixal3d single/pixal3d multi, see docs/3D-MODELS.md); docs/PRODUCTION-WORKFLOWS.md added (AssetForge vs comfy-forge decision matrix). Pixal3D recommended overall; Idol kept for previews.
- v0.6.0: repo renamed comfy-kit -> comfy-forge (state folder keeps `%LOCALAPPDATA%\comfy-kit`); `presets/3d/` ships Idol/Pixal3D templates; `TOOLS/Install-3DModels.ps1` downloads weights opt-in; docs/3D-MODELS.md is the decision guide (props -> Pixal3D single; characters -> turnaround sheet -> Pixal3D multi-view; previews -> Idol). 3D renders UNVERIFIED (backend not running).
- MCP is proven end to end: `test_mcp_handshake.py --workflow` enqueues through the MCP tool and waits for a rendered image.

**Log** — dtype policy, transactional writer, dependency resolver, safe model
publish, pinned node packs, live integration test.
**Build/run** — `TOOLS/image-stack.ps1 <status|install|presets|generate|merge|mcp>`.
**Live test** — `python TESTS/integration_live.py --url http://127.0.0.1:7821`
(use the backend's actual port; SwarmUI increments it when one is busy).
