# STATE

**Goal** — a beginner-usable Anima stack for SwarmUI/ComfyUI: install models and
node packs, render from presets, and merge checkpoints into real, loadable files.

**Version** — see `VERSION` (0.5.16). Since 0.5.1 the tag, VERSION, changelog
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
- MCP is proven end to end: `test_mcp_handshake.py --workflow` enqueues through the MCP tool and waits for a rendered image.

**Log** — dtype policy, transactional writer, dependency resolver, safe model
publish, pinned node packs, live integration test.
**Build/run** — `TOOLS/image-stack.ps1 <status|install|presets|generate|merge|mcp>`.
**Live test** — `python TESTS/integration_live.py --url http://127.0.0.1:7821`
(use the backend's actual port; SwarmUI increments it when one is busy).
