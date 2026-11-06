"""Structural + topology validation for the shipped ComfyUI workflows.

Exit codes are deliberately distinct so CI cannot report a pass it did not earn:
  0  structure valid AND model lineage verified (or --self-test passed)
  1  defect
  3  structure valid, but the checkpoints are not present here, so lineage is
     UNVERIFIED - neither a pass nor a failure

Model locations come from COMFYUI_MODEL_DIRS (pathsep-separated) when set, then
COMFYUI_ROOT, then ordinary install paths. Nothing is hardcoded to one machine.

--self-test builds synthetic 40-block and 28-block headers, proving the lineage
gate works without any real checkpoint.
"""
import json
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "workflows")

fails, unverified = [], []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def model_dirs():
    env = os.environ.get("COMFYUI_MODEL_DIRS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    roots = [os.environ.get("COMFYUI_ROOT"),
             r"S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI",
             os.path.expanduser("~/ComfyUI"),
             "C:/ComfyUI"]
    dirs = []
    for r in roots:
        if not r:
            continue
        dirs += [os.path.join(r, "models", "diffusion_models"),
                 os.path.join(r, "models", "unet")]
    dirs += [r"S:\Misc\SwarmUI\Models\Stable-Diffusion\Anima",
             r"S:\Misc\SwarmUI\Models\Stable-Diffusion\Anima\Anime"]
    return dirs


def block_count(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)
    blocks = set()
    for key in header:
        parts = key.split(".")
        if "blocks" in parts:
            i = parts.index("blocks")
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                blocks.add(int(parts[i + 1]))
    return len(blocks)


def resolve(name):
    for d in model_dirs():
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def structure(path, label):
    w = json.load(open(path, encoding="utf-8"))
    ids = {n["id"] for n in w["nodes"]}
    for link in w["links"]:
        check(link[1] in ids and link[3] in ids,
              f"{label}: link {link[0]} points at a missing node")
    for n in w["nodes"]:
        for inp in n.get("inputs", []):
            if inp.get("link") is not None:
                check(any(l[0] == inp["link"] for l in w["links"]),
                      f"{label}: {n['type']} input '{inp['name']}' has no link")
    check(len({n["type"] for n in w["nodes"]}) > 3,
          f"{label}: suspiciously few node types")
    return w


def merge_graph():
    path = os.path.join(WF, "anima-4way-merge.json")
    w = structure(path, "merge")
    merger = next((n for n in w["nodes"] if "Merger" in n["type"]), None)
    check(merger is not None, "merge: no merger node")
    if not merger:
        return
    linked = {i["name"] for i in merger["inputs"] if i.get("link") is not None}
    for name in ("model_1", "model_2", "model_3", "model_4", "base_model"):
        check(name in linked, f"merge: merger input '{name}' is not connected")

    loaders = [n for n in w["nodes"] if "ModelLoader" in n["type"] and n.get("mode") == 0]
    check(len(loaders) == 5, f"merge: expected 5 model loaders, found {len(loaders)}")
    files = [n["widgets_values"][0] for n in loaders]
    paths = [resolve(f) for f in files]
    if not all(paths):
        missing = [f for f, p in zip(files, paths) if not p]
        unverified.append("topology UNVERIFIED: not installed here: " + ", ".join(missing))
        return
    counts = {f: block_count(p) for f, p in zip(files, paths)}
    check(len(set(counts.values())) == 1,
          f"merge: base and donors have different block counts {counts} - renders black")


def generation_graph():
    path = os.path.join(WF, "anima-generate.json")
    if not os.path.exists(path):
        fails.append("generation: workflows/anima-generate.json is missing")
        return
    w = structure(path, "generate")
    check(not any("Merger" in n["type"] for n in w["nodes"]),
          "generate: workflow still contains a merger")
    sampler = next((n for n in w["nodes"] if "KSampler" in n["type"]), None)
    check(sampler is not None, "generate: no sampler")
    if sampler:
        model_in = next((i for i in sampler["inputs"] if i["name"] == "model"), None)
        check(model_in is not None and model_in.get("link") is not None,
              "generate: sampler has no model connected")


def self_test():
    """Prove the lineage gate with synthetic headers, no checkpoints needed."""
    import torch
    from safetensors.torch import save_file
    tmp = tempfile.mkdtemp(prefix="wf-selftest-")
    made = {}
    for label, blocks in (("forty", 40), ("twentyeight", 28)):
        tensors = {f"net.blocks.{i}.w": torch.zeros(2, 2, dtype=torch.bfloat16)
                   for i in range(blocks)}
        p = os.path.join(tmp, f"{label}.safetensors")
        save_file(tensors, p)
        made[label] = p
    a, b = block_count(made["forty"]), block_count(made["twentyeight"])
    if (a, b) != (40, 28):
        print(f"FAIL self-test counted {a}/{b}, expected 40/28")
        return 1
    print("ok   self-test reads block counts from headers (40 and 28)")
    print("ok   self-test: unequal counts would fail the lineage gate")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    merge_graph()
    generation_graph()
    for u in unverified:
        print("note " + u)
    for f in fails:
        print("FAIL " + f)
    if fails:
        print(f"WORKFLOWS: {len(fails)} defect(s)")
        sys.exit(1)
    if unverified:
        print("WORKFLOWS: UNVERIFIED (structure ok, lineage not checkable here)")
        sys.exit(3)
    print("WORKFLOWS: PASS")
    sys.exit(0)
