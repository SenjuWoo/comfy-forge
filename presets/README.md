# Generator presets

`presets.json` contains 42 operator-provided entries with titles, descriptions
and `param_map` settings. It is preset data, not a ComfyUI node-graph workflow.
Do not submit it to a workflow API or assume it installs models/custom nodes.

Keep a backup before importing into a matching frontend. Resolve the selected
model, VAE, sampler and scheduler against the installed backend; preserve local
choices when names differ. Hardware/model-specific descriptions are tuning
intent, not controlled quality or speed benchmarks. JSON parsing is checked by
`TESTS/Test-AnimaInstall.ps1`; GPU generation and frontend import are not.

Three Anima presets use RES4LYF names (`beta57`, `res_2s`, `bong_tangent`). They
work in SwarmUI once the RES4LYF node pack is installed (`image-stack.ps1 install`
does that) and SwarmUI has been restarted.

## The Anima stack

`anima-manifest.json` pins the model files for a maximum-quality Anima setup
and `TOOLS/Ensure-AnimaSwarmUI.ps1` installs them into a detected SwarmUI or
ComfyUI install; `TOOLS/image-stack.ps1 install` runs it for you.

See `ANIMA-HANDOFF.md` in this folder for how it works, the exact commands and
the facts the operator needs.
