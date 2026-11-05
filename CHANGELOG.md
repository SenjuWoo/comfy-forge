# Changelog

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
