# PLAN - 0.11.0: every generative lane an agent needs, start to finish

Goal: an agent that can only write text and code can get an image, a song or a
game-ready 3D model out of comfy-forge with no human step, and Asset Forge can
take each result. Fix what is broken on the way.

Evidence format: `cmd:<command> @ <sha> exit=<n>`.

| ID | Task | Status | Depends on | Verify | Evidence |
|---|---|---|---|---|---|
| T01 | `generate` picks a model SwarmUI actually has (the default named a file that is not in the model list, so a default render was refused) | complete | - | `cmd:python TESTS/test_generate.py` | cmd:python TESTS/test_generate.py @ 2b6a85c exit=0; live default render picked woomixAnima_v180 |
| T02 | `comfy-forge --help` / `-h` work (the registered command failed on `--help`) | complete | - | `cmd:powershell -File TESTS/Test-CliSurface.ps1` | cmd:powershell -File TESTS/Test-CliSurface.ps1 @ 2b6a85c exit=0; `comfy-forge --help` exit 0 through the registered command |
| T03 | `start` / `stop`: headless SwarmUI (or standalone ComfyUI), ready-wait, stop only what was started; `generate` starts it when it is down (`-NoStart` opts out) | complete | - | `cmd:python TESTS/test_generate.py` | cmd:python TESTS/test_generate.py @ 2b6a85c exit=0; live: SwarmUI auto-started (web 3 s, backend ~45 s) |
| T04 | `generate` options: `-Negative`, `-InitImage` + `-Creativity`, `-Mask`, `-Lora`, `-Transparent`, `-Tileable`, `-Upscale`, `-Set k=v`, `-Json` closing line, `.json` sidecar per image | complete | T01 | `cmd:python TESTS/test_generate.py` | cmd:python TESTS/test_generate.py @ 2b6a85c exit=0; live: img2img, transparent (alpha), LoRA, upscale 768->1152, Japanese prompt |
| T05 | `TOOLS/model3d.py` (+ `model3d.ps1`): `variants`, `check`, `build`, `generate` for pixal3d / pixal3d-multi / trellis2 / idol from the shipped templates; `--prompt` (text to image first); `--game-ready` (Asset Forge) | complete | T03 | `cmd:python TESTS/test_model3d.py` | cmd:python TESTS/test_model3d.py @ 2b6a85c exit=0; live: pixal3d 58 s, pixal3d-multi 64 s, idol 50 s, --prompt --game-ready 699,350->17,000 tris |
| T06 | `capabilities` verb: one JSON report of every lane, what is ready and the command that fixes what is not | complete | T03 T05 | `cmd:powershell -File TESTS/Test-CliSurface.ps1` | cmd:powershell -File TESTS/Test-CliSurface.ps1 @ 2b6a85c exit=0 |
| T07 | Cleanup: unreachable branch in `Install-MissingRequirements`; `help` names every tool | complete | - | `cmd:powershell -File TESTS/Test-CliSurface.ps1` | cmd:powershell -File TESTS/Test-CliSurface.ps1 @ 2b6a85c exit=0 |
| T08 | Tests wired into `preflight.ps1`, `ci.yml`, `release-verify.yml` | complete | T01-T07 | `cmd:powershell -File TOOLS/preflight.ps1` | cmd:powershell -File TOOLS/preflight.ps1 @ 2b6a85c exit=0 (27 passed, 0 failed, 0 unverified) |
| T09 | Live proof on this machine: default render, img2img, transparent, text to 3D to game-ready, multi-view, music regression, Asset Forge `comfy-generate` | complete | T01-T06 | `file:STATE.md` | file:STATE.md @ 2b6a85c; also integration_music PASS, integration_live PASS, test_mcp_handshake --workflow PASS, Asset Forge comfy-generate SUCCEEDED |
| T10 | Docs, AGENTS.md, VERSION 0.11.0, CHANGELOG, STATE; field reports for Asset Forge and the bundle | complete | T09 | `cmd:powershell -File TESTS/Test-Release.ps1` | cmd:powershell -File TESTS/Test-Release.ps1 @ 2b6a85c exit=0 |
| T11 | PR, CI green on the SHA, merge, tag `v0.11.0`, `release-verify` green, alerts queried | complete | T10 | `cmd:gh run list` | cmd:gh run view 44201933913 (ci) @ e87acba exit=0; cmd:gh run view 44202742261 (release-verify) @ e87acba exit=0; archive 95/95 blobs identical; alerts dependabot 0, secret 0, code scanning 404 |

## Decisions

- Images stay on SwarmUI's API. SwarmUI builds the right graph for Anima, FLUX,
  SDXL and Z-Image from one parameter map; rebuilding 42 presets as ComfyUI graphs
  would be a second implementation to keep in step. The gap was reaching it without
  a human, so the fix is `start`, not a new renderer.
- 3D runs the shipped template itself, converted to API format at run time. The
  generation lock (`presets/3d/generation.json`) keeps guarding the widgets; no
  second copy of the graph exists to drift.
- Switch nodes with a constant input are folded before submission, so the branch
  not taken (stock TRELLIS.2, whose weights are optional) is never validated.
- `generate` starts the backend when it is down and leaves it running; `stop`
  ends only a backend this tool started. `-NoStart` keeps the old fail-fast.
- Asset Forge is not edited from here. Its side (a `comfy-3d` recipe, the new
  flags) goes to its `FIELD-REPORTS.md`; the `comfy-3d` command name goes to the bundle's.
- Game-ready stays Asset Forge's (`mesh-gameready`); `--game-ready` only calls it.

- `-Upscale` sends `refinermethod=PostApply`, `refinercontrolpercentage=0.4` and `refinerupscalemethod=pixel-lanczos` unless the preset or `-Set` gives them: SwarmUI's refiner stage (where the resize happens) needs all three, and the factor alone rendered at the base size.
- `-Tileable` is refused up front for transformer-model families: SwarmUI's seamless mode only patches UNet convolutions and the backend failed with an unrelated pickling error.
- `stop` refuses while the backend has jobs in flight (`-Force` overrides): Asset Forge and other agents use a running backend as they find it.
- `model3d.py` repairs zero-length normals/tangents in the raw `.glb` (byte-exact elsewhere): every Pixal3D mesh measured had 3-14, which is invalid glTF.

## Assumptions

- SwarmUI 0.9.8.4 with its bundled ComfyUI 0.45.0 is the reference backend.
- Idol/Pixal3D/ACE-Step weights already on disk are used; nothing is downloaded.

## Risks

- A headless SwarmUI started by `generate` keeps VRAM until `stop`. Mitigation:
  every start prints the stop command; `capabilities` shows who owns the backend.
- ComfyUI changes a 3D node's inputs. Mitigation: the converter refuses a widget
  count it cannot map, and `check` compares the graph with `/object_info`.
