# Testing comfy-forge

`<SwarmUI>` below is your SwarmUI folder. `powershell -File TOOLS\image-stack.ps1 python` prints the interpreter the stack uses, so the long path never has to be typed.

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
python TESTS\test_generate.py            # start, stop, generate, capabilities against a stand-in backend
python TESTS\test_model3d.py             # 3D templates as API graphs, lock, installer, CLI, glTF repair
python TESTS\test_music.py
python TESTS\test_mcp_config.py          # mcp -Write keeps the rest of an agent config
python TESTS\test_installers.py          # weight installers: root discovery, missing drive
python TESTS\test_dataset_regressions.py # --exclude, UTF-16 captions, stale pairs
python TESTS\validate_workflows.py
python TESTS\validate_workflows.py --self-test
python TESTS\test_mcp_handshake.py
```

`powershell -File TOOLS\preflight.ps1` runs all of them and gives one verdict.

`test_merge.py` needs `torch` and `safetensors`. If the system Python lacks
them, use the interpreter ComfyUI already ships:

```powershell
& "<SwarmUI>\dlbackend\comfy\python_embeded\python.exe" TESTS\test_merge.py
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

1. Start the backend: `powershell -File TOOLS\image-stack.ps1 start` (headless), or open
   SwarmUI or ComfyUI yourself.
2. Find the backend's port — SwarmUI picks the next free one if 7821 is busy:
   `powershell -File TOOLS\image-stack.ps1 status` prints it.
3. Run:

```powershell
& "<SwarmUI>\dlbackend\comfy\python_embeded\python.exe" TESTS\integration_live.py --url http://127.0.0.1:7822
```

It submits an API-format graph (not a UI-save file, which `/prompt` rejects),
waits for completion, downloads the image and checks the pixels are not flat.
The checkpoint and scheduler are named, and a substitute is refused unless
`--allow-any` is passed; without Pillow the result is UNVERIFIED, because an
image arriving does not prove it rendered.

The MCP surface is proven separately, including a real submission:

```powershell
python TESTS\test_mcp_handshake.py --workflow
```

That enqueues through the MCP tool and waits for the job to produce an image.

The image and 3D lanes are proven by using them:

```powershell
powershell -File TOOLS\image-stack.ps1 generate -Prompt "a red apple on a white table" -Out out\live -Json
python TOOLS\model3d.py check --variant all
python TOOLS\model3d.py generate --image out\live\<the image>.png --name apple --game-ready
```

The first line starts the backend if it is down. Look at the image and open the `.glb`:
a file arriving is not proof that it is good.

`TESTS\test_generate.py` and `TESTS\test_model3d.py` need none of that. They run the real
CLI against `TESTS\fixtures\backend_stub.py`, a small server that answers SwarmUI's and
ComfyUI's APIs and records every request, and they start and stop that server through a
fake SwarmUI folder, with the state folder redirected (`COMFY_FORGE_STATE_DIR`) so the
machine's own backend record is never touched.

## The four-way merge graph (live, slow)

```powershell
& "<SwarmUI>\dlbackend\comfy\python_embeded\python.exe" TESTS\integration_merge_graph.py --url http://127.0.0.1:7821
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
& "<SwarmUI>\dlbackend\comfy\python_embeded\python.exe" TOOLS\check_deps.py --pack "<SwarmUI>\dlbackend\comfy\ComfyUI\custom_nodes\hades-anima-suite"
```

Reads `requirements.txt` and `pyproject.toml`, keeps version constraints, and
reports requirements it cannot replay instead of dropping them.
