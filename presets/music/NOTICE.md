# provenance for the music workflows in this folder

These six files are the stock ACE-Step 1.5 workflow templates from the Comfy-Org
workflow_templates project. They are upstream files, not original work, committed here so
the setup survives a backend update:

- upstream: <https://github.com/Comfy-Org/workflow_templates> (MIT)
- `audio_ace_step_1_5_checkpoint.json` — turbo, all-in-one checkpoint
- `audio_ace_step_1_5_split.json` — turbo, split files (0.6B + 1.7B text encoders)
- `audio_ace_step_1_5_split_4b.json` — turbo, split files (0.6B + 4B text encoders)
- `audio_ace_step1_5_xl_turbo.json`, `audio_ace_step1_5_xl_sft.json`, `audio_ace_step1_5_xl_base.json` — XL models

The first three were copied from the `comfyui_workflow_templates_json` package of the ComfyUI
install on this machine (templates 0.11.103); the XL three were downloaded from the upstream
repository's `templates/` folder because that package version does not carry them yet.

License: MIT (© Comfy-Org) for the workflow files. The model weights are separate downloads
and are NOT in this repository. They come from the Hugging Face repo
`Comfy-Org/ace_step_1.5_ComfyUI_files`, whose model card lists `apache-2.0`.
`TOOLS/Install-MusicModels.ps1` fetches them; see [../../docs/MUSIC.md](../../docs/MUSIC.md).

`TOOLS/music.py` builds the same graph in API format and `TESTS/test_music.py` fails if its
wiring, sampler values, loader files or installer URLs drift from these files.
