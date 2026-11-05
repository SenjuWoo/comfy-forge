# UABS Image Stack — Anima starter pack

A small, working image-generation stack for people who have never set one up.
Anima-focused for now: it installs the Anima model files, gives you 39 ready
presets, renders through SwarmUI, merges checkpoints into real files, and wires
an agent to ComfyUI over MCP.

Everything here is plain files. No build step, no Python environment to create,
no service to host.

| You want to… | Command |
|---|---|
| See what is installed and running | `TOOLS\image-stack.ps1 status` |
| Install the Anima models (~7.3 GB) | `TOOLS\image-stack.ps1 install` |
| List the presets | `TOOLS\image-stack.ps1 presets` |
| Make an image | `TOOLS\image-stack.ps1 generate -Prompt "…"` |
| Merge checkpoints | `TOOLS\image-stack.ps1 merge -Donors a,b,c,d -Weights 0.85,0.75,0.6,0.5` |
| Connect an agent to ComfyUI | `TOOLS\image-stack.ps1 mcp` |

## Requirements

- **Windows** with PowerShell 5.1 (built in).
- **SwarmUI** — https://github.com/mcmonkeyprojects/SwarmUI. Run
  `launch-windows.bat` once so it installs its own ComfyUI backend.
- **A GPU** with ~10 GB free VRAM for 1024×1024 Anima. It runs on less; it runs
  slowly.
- Internet for the first install only.

The installer finds SwarmUI by itself (`SWARMUI_ROOT`, then the path the last
run recorded, then a bounded scan). Pass `-SwarmUIRoot` if you keep it somewhere
unusual.

## 1. Install the models

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 install
```

Downloads only what is missing, verifies every file by SHA-256, and copies the
presets next to SwarmUI so the UI can import them. Re-running is safe. Add
`-IncludeOptional` for the 3 GB int8 checkpoint for small GPUs.

Already have a file under a different name? It is matched by hash, not filename,
so it is not downloaded again.

## 2. Make an image

Start SwarmUI (`launch-windows.bat`), then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 generate `
  -Preset "Anima - Author Daily (Euler + SGM)" `
  -Prompt "a weathered stone lighthouse on a basalt cliff at dawn, sea spray, volumetric light"
```

The PNG lands in `out\`. Add `-Steps 36 -Cfg 4.0` to override the preset, or
`-Preset "Anima - Official Best (ER-SDE + SGM)"` for the slow, high-quality path.
`-Seed 42` makes a render reproducible; the default `-1` is random.

In the SwarmUI UI, import the same presets: **Presets tab → Import →
`Data\presets-uabs-anima.json`**.

## 3. Merge checkpoints

A merge reads four fine-tunes of the same ancestor and writes one new
`.safetensors` you can load like any other checkpoint.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 merge `
  -Donors "D:\models\Versetility - Diving-Anima_v9.0.safetensors",
          "D:\models\Details - Terra Rising Unity 3.0 - Turbo.safetensors",
          "D:\models\Composition - hentai_studio_quality-000008.safetensors",
          "D:\models\Consistensy - One obsession_animaV5turbo.safetensors" `
  -Weights 0.85,0.75,0.60,0.50
```

It writes `Anima\Anima-4way-<timestamp>.safetensors` into SwarmUI's model folder
and refreshes SwarmUI's model list, so the merge appears in the UI immediately.
Full explanation and the parameter reference: **[docs/MERGE.md](docs/MERGE.md)**.

`workflows/anima-4way-merge.json` is the same merge as a ComfyUI graph, for
people who prefer the node editor. It needs the
[HaDeS Anima Suite](https://github.com/Devildonia/HaDeS-Anima-Suite) custom node
pack; the CLI above does not.

## 4. Wire an agent (MCP)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 mcp
```

Prints a client config for **comfyui-mcp** (pinned to `0.52.206`) pointed at the
ComfyUI instance that is actually listening — a standalone ComfyUI on 8188 or
SwarmUI's own backend on 7820+ — in `compact` tool mode, which registers three
meta-tools instead of ~200 schemas. `-Write -Agent claude` writes it into
`~/.claude.json`; for Hermes and Codex the block is printed to paste into
`config.yaml` / `config.toml`.

## What is verified, and what is not

Verified by running it (RTX 4080 SUPER, SwarmUI v0.9.8.4, Windows 11):

- The installer downloaded 5.84 GB, verified SHA-256, and installed it.
- A preset render through SwarmUI's API produced a real 1024×1024 PNG (27 s at
  28 steps), and the same preset renders the same way from the CLI.
- `merge_anima.py` merged the base with itself to a **bit-identical** file that
  renders correctly — the writer, the header and the streaming order are right.
- The merge formula matches `HaDeSAnimaMultiModelMerger`; 18 assertions cover
  weights, scale, `llm_adapter` protection, mixed dtypes, both key namespaces and
  the refusal cases.
- The topology gate rejected a 28-block donor against the 40-block base, which is
  the case that otherwise writes a file rendering pure black.
- `comfyui-mcp@0.52.206` started over stdio, completed the MCP handshake and
  exposed `list_tools`, `describe_tool`, `call_tool`.

Not verified here: a four-donor merge with real fine-tunes. Those four
checkpoints were not on the build machine. The only second Anima checkpoint
available is a different lineage (28 blocks vs 40), which the merger correctly
refuses — so the blend path is covered by the self-merge and the unit tests, not
by your four donor files.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `SwarmUI is not running` | Run `launch-windows.bat` in the SwarmUI folder first. |
| New checkpoint missing from the UI | `image-stack.ps1 status` then restart SwarmUI; the list is cached at startup. |
| `Invalid value for parameter VAE` | The preset asks for `Automatic`, which SwarmUI rejects. The CLI strips it; in the UI pick a real VAE. |
| Merge says `nothing to merge` | The donor is not an Anima diffusion checkpoint, or every tensor was protected. Run with `--dry-run` for the breakdown. |
| Merge refuses a donor because of its block count | Different lineage. Merge only checkpoints fine-tuned from the same base. |
| A merged model renders pure black | Its weights were paired across different layer counts. Use the check above. |

## Layout

```
presets/     presets.json, anima-manifest.json, ANIMA-HANDOFF.md
workflows/   anima-4way-merge.json  (ComfyUI graph, needs HaDeS nodes)
TOOLS/       image-stack.ps1, merge_anima.py, Ensure-AnimaSwarmUI.ps1
TESTS/       Test-AnimaInstall.ps1, test_merge.py, Test-PsSyntax.ps1, test_mcp_handshake.py
docs/        MERGE.md
```

## Ownership and licences

The presets are operator-authored. The Anima weights are third-party and keep
their own licence — this repo only pins their bytes and hashes. `merge_anima.py`
and `image-stack.ps1` are MIT (see `LICENSE`). The ComfyUI MCP server is a
separate MIT project used as a dependency, not vendored here.
