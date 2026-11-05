# Generator presets

`presets.json` contains 39 operator-provided entries with titles, descriptions
and `param_map` settings. It is preset data, not a ComfyUI node-graph workflow.
Do not submit it to a workflow API or assume it installs models/custom nodes.

Keep a backup before importing into a matching frontend. Resolve the selected
model, VAE, sampler and scheduler against the installed backend; preserve local
choices when names differ. Hardware/model-specific descriptions are tuning
intent, not controlled quality or speed benchmarks. JSON parsing was checked;
GPU generation and frontend import are not certified by the bundle gates.

## The Anima stack (auto-install)

`anima-manifest.json` pins the model files for a maximum-quality Anima setup
and `TOOLS/Ensure-AnimaSwarmUI.ps1` installs them into a detected SwarmUI or
ComfyUI install. `TOOLS/Ensure-Tools.ps1` runs the installer on every pass, so
an install is set up automatically when a frontend is present.

See `ANIMA-HANDOFF.md` in this folder for how it works, the exact commands,
and the facts the operator needs (including why RES4LYF sampler names cannot
be used from SwarmUI).
