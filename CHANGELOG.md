# Changelog

## v0.5.0-rc.2 - second candidate

### Fixed

- **Integer and bool tensors crashed the writer** (`KeyError: I64`); full
  checkpoints carry step counters and masks. The dtype table now covers every
  safetensors dtype, non-float tensors are copied from the base verbatim, and
  they are reported separately instead of counted as "skipped".
- **An output path equal to an input checkpoint** is refused, with or without
  `--force`.
- **Inherited identity metadata** (`author`, `license`, `license_url`, `date`)
  is dropped like `workflow`, `prompt` and `hash_sha256` already were.
- **Dependency constraints were dropped** (`numpy>=1.26.4` became `numpy`) and
  pip was invoked through a concatenated shell string. Requirements now travel as
  argv with their constraints, torch is never replaced, and an unsatisfied
  requirement fails the install instead of printing a warning.
- **Standalone ComfyUI could not receive node packs**: `Get-ComfyNodeDir`
  required SwarmUI. It now resolves standalone roots (env, portable, venv).

### Changed

- Workflow and MCP validation return **UNVERIFIED (exit 3)** instead of PASS when
  they cannot verify; CI turns that into a warning, never a silent pass.
- The workflow validator no longer hardcodes one machine: paths come from
  `COMFYUI_MODEL_DIRS` / `COMFYUI_ROOT`, and `--self-test` proves the lineage gate
  with synthetic 40-block and 28-block headers.
- Mirror backups are GUID-suffixed, copies are verified by SHA-256, and a failed
  replacement restores the file it displaced.
- `TESTS/Test-Version.ps1` fails when VERSION, CHANGELOG and STATE disagree.

## v0.5.0-rc.1 - release candidate

### Fixed

- **Four-way merges silently used three donors.** A fallback that anchored on
  donor #1 also removed it from the donor list, so weights renormalised over the
  rest: 31.48/27.78/22.22/18.52 became 0/40.54/32.43/27.03. The anchor now keeps
  its weight (zero task vector) and the fallback is opt-in.
- **The mirror could delete the file it was mirroring** and verified links by
  name. It now compares volume serial + file id, detects in-place targets, sets
  collisions aside and verifies copies.
- **Node-pack dependencies were never installed.** Requirements are read from each
  pack's manifest and installed into the frontend's own interpreter.
- **MCP config pointed at 8188.** `comfyui-mcp` ignores `COMFYUI_HOST`/`PORT`;
  `--comfyui-url` is what it honours.
- **The shipped workflow paired a 40-block base with 28-block donors.**

## v0.4.2 — keep the kit generic

A model-release package (`civitai/`) was committed here for a personal
checkpoint. That was out of scope for a general toolkit and has been removed;
those materials live outside this repo. The merge fixes from v0.4.0 are
unaffected.

## v0.4.0 — full checkpoints and honest hashes

### Fixed

- **Merging a full checkpoint crashed.** Scalar (0-dim) tensors cannot be viewed
  as `uint8`; `tensor_bytes` now reshapes first. This blocked every merge whose
  base carried an encoder or VAE.
- **The reported `sha256` was not the file's hash.** The writer digested
  `header || data` and skipped the 8-byte length prefix, so the printed value
  never matched `sha256sum` — a hash you could not publish. The prefix is now
  included; the printed digest and an independent `sha256sum` agree.
- **`merge` could claim a link that did not exist.** `New-Item -ItemType HardLink`
  reported success while leaving a separate copy in ComfyUI's tree. The link is
  now made with `mklink /H` and verified with `fsutil hardlink list`.

## v0.3.1 — a merge no longer pretends to be its base

### Fixed

- A merged checkpoint inherited the base's `workflow` and `prompt` blocks and its
  `modelspec.hash_sha256`, so the file claimed to be the base model (and the hash
  no longer matched the weights). Both are dropped; `modelspec.title`,
  `modelspec.description` and `modelspec.merge` now describe the blend, and the
  topology facts (`architecture`, `resolution`) are kept.
- Verified the merge math independently: reconstructed output vs the formula over
  12 tensors, max deviation `0.0008` (bf16 rounding).

## v0.3.0 — RES4LYF samplers in SwarmUI

### Added

- `install` clones **RES4LYF** alongside HaDeS, so `beta57`, `bong_tangent`,
  `res_2s`, `res_2m` and `rk_beta` exist. SwarmUI merges ComfyUI's `KSampler`
  lists into its own dropdowns at backend connect, so they become selectable
  after a restart (schedulers 9 → 11, samplers 45 → 64 here).
- Three presets on the new schedules: `Anima - Official Best (ER-SDE + beta57)`,
  `Anima - Author Daily (Euler + beta57)`, `Anima - Combo (res_2s + bong_tangent)`.
- `install` also installs `pywavelets`, which RES4LYF imports.

### Fixed

- `presets/ANIMA-HANDOFF.md` claimed RES4LYF names could not be used from
  SwarmUI. That was wrong: `ComfyUIBackendExtension.AssignValuesFromRaw` merges
  the live KSampler list. Corrected with the measured numbers.
- Documented that HaDeS's `anima_beta57` is `beta57` (same beta 0.5/0.7 schedule).

## v0.2.1 — where the merge actually lands

### Fixed

- A merge was written only to SwarmUI's `Models\Stable-Diffusion\Anima\`, so
  ComfyUI, the HaDeS workflow and the MCP could not see it. `merge` now hard-links
  the result into ComfyUI's `models\diffusion_models\` as well (a copy only when
  the trees are on different volumes). `-NoMirror` opts out.
- `LICENSE` carried a trailing note that made GitHub classify the repo as
  `NOASSERTION`. It is plain MIT again; the third-party note lives in the README.

### Repo

- `main` is protected: the `test` check is required, force pushes and branch
  deletion are blocked, and conversation resolution is on.
- Secret scanning, push protection and Dependabot security updates enabled.

## v0.2.0 — comfy-kit

Renamed from `uabs-image-stack`, and the four-way merge now works on real files.

### Added

- `install` fetches **everything** that is missing, not just models: the Anima
  custom node pack (`custom_nodes/hades-anima-suite`), `safetensors`/`numpy` in
  ComfyUI's python, and a check for Node.js. torch is never replaced — ComfyUI's
  CUDA build stays untouched.
- `status` reports each requirement with the command that fixes it.
- `merge` anchors on the first donor automatically when the requested base is a
  different Anima lineage, and says so. An explicit `-Base` is never overridden.
- `docs/banner.svg`, a real sample render in the README.

### Fixed

- **The 40-block trap.** `Anima-2.9B-preview-v1` has 40 blocks; the four Anima
  fine-tunes have 28. Blending them by name loads fine and renders pure black.
  The merger now returns a distinct exit code for that case and the CLI retries
  with a same-lineage anchor instead of writing a dead checkpoint.
- `move explicit-base capture`: the retry never fired because the default base
  was assigned before the "did the user pick one" check.

### Verified

- Four-donor merge (0.85/0.75/0.60/0.50) anchored on `Versetility`: 567 tensors
  merged, 4.18 GB written, rendered a coherent 1024×1024 image with
  `Anima - Official Best (ER-SDE + SGM)`.

## v0.1.0 — first usable starter pack

Extracted from Ultimate-AI-Starter-Bundle and turned into something a new user
can actually run.

### Added

- `TOOLS/image-stack.ps1` — one entry point: `status`, `install`, `presets`,
  `generate`, `merge`, `mcp`. Reads the installer's state file instead of
  rescanning drives, talks to SwarmUI's HTTP API, and discovers the ComfyUI port
  by probing `/system_stats`.
- `TOOLS/merge_anima.py` — offline Anima checkpoint merger. Writes a real
  `.safetensors` with bounded memory (streamed tensor by tensor), records the
  merge settings in `modelspec.merge`, and reproduces the
  `HaDeSAnimaMultiModelMerger` Task Arithmetic formula exactly.
- `workflows/anima-4way-merge.json` — the 4-way merge + generate graph, with the
  operator's donor weights (0.85 / 0.75 / 0.60 / 0.50, scale 1.0).
- `docs/MERGE.md` — how a merge works, the parameter meanings, compatibility
  rules and how to check the result.
- `TESTS/test_merge.py` — 18 assertions on the merge math, namespaces, adapter
  protection, dtype handling and refusal cases.
- `TESTS/Test-PsSyntax.ps1` — parse gate for the PowerShell tools.
- `TESTS/test_mcp_handshake.py` — starts `comfyui-mcp`, completes the MCP
  handshake and lists the tools it exposes.
- `.github/workflows/ci.yml` — runs all of the above on every push.

### Fixed

- MCP wiring: the upstream server defaults to ComfyUI on 8188, which is wrong
  for a SwarmUI install (its backend starts at 7820). The `mcp` verb now probes
  the live port and emits the correct config.
- Presets carry `vae: "Automatic"`, which SwarmUI rejects outright. The CLI
  strips it before sending.
- The merged-checkpoint download URL was built as `View/View/...` because
  SwarmUI already prefixes returned image paths with `View/`.

### Replaced

- A vendored 168 MB checkout of the third-party `comfyui-mcp` server is no longer
  part of the project. It is used as a pinned npm dependency
  (`comfyui-mcp@0.52.206`) instead.
