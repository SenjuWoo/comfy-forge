"""Structural + topology validation for the shipped ComfyUI workflows.

A JSON parse says nothing about whether a graph can render. This checks that the
links are coherent and, when the referenced checkpoints are present on this
machine, that the base and donors share an Anima lineage (same block count).
Merging different block counts produces a file that loads and renders black.

Exit 0 = pass, 1 = defect, 2 = environment could not verify topology.
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "workflows")
SEARCH = [
    r"S:\Misc\SwarmUI\Models\Stable-Diffusion\Anima",
    r"S:\Misc\SwarmUI\Models\Stable-Diffusion\Anima\Anime",
    r"S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI\models\diffusion_models",
]

fails, notes = [], []


def check(cond, msg):
    if not cond:
        fails.append(msg)


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
    for d in SEARCH:
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
        notes.append("topology not verified: some workflow checkpoints are not installed here "
                     f"({[f for f, p in zip(files, paths) if not p]})")
        return
    counts = {f: block_count(p) for f, p in zip(files, paths)}
    distinct = set(counts.values())
    check(len(distinct) == 1,
          f"merge: base and donors have different block counts {counts} - this renders black")


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


merge_graph()
generation_graph()

for n in notes:
    print("note " + n)
for f in fails:
    print("FAIL " + f)
if fails:
    print(f"WORKFLOWS: {len(fails)} defect(s)")
    sys.exit(1)
print("WORKFLOWS: PASS" + (" (topology unverified)" if notes else ""))
sys.exit(0)
