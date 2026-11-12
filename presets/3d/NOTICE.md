# provenance for the 3D workflows in this folder

These three files are the stock workflow templates from the Comfy-Org workflow_templates
project, saved from ComfyUI's template library on this machine and committed here so the
setup survives a backend update. They are upstream files, not original work:

- upstream: https://github.com/Comfy-Org/workflow_templates (MIT)
- `3d_idol_image_to_gaussian_splat.json` — <https://github.com/Comfy-Org/workflow_templates/blob/main/templates/3d_idol_image_to_gaussian_splat.json>
- `3d_pixal3d_multi_views.json` — <https://github.com/Comfy-Org/workflow_templates/blob/main/templates/3d_pixal3d_multi_views.json>
- `3d_pixal3d_trellis2_image_to_model.json` — <https://github.com/Comfy-Org/workflow_templates/blob/main/templates/3d_pixal3d_trellis2_image_to_model.json>

License: MIT (© Comfy-Org). The model weights these workflows load are separate downloads
and are NOT in this repository — see [../../docs/3D-MODELS.md](../../docs/3D-MODELS.md) for
where they come from and for the license on each.

The copies here contain the workflow only. Running them requires the models listed in each
workflow's notes and a ComfyUI build with TRELLIS.2 support on this machine.
