"""Image (or text) to a 3D model through a ComfyUI backend.

    python TOOLS/model3d.py variants
    python TOOLS/model3d.py check    [--variant pixal3d|all] [--url URL]
    python TOOLS/model3d.py generate --image sword.png --name sword --game-ready
    python TOOLS/model3d.py generate --prompt "a viking axe with a wolf-head pommel" --game-ready
    python TOOLS/model3d.py generate --variant pixal3d-multi --views front.png,left.png,back.png,right.png
    python TOOLS/model3d.py build    --image sword.png --out graph.json

The graph that runs is the shipped template under presets/3d/ itself, converted to
ComfyUI's API format here. Nothing is rebuilt by hand, so the generation lock
(presets/3d/generation.json, TESTS/test_3d_generation.py) guards what executes.

The mesh a template writes is a sculpt of about 700,000 triangles. --game-ready
hands it to Asset Forge's mesh-gameready recipe (TOOLS/game-ready.ps1), which
writes a mesh at or under the budget; this tool never decimates.

The backend is found on its own (--url, COMFYUI_URL, port 8188, 7820-7832) and is
started headless when none answers (--no-start refuses instead).

Nothing here downloads a model (TOOLS/Install-3DModels.ps1 does, on request).
Exit codes: 0 done, 1 failed, 2 bad arguments, 3 no backend reachable.
"""
import argparse
import copy
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from music import (BackendError, UsageError, ensure_backend, http_bytes, http_json, image_stack,  # noqa: E402
                   no_backend_message, powershell, resolve_backend, slug, tool_version, unique_path, visible_options,
                   wait_for, warn)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO, "presets", "3d")

# One row per way of running a shipped template.
#   input     "image": one picture.  "views": a turnaround sheet, or up to four pictures.
#   switches  PrimitiveBoolean title -> value, for templates that carry a branch.
VARIANTS = {
    "pixal3d": {
        "template": "3d_pixal3d_trellis2_image_to_model.json",
        "title": "Pixal3D, one image -> textured PBR mesh (.glb). Props and items.",
        "input": "image", "install_set": "pixal3d",
        "switches": {"Boolean (Switch to Trellis2)": False},
    },
    "pixal3d-multi": {
        "template": "3d_pixal3d_multi_views.json",
        "title": "Pixal3D multi-view, turnaround sheet or 1-4 views -> textured PBR mesh (.glb). Characters.",
        "input": "views", "install_set": "pixal3d",
    },
    "trellis2": {
        "template": "3d_pixal3d_trellis2_image_to_model.json",
        "title": "Stock TRELLIS.2, one image -> textured PBR mesh (.glb).",
        "input": "image", "install_set": "trellis2",
        "switches": {"Boolean (Switch to Trellis2)": True},
    },
    "idol": {
        "template": "3d_idol_image_to_gaussian_splat.json",
        "title": "Idol, one image -> Gaussian splat (.spz), an untextured .glb and an orbit video. Previews.",
        "input": "image", "install_set": "idol",
    },
}
DEFAULT_VARIANT = "pixal3d"
VIEW_NAMES = ("front", "left", "back", "right")

# Loader input -> the models subfolder its file lives in.
LOADERS = {
    ("UNETLoader", "unet_name"): "diffusion_models",
    ("VAELoader", "vae_name"): "vae",
    ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("LoadMoGeModel", "model_name"): "geometry_estimation",
    ("LoadBackgroundRemovalModel", "bg_removal_name"): "background_removal",
}
# Nodes that write the files this tool hands back. Preview nodes are not run.
SAVERS = ("Save3DAdvanced", "SaveGLB", "SaveVideo", "SaveImageAdvanced")
NOTES = ("Note", "MarkdownNote")
# Values the editor stores after a seed-like widget; they are not node inputs.
CONTROL = ("fixed", "randomize", "increment", "decrement")
# Editor-only widgets that the node itself does not take.
UI_ONLY = {("LoadImage", "upload"), ("SaveGLB", "image")}
# A widget that stores extra values after its own (a crop box and its four fields).
EXTRA_VALUES = {("ImageCropV2", "crop_region"): 4}
MODEL_EXT = (".glb", ".gltf", ".obj", ".fbx", ".stl", ".usdz")
SPLAT_EXT = (".spz", ".ply", ".splat", ".ksplat")


class ConvertError(Exception):
    """The template cannot be turned into an API graph without guessing."""


# ---------------------------------------------------------------------------
# Template -> API graph
# ---------------------------------------------------------------------------
def _direct(nodes, links):
    """Copies of the nodes, each input carrying its source as (node id, slot)."""
    table = {}
    for link in links:
        if isinstance(link, dict):
            table[link["id"]] = (link["origin_id"], link["origin_slot"])
        else:
            table[link[0]] = (link[1], link[2])
    out = {}
    for node in nodes:
        node = copy.deepcopy(node)
        for entry in node.get("inputs") or []:
            lid = entry.get("link")
            if lid is not None and lid not in table:
                raise ConvertError(f"node {node['id']} ({node.get('type')}) names link {lid}, which does not exist")
            entry["src"] = table.get(lid) if lid is not None else None
        out[node["id"]] = node
    return out


def _subgraph_outputs(sub):
    """Output slot -> (inner node id, slot)."""
    outs = {}
    for link in sub["links"]:
        if link["target_id"] == sub["outputNode"]["id"]:
            outs[link["target_slot"]] = (link["origin_id"], link["origin_slot"])
    return outs


def widget_values(node):
    """{widget input name: value} for one editor node, or raise when the list cannot be mapped."""
    names = [e["name"] for e in node.get("inputs") or [] if e.get("widget")]
    values = node.get("widgets_values")
    if values is None:
        values = []
    if isinstance(values, dict):
        return {k: values[k] for k in names if k in values}
    cls = node.get("type")
    out, vi = {}, 0
    for i, name in enumerate(names):
        if vi >= len(values):
            break  # trailing widgets the editor saved no value for keep the node's default
        out[name] = values[vi]
        vi += 1
        vi += EXTRA_VALUES.get((cls, name), 0)
        left_values, left_names = len(values) - vi, len(names) - i - 1
        if left_values > left_names and isinstance(values[vi], str) and values[vi] in CONTROL:
            vi += 1
    if vi != len(values):
        raise ConvertError(f"node {node['id']} ({cls}) has {len(values)} widget values, {vi} of them map to its "
                           f"{len(names)} widgets; refusing to guess the rest")
    return out


def _expand_subgraphs(nodes, definitions):
    """Replace every subgraph instance by the nodes inside it, until none is left."""
    subs = {s["id"]: s for s in definitions}
    while True:
        inst = next((n for n in nodes.values() if n.get("type") in subs), None)
        if inst is None:
            return nodes
        sub = subs[inst["type"]]
        iid = inst["id"]
        values = widget_values(inst)
        values.update(inst.get("override") or {})
        bound = {}
        by_name = {e["name"]: e for e in inst.get("inputs") or []}
        for slot, spec in enumerate(sub["inputs"]):
            entry = by_name.get(spec["name"])
            if entry is None:
                bound[slot] = None
            elif entry.get("src") is not None:
                bound[slot] = ("src", entry["src"])
            elif spec["name"] in values:
                bound[slot] = ("value", values[spec["name"]])
            else:
                bound[slot] = None
        inner = _direct(sub["nodes"], sub["links"])
        in_id = sub["inputNode"]["id"]

        def moved(src):
            if src[0] == in_id:
                hit = bound.get(src[1])
                return hit[1] if hit and hit[0] == "src" else None
            return (f"{iid}:{src[0]}", src[1])

        for node in inner.values():
            node["id"] = f"{iid}:{node['id']}"
            for entry in node.get("inputs") or []:
                src = entry.get("src")
                if src is None:
                    continue
                if src[0] == in_id:
                    hit = bound.get(src[1])
                    if hit and hit[0] == "value":
                        node.setdefault("override", {})[entry["name"]] = hit[1]
                entry["src"] = moved(src)
        outs = _subgraph_outputs(sub)
        del nodes[iid]
        for node in nodes.values():
            for entry in node.get("inputs") or []:
                src = entry.get("src")
                if src is not None and src[0] == iid:
                    if src[1] not in outs:
                        raise ConvertError(f"subgraph {sub.get('name')} has no output {src[1]}")
                    entry["src"] = moved(outs[src[1]])
        for node in inner.values():
            nodes[node["id"]] = node


def template_to_api(doc, switches=None):
    """Editor workflow (dict) -> API prompt {node id: {class_type, inputs}}."""
    nodes = _direct(doc.get("nodes") or [], doc.get("links") or [])
    nodes = _expand_subgraphs(nodes, (doc.get("definitions") or {}).get("subgraphs") or [])
    switches = dict(switches or {})
    graph = {}
    for nid, node in nodes.items():
        cls = node.get("type")
        if cls in NOTES or node.get("mode") == 2:
            continue
        if node.get("mode") == 4:
            if any(e.get("src") is not None and e["src"][0] == nid for n in nodes.values() for e in n.get("inputs") or []):
                raise ConvertError(f"node {nid} ({cls}) is bypassed but something reads its output")
            continue
        inputs = widget_values(node)
        inputs.update(node.get("override") or {})
        if cls == "PrimitiveBoolean" and node.get("title") in switches:
            inputs["value"] = bool(switches.pop(node["title"]))
        for entry in node.get("inputs") or []:
            if entry.get("src") is not None:
                inputs[entry["name"]] = [str(entry["src"][0]), entry["src"][1]]
        for key in [k for k in inputs if (cls, k) in UI_ONLY]:
            del inputs[key]
        graph[str(nid)] = {"class_type": cls, "inputs": inputs}
    if switches:
        raise ConvertError(f"the template has no switch named {', '.join(switches)}")
    fold_switches(graph)
    return prune(graph)


def _constant(graph, value):
    """A literal, or the value of the primitive a link points at; None when it is computed."""
    if not isinstance(value, list):
        return value
    node = graph.get(value[0])
    if node and node["class_type"] in ("PrimitiveBoolean", "PrimitiveInt", "PrimitiveFloat", "PrimitiveString"):
        inner = node["inputs"].get("value")
        return None if isinstance(inner, list) else inner
    return None


def fold_switches(graph):
    """Wire the consumers of a switch whose condition is a constant straight to the chosen branch.

    ComfyUI validates both branches of a switch. The branch not taken can name a
    model that is not installed (stock TRELLIS.2 beside Pixal3D), and the whole
    prompt would be rejected for a file it never loads.
    """
    while True:
        done = True
        for nid, node in list(graph.items()):
            if node["class_type"] != "ComfySwitchNode":
                continue
            flag = _constant(graph, node["inputs"].get("switch"))
            if not isinstance(flag, bool):
                continue
            chosen = node["inputs"].get("on_true" if flag else "on_false")
            if not isinstance(chosen, list):
                raise ConvertError(f"switch {nid} selects a branch that is not connected")
            for other in graph.values():
                for key, value in other["inputs"].items():
                    if isinstance(value, list) and value[0] == nid:
                        other["inputs"][key] = list(chosen)
            del graph[nid]
            done = False
        if done:
            return


def prune(graph):
    """Only the nodes a saver depends on. Previews and dead branches are not sent."""
    keep, stack = set(), [nid for nid, n in graph.items() if n["class_type"] in SAVERS]
    if not stack:
        raise ConvertError("the template has no node that saves a file")
    while stack:
        nid = stack.pop()
        if nid in keep:
            continue
        if nid not in graph:
            raise ConvertError(f"a link points at node {nid}, which is not in the graph")
        keep.add(nid)
        stack.extend(v[0] for v in graph[nid]["inputs"].values() if isinstance(v, list))
    return {nid: graph[nid] for nid in graph if nid in keep}


def load_template(variant):
    path = os.path.join(TEMPLATES, VARIANTS[variant]["template"])
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def required_files(graph):
    """[(models subfolder, file, node class, input)] the graph loads."""
    found = []
    for node in graph.values():
        for (cls, field), folder in LOADERS.items():
            if node["class_type"] == cls and isinstance(node["inputs"].get(field), str):
                item = (folder, node["inputs"][field], cls, field)
                if item not in found:
                    found.append(item)
    return sorted(found)


def upstream(graph, start, classes, via=("images", "image")):
    """Follow image links upstream from an input value to the first node of one of `classes`.

    Returns the node ids passed on the way, that node last; [] when the chain ends first."""
    value, path = start, []
    while isinstance(value, list) and value[0] not in path:
        path.append(value[0])
        node = graph[value[0]]
        if node["class_type"] in classes:
            return path
        value = next((node["inputs"][k] for k in via if isinstance(node["inputs"].get(k), list)), None)
    return []


def build_graph(variant, image=None, views=None, sheet_size=None, name="model", seed=None):
    """The API graph for one run. `image` and `views` are names the backend's LoadImage can open."""
    if variant not in VARIANTS:
        raise UsageError(f"unknown variant {variant!r}; choose one of {', '.join(VARIANTS)}")
    spec = VARIANTS[variant]
    graph = template_to_api(load_template(variant), spec.get("switches"))
    loaders = [nid for nid, n in graph.items() if n["class_type"] == "LoadImage"]
    if len(loaders) != 1:
        raise ConvertError(f"expected one LoadImage in {spec['template']}, found {len(loaders)}")
    if spec["input"] == "image":
        if views:
            raise UsageError(f"--views is for pixal3d-multi; {variant} takes one --image")
        if not image:
            raise UsageError("--image (or --prompt) is required")
        graph[loaders[0]]["inputs"]["image"] = image
    else:
        cond = next((nid for nid, n in graph.items() if n["class_type"] == "Pixal3DMultiViewConditioning"), None)
        if cond is None:
            raise ConvertError("the multi-view template has no Pixal3DMultiViewConditioning node")
        chains = {view: upstream(graph, graph[cond]["inputs"].get(view), ("ImageCropV2",)) for view in VIEW_NAMES}
        if not all(chains.values()):
            raise ConvertError("could not follow every view of the multi-view template back to its crop")
        crops = {view: chain[-1] for view, chain in chains.items()}
        if views:
            unknown = [v for v in views if v not in VIEW_NAMES]
            if unknown or "front" not in views:
                raise UsageError("--views gives front[,left,back,right] in that order; the front view is required")
            for view in VIEW_NAMES:
                crop = crops[view]
                if view in views:
                    new_id = f"view-{view}"
                    graph[new_id] = {"class_type": "LoadImage", "inputs": {"image": views[view]}}
                    for node in graph.values():
                        for key, value in node["inputs"].items():
                            if isinstance(value, list) and value[0] == crop:
                                node["inputs"][key] = [new_id, 0]
                else:
                    # The whole chain goes, its view saver included: left in, that
                    # saver would still pull the sheet through its crop.
                    del graph[cond]["inputs"][view]
                    for nid in chains[view]:
                        graph.pop(nid, None)
            graph = prune(graph)
        else:
            if not image or not sheet_size:
                raise UsageError("pixal3d-multi needs --image (a four-view turnaround sheet) or --views")
            width, height = sheet_size
            column = width // 4
            if column < 64:
                raise UsageError(f"the sheet is {width} px wide; four views side by side need at least 256 px")
            graph[loaders[0]]["inputs"]["image"] = image
            # Front, left, back, right, left to right. Each quarter is cropped to its
            # subject by the template's own background-removal step afterwards.
            for index, view in enumerate(VIEW_NAMES):
                graph[crops[view]]["inputs"]["crop_region"] = {
                    "x": index * column, "y": 0, "width": column, "height": height}
    stem = slug(name)
    for node in graph.values():
        cls, inputs = node["class_type"], node["inputs"]
        if cls in ("Save3DAdvanced", "SaveGLB"):
            inputs["filename_prefix"] = f"3d/{stem}"
        elif cls == "SaveVideo":
            inputs["filename_prefix"] = f"video/{stem}"
        elif cls == "SaveImageAdvanced":
            inputs["filename_prefix"] = f"3d/{stem}_{slug(str(inputs.get('filename_prefix') or 'image'))}"
        if seed is not None and isinstance(inputs.get("seed"), int):
            inputs["seed"] = seed
    return graph


# ---------------------------------------------------------------------------
# Input images
# ---------------------------------------------------------------------------
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def image_size(path):
    """(width, height) of a PNG, JPEG or WebP, read from its header."""
    with open(path, "rb") as fh:
        head = fh.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            kind = head[12:16]
            if kind == b"VP8X":
                return (int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1)
            if kind == b"VP8L":
                bits = int.from_bytes(head[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            if kind == b"VP8 ":
                return (struct.unpack("<H", head[26:28])[0] & 0x3FFF, struct.unpack("<H", head[28:30])[0] & 0x3FFF)
        if head[:2] == b"\xff\xd8":
            fh.seek(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    break
                if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                    continue
                size = struct.unpack(">H", fh.read(2))[0]
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", fh.read(5)[1:5])
                    return (width, height)
                fh.seek(size - 2, 1)
    raise UsageError(f"{path} is not a PNG, JPEG or WebP image this tool can read")


def check_image(path, what):
    if not os.path.isfile(path):
        raise UsageError(f"{what} not found: {path}")
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
        raise UsageError(f"{what} must be a .png, .jpg or .webp file: {path}")
    return image_size(path)


def upload_image(base, path):
    """Put a local image where the backend's LoadImage can open it; returns that name."""
    with open(path, "rb") as fh:
        data = fh.read()
    ext = os.path.splitext(path)[1].lower()
    name = f"comfy-forge-{hashlib.sha1(data).hexdigest()[:16]}{ext}"
    boundary = uuid.uuid4().hex
    mime = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(base + "/upload/image", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            reply = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise BackendError(f"the backend refused the image upload: {e.read()[:300]!r}")
    sub = reply.get("subfolder") or ""
    return f"{sub}/{reply['name']}" if sub else reply["name"]


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
def preflight(base, graph, variant):
    """Problems that would make the backend reject this graph."""
    info = http_json(base + "/object_info", timeout=120)
    problems = []
    for cls in sorted({n["class_type"] for n in graph.values()}):
        if cls not in info:
            problems.append(f"node {cls} is not registered; this ComfyUI is too old for the {variant} template")
    for nid, node in graph.items():
        known = info.get(node["class_type"], {}).get("input", {})
        names = set(known.get("required", {})) | set(known.get("optional", {}))
        if not names:
            continue
        for key in node["inputs"]:
            if key.split(".")[0] not in names:
                problems.append(f"node {nid} ({node['class_type']}) no longer takes '{key}'; "
                                "the template and this ComfyUI disagree")
    for folder, name, cls, field in required_files(graph):
        if cls in info and name not in visible_options(info, cls, field):
            problems.append(f"{name} is not in models/{folder}; run TOOLS\\Install-3DModels.ps1 "
                            f"-Set {VARIANTS[variant]['install_set']}")
    return problems


def collect_files(entry):
    """Every file the run saved (previews, which ComfyUI keeps as type 'temp', are left out)."""
    files = []
    for output in entry.get("outputs", {}).values():
        for items in output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename") and item.get("type", "output") == "output":
                    files.append(item)
    return files


def glb_summary(path):
    """Triangle count and size of a .glb, from its JSON chunk; {} when it cannot be read."""
    info = {"bytes": os.path.getsize(path)}
    try:
        with open(path, "rb") as fh:
            magic, _version, _length = struct.unpack("<4sII", fh.read(12))
            size, kind = struct.unpack("<I4s", fh.read(8))
            if magic != b"glTF" or kind != b"JSON":
                return info
            doc = json.loads(fh.read(size).decode("utf-8"))
        triangles = 0
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                if prim.get("mode", 4) != 4:
                    continue
                index = prim.get("indices")
                count = doc["accessors"][index]["count"] if index is not None else \
                    doc["accessors"][prim["attributes"]["POSITION"]]["count"]
                triangles += count // 3
        info.update(triangles=triangles, materials=len(doc.get("materials", [])), textures=len(doc.get("images", [])))
    except (OSError, ValueError, KeyError, IndexError, struct.error):
        pass
    return info


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    length = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if not length > 1e-12 or length != length or length == float("inf"):
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def repair_glb_vectors(path):
    """Give every vertex of a .glb a unit normal and a unit tangent; returns how many were repaired.

    The mesh nodes write a zero-length normal on a handful of vertices that sit on
    degenerate triangles. glTF requires unit normals, the Khronos validator calls
    each one an error, and a mesh that starts invalid is not a sound thing to hand
    to the game-ready step. A zero normal becomes the normal of a triangle that
    uses the vertex; a zero tangent becomes any unit vector across that normal.
    Only the bytes of those vectors change; geometry, UVs and textures do not.
    """
    import array
    with open(path, "rb") as fh:
        blob = bytearray(fh.read())
    if blob[:4] != b"glTF" or len(blob) < 28:
        return {"normals": 0, "tangents": 0}
    json_len, json_kind = struct.unpack_from("<I4s", blob, 12)
    if json_kind != b"JSON":
        return {"normals": 0, "tangents": 0}
    doc = json.loads(bytes(blob[20:20 + json_len]).decode("utf-8"))
    bin_start = 20 + json_len + 8
    if bin_start > len(blob) or blob[bin_start - 4:bin_start] != b"BIN\x00":
        return {"normals": 0, "tangents": 0}

    def floats(index, width):
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        if acc.get("componentType") != 5126 or view.get("buffer", 0) != 0 or view.get("byteStride") not in (None, width * 4) \
                or "sparse" in acc:
            return None, 0
        start = bin_start + view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        data = array.array("f")
        data.frombytes(bytes(blob[start:start + acc["count"] * width * 4]))
        if sys.byteorder != "little":
            data.byteswap()
        return data, start

    def integers(index):
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        code = {5125: "I", 5123: "H", 5121: "B"}.get(acc.get("componentType"))
        if code is None or view.get("buffer", 0) != 0 or view.get("byteStride"):
            return None
        start = bin_start + view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        data = array.array(code)
        data.frombytes(bytes(blob[start:start + acc["count"] * data.itemsize]))
        if sys.byteorder != "little":
            data.byteswap()
        return data

    def store(data, start):
        out = array.array("f", data)
        if sys.byteorder != "little":
            out.byteswap()
        blob[start:start + len(out) * 4] = out.tobytes()

    fixed = {"normals": 0, "tangents": 0}
    done = set()
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if prim.get("mode", 4) != 4 or "NORMAL" not in attrs or "POSITION" not in attrs:
                continue
            key = (attrs["NORMAL"], attrs.get("TANGENT"))
            if key in done:
                continue
            done.add(key)
            normals, n_start = floats(attrs["NORMAL"], 3)
            positions, _ = floats(attrs["POSITION"], 3)
            if normals is None or positions is None:
                continue
            index = integers(prim["indices"]) if prim.get("indices") is not None else None

            def face_normal(vertex):
                if index is None:
                    first = vertex - vertex % 3
                    corners = [(first, first + 1, first + 2)]
                else:
                    corners, at = [], -1
                    for _ in range(24):
                        try:
                            at = index.index(vertex, at + 1)
                        except ValueError:
                            break
                        tri = at - at % 3
                        if tri + 2 < len(index):
                            corners.append((index[tri], index[tri + 1], index[tri + 2]))
                for a, b, c in corners:
                    pa, pb, pc = (positions[3 * i:3 * i + 3] for i in (a, b, c))
                    unit = _unit(_cross([pb[k] - pa[k] for k in range(3)], [pc[k] - pa[k] for k in range(3)]))
                    if unit:
                        return unit
                return (0.0, 0.0, 1.0)

            changed = 0
            for v in range(len(normals) // 3):
                x, y, z = normals[3 * v], normals[3 * v + 1], normals[3 * v + 2]
                square = x * x + y * y + z * z
                if 0.999 < square < 1.001:
                    continue
                unit = _unit((x, y, z)) if square > 1e-10 and square == square and square != float("inf") else None
                normals[3 * v:3 * v + 3] = array.array("f", unit or face_normal(v))
                changed += 1
            if changed:
                store(normals, n_start)
                fixed["normals"] += changed
            if "TANGENT" not in attrs:
                continue
            tangents, t_start = floats(attrs["TANGENT"], 4)
            if tangents is None:
                continue
            changed = 0
            for v in range(len(tangents) // 4):
                x, y, z, w = tangents[4 * v:4 * v + 4]
                square = x * x + y * y + z * z
                if 0.999 < square < 1.001 and w in (1.0, -1.0):
                    continue
                unit = _unit((x, y, z)) if square > 1e-10 and square == square and square != float("inf") else None
                if unit is None:
                    n = tuple(normals[3 * v:3 * v + 3])
                    unit = _unit(_cross(n, (0.0, 0.0, 1.0))) or _unit(_cross(n, (0.0, 1.0, 0.0))) or (1.0, 0.0, 0.0)
                tangents[4 * v:4 * v + 4] = array.array("f", (unit[0], unit[1], unit[2], -1.0 if w < 0 else 1.0))
                changed += 1
            if changed:
                store(tangents, t_start)
                fixed["tangents"] += changed
    if fixed["normals"] or fixed["tangents"]:
        with open(path, "wb") as fh:
            fh.write(blob)
    return fixed


# ---------------------------------------------------------------------------
# Text -> image, and the game-ready hand-off
# ---------------------------------------------------------------------------
PROMPT_FRAMING = ("single object, whole object in frame, centered, three-quarter view, "
                  "plain white background, even studio lighting, no cast shadow")


def render_concept(args, out_dir, log):
    """--prompt: one image through image-stack.ps1 generate; returns its path."""
    prompt = args.prompt if args.raw_prompt else f"{args.prompt}, {PROMPT_FRAMING}"
    cli = ["generate", "-Prompt", prompt, "-Width", "1024", "-Height", "1024", "-Count", "1",
           "-Out", os.path.join(out_dir, "concept"), "-Json"]
    for flag, value in (("-Preset", args.preset), ("-Model", args.image_model), ("-Negative", args.negative)):
        if value:
            cli += [flag, value]
    if args.image_seed is not None:
        cli += ["-Seed", str(args.image_seed)]
    if args.no_start:
        cli.append("-NoStart")
    log("..   concept     rendering the image the model is built from")
    code, lines = image_stack(cli, timeout=1800)
    if code != 0:
        raise BackendError("the concept image could not be rendered: " + (lines[-1] if lines else f"exit {code}"))
    try:
        result = json.loads(lines[-1])
        path = result["files"][0]
    except (ValueError, KeyError, IndexError):
        raise BackendError("image-stack.ps1 generate printed no result line")
    log(f"ok   concept     {path} (seed {result.get('seed')}, model {result.get('model')})")
    return path, {"prompt": prompt, "seed": result.get("seed"), "model": result.get("model"),
                  "preset": result.get("preset")}


def game_ready(glb, budget, log):
    """Asset Forge mesh-gameready through TOOLS/game-ready.ps1; returns the written path."""
    shell = powershell()
    if not shell:
        raise BackendError("PowerShell was not found, so TOOLS/game-ready.ps1 cannot run")
    dest = os.path.splitext(glb)[0] + "-gameready.glb"
    cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(REPO, "TOOLS", "game-ready.ps1"),
           "-Input", glb, "-Budget", budget, "-Out", dest]
    log(f"..   game-ready  Asset Forge mesh-gameready, budget {budget}")
    run = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600)
    lines = [line for line in (run.stdout + run.stderr).splitlines() if line.strip()]
    if run.returncode != 0 or not os.path.isfile(dest):
        raise BackendError("game-ready failed: " + (" | ".join(lines[-3:]) if lines else f"exit {run.returncode}"))
    for line in lines:
        if line.startswith(("cap ", "PASS", "FAIL", "WARN")):
            log(f"     {line}")
    return dest


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def parse_views(text):
    paths = [p.strip() for p in (text or "").split(",")]
    if not 1 <= len(paths) <= 4 or not paths[0]:
        raise UsageError("--views takes one to four files: front[,left,back,right]")
    return {view: path for view, path in zip(VIEW_NAMES, paths) if path}


def run_generate(args, out_stream=sys.stdout):
    def log(message):
        print(message, file=out_stream, flush=True)

    variant = args.variant or DEFAULT_VARIANT
    spec = VARIANTS[variant]
    sources = [bool(args.image), bool(args.views), bool(args.prompt)]
    if sum(sources) != 1:
        raise UsageError("give exactly one of --image, --views or --prompt")
    if args.views and spec["input"] != "views":
        raise UsageError(f"--views is for pixal3d-multi; {variant} takes one --image")
    if args.prompt and spec["input"] != "image":
        raise UsageError("--prompt renders one picture; pixal3d-multi needs --image (a turnaround sheet) or --views")
    views = parse_views(args.views) if args.views else None
    sheet = None
    if args.image:
        sheet = check_image(args.image, "--image")
    for view, path in (views or {}).items():
        check_image(path, f"the {view} view")
    name = args.name or os.path.splitext(os.path.basename(args.image or (views or {}).get("front") or "model"))[0]
    out_dir = os.path.abspath(args.out)
    # Validate the whole graph before any backend work.
    build_graph(variant, image="placeholder.png" if spec["input"] == "image" or not views else None,
                views={v: "placeholder.png" for v in views} if views else None, sheet_size=sheet or (1024, 1024),
                name=name, seed=args.seed)

    concept = None
    try:
        image_path = args.image
        if args.prompt:
            image_path, concept = render_concept(args, out_dir, log)
            check_image(image_path, "the concept image")
        base, stats = ensure_backend(args.url, not args.no_start, log)
        if not stats:
            log(no_backend_message(args.url))
            return 3
        log(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')} at {base}")
        remote_views = {v: upload_image(base, p) for v, p in views.items()} if views else None
        remote_image = upload_image(base, image_path) if image_path else None
        graph = build_graph(variant, image=remote_image, views=remote_views, sheet_size=sheet, name=name,
                            seed=args.seed)
        problems = preflight(base, graph, variant)
        if problems:
            for p in problems:
                log(f"FAIL {p}")
            return 1
        try:
            res = http_json(base + "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})
        except urllib.error.HTTPError as e:
            log(f"FAIL /prompt rejected the graph: {e.read()[:900]!r}")
            return 1
        pid = res.get("prompt_id")
        if not pid:
            log(f"FAIL no prompt_id in {res}")
            return 1
        log(f"ok   queued      {pid} (variant {variant}, {len(graph)} nodes)")
        entry, elapsed = wait_for(base, pid, args.timeout, log)
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            log(f"FAIL execution error: {json.dumps(status.get('messages', ''))[:900]}")
            return 1
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for item in collect_files(entry):
            query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                            "type": item.get("type", "output")})
            dest = os.path.abspath(unique_path(out_dir, os.path.basename(item["filename"])))
            with open(dest, "wb") as fh:
                fh.write(http_bytes(f"{base}/view?{query}", timeout=900))
            written.append(dest)
        models = [p for p in written if p.lower().endswith(MODEL_EXT)]
        splats = [p for p in written if p.lower().endswith(SPLAT_EXT)]
        if not models and not splats:
            log("FAIL the run finished but saved no 3D file")
            return 1
        repaired = {}
        for path in models:
            if path.lower().endswith(".glb"):
                try:
                    repaired[path] = repair_glb_vectors(path)
                except (OSError, ValueError, KeyError, IndexError, struct.error) as e:
                    warn(f"{os.path.basename(path)}: its normals could not be checked ({e})")
        summary = glb_summary(models[0]) if models and models[0].lower().endswith(".glb") else {}
        for path in written:
            log(f"ok   file        {path}" + (f" {summary}" if models and path == models[0] else ""))
            fix = repaired.get(path) or {}
            if fix.get("normals") or fix.get("tangents"):
                log(f"ok   repaired    {fix['normals']} zero-length normal(s), {fix['tangents']} tangent(s): "
                    "the file is valid glTF now")
        log(f"ok   rendered    in {elapsed:.0f}s")
        if summary and summary.get("bytes", 0) < 10000:
            log("FAIL the .glb is suspiciously small")
            return 1
        ready, ready_error = None, None
        if args.game_ready:
            if not models:
                ready_error = "--game-ready needs a .glb; this run wrote only a splat"
            else:
                try:
                    ready = game_ready(models[0], args.budget, log)
                    log(f"ok   game-ready  {ready} {glb_summary(ready)}")
                except BackendError as e:
                    # The model itself exists and is kept; only the last step failed.
                    ready_error = str(e)
            if ready_error:
                log(f"FAIL {ready_error}")
    except BackendError as e:
        log(f"FAIL {e}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        log(f"FAIL backend connection: {e}")
        return 1
    except subprocess.TimeoutExpired as e:
        log(f"FAIL a step did not finish in time: {e}")
        return 1

    result = {
        "variant": variant, "name": name, "files": written,
        "model": models[0] if models else None, "splat": splats[0] if splats else None,
        "game_ready": ready, "game_ready_error": ready_error, "triangles": summary.get("triangles"),
        "game_ready_triangles": glb_summary(ready).get("triangles") if ready else None,
        "image": os.path.abspath(image_path) if image_path else None,
    }
    record = dict(result, tool="comfy-forge model3d", tool_version=tool_version(),
                  views={v: os.path.abspath(p) for v, p in (views or {}).items()} or None,
                  concept=concept, seed=args.seed, budget=args.budget if args.game_ready else None,
                  comfyui_version=stats.get("system", {}).get("comfyui_version"), prompt_id=pid,
                  render_seconds=round(elapsed, 1), created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    anchor = models[0] if models else splats[0]
    with open(anchor + ".json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    log(json.dumps(result))
    # The closing line is printed either way, so a caller still learns where the
    # model is when only the game-ready step failed.
    return 1 if ready_error else 0


def variant_files(variant):
    return [[folder, name] for folder, name, _cls, _field in required_files(
        template_to_api(load_template(variant), VARIANTS[variant].get("switches")))]


def cmd_variants(args):
    if args.json:
        print(json.dumps({key: {"title": spec["title"], "input": spec["input"], "install_set": spec["install_set"],
                                "files": variant_files(key)} for key, spec in VARIANTS.items()}, indent=2))
        return 0
    for key, spec in VARIANTS.items():
        print(f"{key:14} {spec['title']}")
        print(f"               {', '.join(name for _folder, name in variant_files(key))}")
    print(f"default: {DEFAULT_VARIANT}")
    return 0


def cmd_check(args):
    base, stats = resolve_backend(args.url)
    if not stats:
        print(no_backend_message(args.url))
        return 3
    print(f"ok   backend     ComfyUI {stats.get('system', {}).get('comfyui_version', '?')} at {base}")
    names = list(VARIANTS) if args.variant == "all" else [args.variant or DEFAULT_VARIANT]
    ready, failed = [], 0
    for name in names:
        spec = VARIANTS[name]
        graph = build_graph(name, image="check.png", sheet_size=(1024, 1024))
        problems = [p for p in preflight(base, graph, name)]
        if not problems:
            ready.append(name)
            print(f"ok   variant     {name}: nodes registered and model files visible ({len(graph)} nodes)")
        elif args.variant == "all":
            print(f"..   variant     {name}: not ready ({len(problems)} problem(s); "
                  f"TOOLS\\Install-3DModels.ps1 -Set {spec['install_set']})")
        else:
            failed += 1
            for p in problems:
                print(f"FAIL {p}")
    if args.variant == "all":
        print(f"ready: {', '.join(ready) if ready else 'none (TOOLS\\Install-3DModels.ps1 -WhatIf lists the files)'}")
        return 0 if ready else 1
    return 1 if failed else 0


def cmd_build(args):
    variant = args.variant or DEFAULT_VARIANT
    views = parse_views(args.views) if args.views else None
    sheet = check_image(args.image, "--image") if args.image and os.path.isfile(args.image) else (1024, 1024)
    graph = build_graph(variant, image=os.path.basename(args.image) if args.image else None,
                        views={v: os.path.basename(p) for v, p in views.items()} if views else None,
                        sheet_size=sheet, name=args.name or "model", seed=args.seed)
    text = json.dumps(graph, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def make_parser():
    p = argparse.ArgumentParser(description="Image or text to a 3D model through ComfyUI (Pixal3D, TRELLIS.2, Idol)")
    sub = p.add_subparsers(dest="command", required=True)
    url_help = "ComfyUI address; default: COMFYUI_URL, else the first of 8188 and 7820-7832 that answers"

    def source_args(sp):
        sp.add_argument("--variant", default=None, choices=list(VARIANTS), help=f"default {DEFAULT_VARIANT}")
        sp.add_argument("--image", help="the picture to build from; for pixal3d-multi a four-view turnaround sheet "
                                        "(front, left, back, right, side by side)")
        sp.add_argument("--views", help="pixal3d-multi: front[,left,back,right] as separate square pictures")
        sp.add_argument("--name", default="", help="file name stem for the result, e.g. 'rune-axe'")
        sp.add_argument("--seed", type=int, default=None,
                        help="replace every sampler seed; default: the template's fixed seeds, so a run repeats")

    v = sub.add_parser("variants", help="list the variants and the files each needs")
    v.add_argument("--json", action="store_true", help="machine-readable list with model subfolders")
    c = sub.add_parser("check", help="is the backend ready for a variant (or --variant all)")
    c.add_argument("--variant", default=DEFAULT_VARIANT, choices=list(VARIANTS) + ["all"])
    c.add_argument("--url", default=os.environ.get("COMFYUI_URL"), help=url_help)
    b = sub.add_parser("build", help="write the API graph without contacting a backend")
    source_args(b)
    b.add_argument("--out", default="", help="write the graph here instead of stdout")
    g = sub.add_parser("generate", help="build a model and save it")
    source_args(g)
    g.add_argument("--prompt", help="describe the object; a picture is rendered first (image-stack.ps1 generate)")
    g.add_argument("--raw-prompt", action="store_true",
                   help="send --prompt as written, without the one-object, plain-background framing")
    g.add_argument("--preset", default="", help="--prompt: image preset (default: image-stack.ps1's)")
    g.add_argument("--image-model", default="", help="--prompt: image checkpoint")
    g.add_argument("--negative", default="", help="--prompt: negative prompt")
    g.add_argument("--image-seed", type=int, default=None, help="--prompt: seed of the picture")
    g.add_argument("--game-ready", action="store_true",
                   help="then run Asset Forge mesh-gameready on the .glb (TOOLS/game-ready.ps1)")
    g.add_argument("--budget", default="game", choices=("game", "hero", "accessory"),
                   help="--game-ready: triangle budget (default game, 20,000)")
    g.add_argument("--url", default=os.environ.get("COMFYUI_URL"), help=url_help)
    g.add_argument("--no-start", action="store_true", help="do not start a backend when none answers")
    g.add_argument("--out", default=os.path.join("out", "3d"), help="folder for the files (default out/3d)")
    g.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the run")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    try:
        if args.command == "variants":
            return cmd_variants(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "build":
            return cmd_build(args)
        return run_generate(args)
    except UsageError as e:
        print(f"error  {e}", file=sys.stderr)
        return 2
    except ConvertError as e:
        print(f"error  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
