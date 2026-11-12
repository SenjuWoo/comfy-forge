"""The 3D templates stay on the widgets that produced the measured meshes.

presets/3d/generation.json is the lock. A drive-by edit of the workflow, or of
the lock, fails here. Game-ready size is not a widget in these files.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(REPO, "presets", "3d", "generation.json")
PIXAL = (
    "3d_pixal3d_trellis2_image_to_model.json",
    "3d_pixal3d_multi_views.json",
)
IDOL = "3d_idol_image_to_gaussian_splat.json"

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name)
        fail += 1


def widgets(doc, type_name):
    return [node.get("widgets_values") for node in doc["nodes"] if node.get("type") == type_name]


def main():
    lock = json.loads(open(LOCK, encoding="utf-8").read())
    check(lock.get("schema") == 1, "generation lock schema")
    ready = lock.get("game_ready") or {}
    check(ready.get("recipe") == "mesh-gameready", "game-ready owner is Asset Forge")
    check(ready.get("default_budget") == "game", "default budget is game")
    check(ready.get("triangle_cap") == 20000, "default triangle cap is 20000")
    for name in PIXAL:
        doc = json.loads(open(os.path.join(REPO, "presets", "3d", name), encoding="utf-8").read())
        got = sorted(tuple(item) for item in widgets(doc, "KSampler"))
        want = sorted(tuple(item) for item in lock["pixal3d"]["ksampler"])
        check(got == want, f"{name} samplers")
        check(widgets(doc, "DecimateMesh") == [lock["pixal3d"]["decimate"]], f"{name} decimate cap")
        check(widgets(doc, "ModelSamplingSD3") == [lock["pixal3d"]["flow_shift"]], f"{name} flow shift")
        check(sorted(widgets(doc, "RescaleCFG")) == sorted(lock["pixal3d"]["rescale_cfg"]), f"{name} rescale cfg")
        check(sorted(widgets(doc, "CFGOverride")) == sorted(lock["pixal3d"]["cfg_override"]), f"{name} cfg override")
        titled = [
            node.get("widgets_values")
            for node in doc["nodes"]
            if node.get("type") == "PrimitiveInt" and node.get("title") == "Texture Resolution"
        ]
        check(titled == [lock["pixal3d"]["texture_resolution"]], f"{name} texture resolution")
        remesh = widgets(doc, "RemeshMesh")
        check(bool(remesh) and remesh[0][0] == lock["pixal3d"]["remesh_resolution"], f"{name} remesh resolution")
    idol = json.loads(open(os.path.join(REPO, "presets", "3d", IDOL), encoding="utf-8").read())
    check(widgets(idol, "SplatToMesh") == [lock["idol"]["splat_to_mesh"]], "idol splat to mesh")
    if fail:
        print(f"3D GENERATION: {fail} FAILED")
        return 1
    print("3D GENERATION: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
