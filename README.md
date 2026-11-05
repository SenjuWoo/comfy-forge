# UABS Image Stack

Standalone repo for the SwarmUI / ComfyUI preset set and the Anima model
installer that used to live inside Ultimate-AI-Starter-Bundle.

## What is here

- `presets/presets.json` — 39 frontend presets (data, not a workflow graph).
- `presets/anima-manifest.json` — pinned Anima files: HTTPS URL, byte size, SHA-256.
- `presets/ANIMA-HANDOFF.md` — the operator facts and the SwarmUI import steps.
- `TOOLS/Ensure-AnimaSwarmUI.ps1` — detects SwarmUI/ComfyUI, downloads only missing
  files, verifies the pinned hash, never installs when neither frontend exists.
- `TESTS/Test-AnimaInstall.ps1` — 15 stub checks: gate, bounded discovery, offline.

## Run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS/Ensure-AnimaSwarmUI.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS/Ensure-AnimaSwarmUI.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS/Test-AnimaInstall.ps1
```

`-CheckOnly` never downloads. State is written to
`%LOCALAPPDATA%\Ultimate-AI-Starter-Bundle\anima-swarmui.json`; change it with
`-CacheDir` if this repo should own its own state.

## Ownership

The presets were operator-provided; the Anima model files are third-party weights
from Hugging Face and keep their own licences. This repo only pins their bytes.
