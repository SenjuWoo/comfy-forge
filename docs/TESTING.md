# Testing comfy-forge

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
The checkpoint and scheduler are named, and a substitute is refused unless
`--allow-any` is passed; without Pillow the result is UNVERIFIED, because an
image arriving does not prove it rendered.

The MCP surface is proven separately, including a real submission:

```powershell
python TESTS	est_mcp_handshake.py --workflow
```

That enqueues through the MCP tool and waits for the job to produce an image.

## The four-way merge graph (live, slow)

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TESTS\integration_merge_graph.py --url http://127.0.0.1:7821
```

Builds the graph from the live node schemas, runs `HaDeSAnimaMultiModelMerger`,
saves with `HaDeSAnimaModelSaver`, reloads the saved file through `UNETLoader`
and renders. It loads five models and can take several minutes; `--dry-run`
checks the graph against `/object_info` without running it. This is the only
check that proves the shipped merge path end to end, and CI cannot run it.

It executes `workflows/anima-4way-merge.json` itself (converted by
`TESTS/workflow_to_api.py`), so the weights, scale, loaders and scheduler are the
ones the shipped file advertises. Every run uses a unique output name, refuses to
overwrite an existing model, and deletes only the two files it created - pass
`--keep-artifacts` to inspect them.

## Safety regressions (fast, but need git and python)

```powershell
./TESTS/Test-ModelInstall.ps1   # ownership, crash recovery, real HTTP resume
./TESTS/Test-NodePackState.ps1  # locked/dirty/drift/foreign/status-failure, clone recovery
./TESTS/Test-Release.ps1        # VERSION, changelog, STATE and tag agree exactly
python TESTS/test_workflow_convert.py   # shipped workflow is executable, drift rejected
python TESTS/test_merge_artifacts.py    # merge output ownership, cleanup, failed copy
```

`Test-ModelInstall.ps1` starts `TESTS/fixtures/http_range_server.py` and lets real
curl talk to it, so a resume is proven by an actual `Range: bytes=N-` request
rather than a mocked downloader.

## Node-pack dependency checks

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TOOLS\check_deps.py --pack "S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes\hades-anima-suite"
```

Reads `requirements.txt` and `pyproject.toml`, keeps version constraints, and
reports requirements it cannot replay instead of dropping them.
