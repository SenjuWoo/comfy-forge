# Changelog

## v0.5.5 - the CLI surface is tested, not just parsed

0.5.4 restored `Get-NodePackDrift` and added `status` as a gate. That covered one
verb. `install`, `generate` and `merge` still had no regression coverage, and the
failure that started this - a helper deleted by a refactor - could happen again in
any of them.

`TESTS/Test-CliSurface.ps1` now checks four things:

- **every read-only verb runs**: `help`, `presets`, `status`, `mcp` must exit 0 and
  print what they claim to print
- **the call graph resolves**: both script and libraries are parsed, and any call
  to a function that is neither defined locally nor resolvable as a command fails
  the test. That is the deleted-helper bug caught statically, before a user runs it.
  `Get-NodePackDrift` also keeps a named check.
- **error paths refuse cleanly**: `merge` with no donors, `merge` with a missing
  donor file, `generate` with an unknown preset must exit non-zero with a clear
  message - and `merge` must write nothing when it refuses.
- **`install` is not run where a real SwarmUI exists**: it reports `skip` rather
  than touching an installation. On CI, with no SwarmUI, it must refuse cleanly.

Verified by mutation: renaming `Get-NodePackDrift` fails the suite three ways
(verb, call graph, named check), then passes again when restored.

## v0.5.4 - `status` works again

The 0.5.1 refactor that moved the node-pack evaluator into
`TOOLS/node-pack-lib.ps1` also deleted `Get-NodePackDrift`, which sat in the same
block. Every release since then shipped a `status` verb that threw
`CommandNotFoundException` after printing the model list. No test ran the verb, so
nothing caught it.

`Get-NodePackDrift` is restored, and `status` is now a preflight and CI check:
the verb has to run, not merely parse.

## v0.5.3 - cleanup covers the render preview

The merge test removed its checkpoint and the model link, but left the 0.35 MB
preview PNG that SaveImage writes into output/ - one file per run, forever. It is
now registered with the rest and removed unless --keep-artifacts is set.

## v0.5.2 - maintenance

Follow-up to 0.5.1. No behaviour changes for a normal run.

### Fixed

- **A failed merge test could leave 4.18 GB behind.** The saved checkpoint was
  only registered for cleanup after the file had been exposed to `UNETLoader`, so
  a failure in between (unreadable safetensors, name collision, link error) left
  the whole merge in `output/`. It is now registered the moment it exists, and a
  cross-volume copy that dies midway removes its own half-written file instead of
  leaving a truncated model. Collision refusal is unchanged, and no pre-existing
  file is ever removed.
- **`0.5.1-rc.1` satisfied the `0.5.1` heading check.** The changelog test matched
  a version prefix. It now compares the version token exactly, and the matcher is
  self-tested against `0.5.1-rc.1`, `0.5.10` and an unversioned heading.

### Added

- `TESTS/test_merge_artifacts.py` - failure injection for the merge test's
  artifacts: hard link vs copy, collision refusal, mid-copy failure, free-space
  refusal, and cleanup in keep and remove modes.

## v0.5.1 - safety patch after v0.5.0

`v0.5.0` shipped with a stable tag on a tree that still declared `0.5.0-rc.5`,
so the tag, the changelog and STATE.md disagreed. That is fixed here, and the
three user-data bugs found afterwards are closed.

### Fixed

- **The live merge test could delete a user's model.** It derived a predictable
  name under `models/diffusion_models`, unlinked whatever was there and linked the
  new merge over it. Every run now uses a unique name, refuses a collision
  outright, copies only when the volumes differ (after a free-space check) and
  removes nothing except the two files it created. `--keep-artifacts` leaves them
  for inspection.
- **The installer deleted partial files it did not own.** A `.partial` with a
  missing or unrecognised `.id` sidecar was treated as garbage. Ownership is now
  structural: staging lives in `.stage-<identity-hash>/` and only a directory
  whose `id` matches the expected `sha256|bytes` is resumed or removed. Anything
  else is left byte-for-byte where it is.
- **Crash recovery guessed from filenames.** If the destination was missing, the
  newest `.replaced-*` was moved back - including a historical backup the user had
  deliberately kept, and it ran before the lock. Recovery now happens inside the
  lock, from a transaction marker written before the move, and only for the same
  artifact identity. A complete staged file is published without another network
  request.
- **Resume did not work, and a 416 crashed the install.** Staging names changed
  per run, so `curl -C -` never had a partial. A server that rejects ranges
  (curl exit 33) is now handled as "restart clean" instead of an error the user
  cannot act on.
- **The shipped workflow was never executed by the tests.** The live test rebuilt
  an equivalent graph in Python with different weights and a bypassed saver. It
  now converts `workflows/anima-4way-merge.json` itself - the same loaders, the
  same weights, the same `anima_beta57` scheduler - and the saver is enabled by
  default so a beginner actually gets a file.
- **A failing `git status` could read as `locked`.** It is now `unverifiable`,
  because a status command that did not run proves nothing about the tree.
- **A failed clone blocked every later install.** A pack folder without `.git` is
  moved aside (contents preserved) instead of making `git clone` fail forever; a
  real checkout is never touched or reset.
- **The state file was written into another tool's folder.** `comfy-kit` now owns
  `%LOCALAPPDATA%\comfy-kit`, while still reading a record left at the old
  bundle path.

### Added

- `TESTS/workflow_to_api.py` - converts the shipped UI workflow with explicit
  widget tables and hard failures on any mismatch.
- `TESTS/test_workflow_convert.py` - asserts the shipped recipe, the enabled
  saver and that drift (short widget list, unknown class) is rejected.
- `TESTS/Test-Release.ps1` - VERSION, top changelog heading, STATE and (on a tag)
  the tag name must agree; a released tag may not declare an RC version.
- `.github/workflows/release-verify.yml` - runs those checks on the tagged tree.
- `TOOLS/node-pack-lib.ps1` + `TESTS/Test-NodePackState.ps1` - real temporary git
  repositories covering locked, dirty, drift, foreign, no-origin, failing-status
  and partial-clone states.
- `TESTS/Test-ModelInstall.ps1` now drives real HTTP against
  `TESTS/fixtures/http_range_server.py`: Range honoured (206), ignored (200),
  rejected (416), complete-partial, and user-owned partial preservation.

## v0.5.0-rc.5 - correctness and honest tests

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0, HaDeS 0.4.7)

```
integration_merge_graph.py
  donors 4 | merger HaDeSAnimaMultiModelMerger | method Task Arithmetic
  saver  -> S:\...\output\comfy-kit-4way-proof_00001_.safetensors (4.18 GB)
  saved  685 tensors, loadable by safetensors
  visible to UNETLoader in models/diffusion_models
  render mean=157.0 range=0-255
  MERGE GRAPH: PASS (merge -> save -> reload -> render)
```

This is the shipped four-way path executed end to end for the first time - the
previous integration test only generated an image from an existing checkpoint.


### Fixed

- **The fallback merge dropped `--report`.** The retry rebuilt its argument list
  by hand, so a full checkpoint merged through `-AcceptFallbackAnchor` was
  reported as diffusion-only and routed to `models/diffusion_models`, where
  `CheckpointLoaderSimple` cannot see it. One argument builder now serves every
  execution, and a missing, unreadable or mismatched report stops the run instead
  of guessing a category.
- **"Full checkpoint" now means model + encoder + VAE.** A file with only one of
  the two is treated as diffusion-only and says so.
- **`Test-NodePack` still meant "the folder exists".** Status and installation now
  share one state evaluator; only `locked` or `dirty` counts as installed.
- **The pack list was duplicated** between a hardcoded array and the lock. The
  lock is now the only source, a missing lock refuses to install anything, and
  clone/checkout/verification failures all reach the exit code.
- **A missing git origin could still read as `locked`**, and equivalent GitHub
  origin spellings were compared as raw strings. Origin is now normalised and an
  absent origin is `unverifiable`.
- **Resumable downloads were not resumable.** Staging used a fresh GUID per run,
  so `curl -C -` never had a partial to continue. The partial is now stable and
  tied to the expected SHA-256 and size, a partial from a different artifact is
  discarded, a second installer is refused by a lock file, and a server that
  ignores the range is detected and restarted cleanly.
- **The publication-failure test never reached publication.** It placed a
  directory at the destination, which the installer rejects before downloading.
  There is now a publication seam that fails after the backup exists, plus crash
  recovery for the window between `move aside` and `publish`, and an explicit
  message naming the backup when a restore itself fails.
- **First-run installs failed on a missing model category.** The staging
  directory is created with the destination.
- **`validate-lock.py` could turn a 404 into UNVERIFIED.** It now confirms the
  repository is reachable before deciding, classifies 401/403/rate-limit as
  unknown, rejects malformed entries and non-object packs, and has a mock-based
  self-test.
- **`check_deps.py` exited 0 with `status: failed`.** Exit 2 now means a mandatory
  requirement is missing or failed; `--report-only` is for the installer, which
  installs what is missing.
- **MCP reads had no deadline** and the port probe accepted any open socket. Reads
  are bounded, and the probe requires a real `/system_stats` answer.
- **Preflight judged a command that never launched by the previous exit code.**
  It now reports "could not run" and can emit JSON evidence with commit, version
  and per-check state.

### Added

- `TESTS/integration_merge_graph.py` - the four-way HaDeS graph through the real
  API: merge, save, reload through `UNETLoader`, render, with pixel verification.
- `TESTS/fixtures/http_range_server.py` - a local fixture that honours or ignores
  `Range:`, used to prove the resume logic against real bytes.

## v0.5.0-rc.4 - closeout

### Fixed

- **A logging helper that does not exist could lose a checkpoint.** The model
  installer moved an existing file aside, then called `Write-UabsNote` - defined
  nowhere in this repo - and the backup move sat outside the `try`. One missing
  function and the destination was gone. Model installation is now one
  transaction in `TOOLS/model-install-lib.ps1`: stage, verify size and SHA-256,
  back up, publish, roll back. Logging failures are swallowed by design.
- **The staging path was doubled.** The wrapper passed `file.part` into a function
  that appended `.part` again. There is now exactly one staging path, unique per
  run, cleaned only by the operation that owns it.
- **The lock pinned a commit that does not exist.** `fb2b26f` is a ComfyUI
  commit: the build machine's pack folder carried ComfyUI's `.git`, so the
  installer recorded the wrong repository's HEAD. HaDeS is now pinned to
  `3c0a603` (v0.4.7), verified by cloning it and confirming 25 node classes,
  including `HaDeSAnimaMultiModelMerger` and `HaDeSAnimaModelSaver`.
- **A failed checkout counted as installed.** Install now verifies HEAD after
  checkout and fails; pack state distinguishes `locked / drift / dirty /
  foreign-repo / unverifiable / missing` instead of "folder exists".
- **Encoder and VAE tensors could be blended.** The merge now classifies every
  tensor (`diffusion / encoder / vae / adapter / scale / other`) and only
  diffusion weights merge; everything else is copied from the base.
- **A checkpoint with two spellings of one tensor** (`net.x` and
  `model.diffusion_model.x`) was merged by silently picking one; it is refused.
- **Merge failures deleted an unrelated `<output>.part`.** The writer owns its
  unique staging file; the outer handler no longer touches anything.
- **Full checkpoints were mirrored into `diffusion_models`**, where
  `CheckpointLoaderSimple` cannot see them. The merge now reports whether the
  result carries encoder+VAE and routes it to `models/checkpoints` or
  `models/diffusion_models` accordingly.
- **Dependency validation could pass while unresolved.** Optional extras are no
  longer treated as mandatory, a missing `packaging` is a failed validation, and
  unsupported or unverified requirements make the install exit nonzero.
- **`Extensions` was treated as `custom_nodes`.** Removed: a node pack installed
  there registers nothing.

### Added

- `TOOLS/validate-lock.py` - schema plus "does this commit exist in the repo the
  lock names", with UNVERIFIED when the network cannot answer.
- `TOOLS/preflight.ps1` - one command, every local check, an explicit release
  verdict that blocks on failures *and* on unverified checks.
- `docs/MAINTAINERS.md` - ownership map, the rules that exist because they were
  violated, and the release sequence.
- MCP proof is now a real workflow: `TESTS/test_mcp_handshake.py --workflow`
  enqueues through the MCP tool and waits for a rendered image.

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0, HaDeS 0.4.7)

```
integration_live.py --url http://127.0.0.1:7821
  backend ComfyUI 0.44.0 | HaDeS 25 nodes | euler/beta57 | 512x512 mean=119.4
  INTEGRATION: PASS
test_mcp_handshake.py --workflow
  generate_image -> enqueued 665c13d5 | rendered ComfyUI_00001_.png
  MCP: PASS (workflow executed)
```

## v0.5.0-rc.3 - third candidate

### Fixed

- **`--dtype` rewrote tensors it must not touch.** The requested dtype was
  assigned to every tensor, so `--dtype fp16` turned an int64 counter, a bool
  mask, a `.scale_weight`, a protected adapter or an encoder tensor into FP16.
  Conversion now applies only to tensors that are actually blended; everything
  else keeps its original dtype and bytes. Non-float tensors are never converted.
- **Unknown safetensors dtypes crashed with `KeyError`** or would have been
  guessed. FP8 and complex dtypes are now sized for byte-exact copying, and any
  dtype this build cannot describe aborts the merge before writing anything.
- **The writer replaced the destination before proving the file was loadable.**
  It now stages to a unique temporary file, checks every tensor's byte length
  against the header, reopens the result with the safetensors library, and only
  then publishes. A failed verification leaves an existing destination intact and
  cleans up its staging file.
- **Input/output identity was compared as path text.** It now uses real file
  identity, so relative paths, hard links and symlinks are caught.
- **HaDeS declares its dependencies in `pyproject.toml`**, which the installer
  never read: it looked only for `requirements.txt` and found nothing. A new
  resolver (`TOOLS/check_deps.py`) reads both, keeps version constraints, reports
  directives it cannot replay instead of dropping them, and is covered by tests.
- **`Find-Python` returned the bare name `python`**, which `Test-Path` cannot
  test, so dependency work silently skipped. It now returns a verified executable
  path, and refuses to fall back to the global interpreter when a frontend exists
  without one.
- **A model download could destroy an existing checkpoint** whose filename
  matched the manifest but whose bytes did not. The installer now keeps it as a
  `.replaced-*` backup and restores it if publication fails.
- **`image-stack.ps1` contained two copies of every function**, the second older
  than the first: PowerShell executed the stale one, so new code was dead.
  Removed 281 duplicate lines.

### Added

- `config/dependencies.lock.json` pins HaDeS and RES4LYF by commit, with the
  node classes they provide. `install` checks out the pin; `status` reports drift
  and never resets a checkout.
- `TESTS/integration_live.py` submits a real API-format graph to a live ComfyUI,
  waits for completion, downloads the image and rejects a flat/black frame.
- `docs/TESTING.md`, `docs/UPDATING.md`, `TESTS/Test-ModelInstall.ps1`,
  `TOOLS/check_deps.py --self-test`, and PowerShell 5.1 steps in CI.

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0)

```
backend     ComfyUI 0.44.0
nodes       1432 registered
HaDeS       25 nodes, e.g. HaDeSAnimaModelBlockMerger
checkpoint  Anima\woomixAnima_v175.safetensors
sampler     euler / beta57
image       comfy-kit-integration_00001_.png (359817 bytes)
pixels      512x512 mean=119.4 range=0-255
INTEGRATION: PASS
```

This is the first time the shipped path is proven to execute: HaDeS had never
registered on the live backend before this round (a stale process was serving
the old node list).

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
