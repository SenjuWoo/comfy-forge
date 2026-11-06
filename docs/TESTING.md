# Testing comfy-kit

Two layers. The fast layer runs anywhere in about a minute; the live layer needs
a real ComfyUI with models and a GPU, and it is the only layer that proves the
installation actually renders.

## Fast layer (no GPU, no backend, no network)

Run from the repo root in **Windows PowerShell 5.1**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-PsSyntax.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-AnimaInstall.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-Mirror.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-ModelInstall.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-Version.ps1
python TESTS\test_merge.py
python TESTS\validate_workflows.py
python TESTS\validate_workflows.py --self-test
python TESTS\test_mcp_handshake.py
```

`test_merge.py` needs `torch` and `safetensors`. If the system Python lacks
them, use the interpreter ComfyUI already ships:

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TESTS\test_merge.py
```

### What each result means

| Output | Meaning |
|---|---|
| `PASS` | the check ran and passed here |
| `UNVERIFIED` (exit 3) | the check could not run: no models, no backend. **Not** a pass |
| `FAIL` (exit 1) | something is wrong |

`validate_workflows.py` prints `UNVERIFIED` on a machine without the model files;
that is expected on CI and must never be reported as a pass.

## Live layer (real backend)

1. Start SwarmUI or ComfyUI.
2. Find the backend's port — SwarmUI picks the next free one if 7821 is busy:
   `netstat -ano | findstr :78`
3. Run:

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TESTS\integration_live.py --url http://127.0.0.1:7822
```

It submits an API-format graph (not a UI-save file, which `/prompt` rejects),
waits for completion, downloads the image and checks the pixels are not flat.
Exit 0 means a real render happened; exit 3 means no backend was reachable.

## Node-pack dependency checks

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TOOLS\check_deps.py --pack "S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes\hades-anima-suite"
```

Reads `requirements.txt` and `pyproject.toml`, keeps version constraints, and
reports requirements it cannot replay instead of dropping them.
