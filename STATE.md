# STATE

**Goal** — a starter pack a new user can run: install Anima, make an image, merge
checkpoints, connect an agent. Anima-focused.

**Version** — v0.1.0 (first release).

**Done**
- `TOOLS/image-stack.ps1` with `status | install | presets | generate | merge | mcp`,
  verified end to end against a live SwarmUI v0.9.8.4.
- `TOOLS/merge_anima.py`: offline Task-Arithmetic merger, streamed write, metadata
  provenance, topology gate. Self-merge is bit-identical; mismatch is refused.
- `workflows/anima-4way-merge.json` (operator graph) + `docs/MERGE.md`.
- Tests: `test_merge.py` (18), `Test-AnimaInstall.ps1` (15), `Test-PsSyntax.ps1`,
  `test_mcp_handshake.py` (comfyui-mcp 0.52.206, 3 tools).
- CI on windows-latest.

**Next**
- Prove a four-donor merge with the real donor set (files were on a `W:` path that
  is not on this machine).
- Optional: a preset import helper for headless SwarmUI sessions.

**Last known good** — v0.1.0 tag.

**Run / build**
```
powershell -File TOOLS\image-stack.ps1 status
powershell -File TESTS\Test-PsSyntax.ps1
powershell -File TESTS\Test-AnimaInstall.ps1
python TESTS\test_merge.py          # needs torch + safetensors (ComfyUI python)
```

**Logs / state**
- Installer state: `%LOCALAPPDATA%\Ultimate-AI-Starter-Bundle\anima-swarmui.json`
- Renders: `out\` (git-ignored)
- SwarmUI's own log: `<SwarmUI>\Data\logs.txt`
