"""Convert a ComfyUI UI workflow into the API prompt format.

The live merge test has to execute the file a beginner actually opens, not a
graph rebuilt in Python that quietly drifts from it (different weights, different
saver mode). This converts `workflows/anima-4way-merge.json` itself.

Widget order follows the node definitions, which `/object_info` does not expose,
so the tables below are explicit and every node is checked for an exact value
count before conversion. A mismatch is a hard failure, never a silent guess.

    python TESTS/workflow_to_api.py workflows/anima-4way-merge.json
"""
import json
import sys

# Ordered widget names per node class, as the UI stores them in widgets_values.
WIDGETS = {
    "HaDeSAnimaModelLoader": ["unet_name", "weight_dtype", "flow_shift",
                              "auto_remap_diffusers", "external_llm_adapter"],
    "HaDeSAnimaCLIPLoader": ["clip_name", "device_mode", "precision", "strict_qwen3_guard"],
    "HaDeSAnimaVAELoader": ["vae_name", "vae_precision"],
    "HaDeSAnimaPromptStudio": ["model_variant", "style_mode", "booru_sanitizer",
                               "t5_weight_multiplier", "character_tag", "artist_tag",
                               "header_mode", "header_trigger", "artwork_title",
                               "positive_prompt", "negative_prompt"],
    "HaDeSAnimaLatentPipelineGuard": ["resolution_preset", "custom_width", "custom_height",
                                      "batch_size", "prevent_29b_white_bg_bias"],
    "HaDeSAnimaMultiModelMerger": ["method", "density", "scale", "seed",
                                   "weight_1", "weight_2", "weight_3", "weight_4",
                                   "protect_llm_adapter"],
    "HaDeSAnimaKSampler": ["variant_profile", "seed", "steps", "cfg", "sampler_name",
                           "scheduler", "denoise", "flow_shift"],
    "HaDeSAnimaVAEDecode": ["decode_mode", "tile_size_px", "tile_overlap_px",
                            "auto_tile_mp_threshold"],
    "HaDeSAnimaModelSaver": ["filename_prefix", "save_format", "precision",
                             "llm_adapter_mode", "include_metadata", "custom_author",
                             "custom_model_title"],
    "SaveImage": ["filename_prefix"],
    "PreviewImage": [],
    "Note": [],
}

# How many leading entries are required. A short list is a silently broken node,
# so it aborts; trailing optional widgets may legitimately be absent.
REQUIRED = {
    "HaDeSAnimaModelLoader": 5,
    "HaDeSAnimaCLIPLoader": 4,
    "HaDeSAnimaVAELoader": 2,
    "HaDeSAnimaPromptStudio": 8,
    "HaDeSAnimaLatentPipelineGuard": 5,
    "HaDeSAnimaMultiModelMerger": 7,   # method..weight_2
    "HaDeSAnimaKSampler": 8,
    "HaDeSAnimaVAEDecode": 4,
    "HaDeSAnimaModelSaver": 5,         # custom_author/title are optional
    "SaveImage": 1,
    "PreviewImage": 0,
    "Note": 0,
}

# The UI inserts this pseudo-widget after every seed/int-with-control field.
CONTROL = {"fixed", "randomize", "increment", "decrement"}


def convert(workflow):
    """Return {node_id: {class_type, inputs}} plus the ids skipped as bypassed."""
    nodes = {str(n["id"]): n for n in workflow.get("nodes", [])}
    links = {l[0]: l for l in workflow.get("links", [])}
    prompt = {}
    skipped = []

    for nid, node in nodes.items():
        cls = node.get("type")
        if cls not in WIDGETS:
            raise SystemExit(f"node {nid} has unknown class {cls!r}; add it to WIDGETS")
        if node.get("mode") in (2, 4):      # muted or bypassed
            skipped.append((nid, cls, node.get("mode")))
            continue

        names = WIDGETS[cls]
        values = node.get("widgets_values") or []
        if not names:
            # Note/PreviewImage carry UI text only; they are not API nodes.
            widgets = {}
        elif isinstance(values, dict):        # some packs store widgets by name
            widgets = {k: values[k] for k in names if k in values}
        else:
            widgets = {}
            vi = 0
            for name in names:
                # Consume an optional control value sitting after a seed field.
                if vi < len(values) and isinstance(values[vi], str) and values[vi] in CONTROL \
                        and name in ("steps", "density", "scale", "weight_1", "cfg"):
                    vi += 1
                if vi >= len(values):
                    break
                widgets[name] = values[vi]
                vi += 1
            need = REQUIRED[cls]
            if vi < need or vi != len(values):
                raise SystemExit(
                    f"node {nid} ({cls}) has {len(values)} widget values, the table "
                    f"needs at least {need} and maps {vi}; refusing to guess the mapping")

        inputs = dict(widgets)
        for slot, entry in enumerate(node.get("inputs") or []):
            lid = entry.get("link")
            if lid is None:
                continue
            link = links.get(lid)
            if not link:
                raise SystemExit(f"node {nid} references missing link {lid}")
            inputs[entry["name"]] = [str(link[1]), link[2]]

        prompt[nid] = {"class_type": cls, "inputs": inputs}

    return prompt, skipped


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    wf = json.load(open(sys.argv[1], encoding="utf-8"))
    prompt, skipped = convert(wf)
    print(json.dumps(prompt, indent=2))
    if skipped:
        print("skipped (bypassed/muted):", skipped, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
