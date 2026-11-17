"""TOOLS/model3d.py: the shipped 3D templates as executable graphs, and the CLI around them.

No GPU and no backend: the conversion is checked against the template files and the
generation lock, and the CLI runs against TESTS/fixtures/backend_stub.py.

What this proves:
  - every variant converts, and what it converts to carries the locked sampler,
    decimate and bake values (so the lock guards the graph that runs)
  - the branch a switch does not take is gone, so an optional model is never asked for
  - the files `variants` lists are the files the installer downloads
  - subgraphs (Idol) are expanded with their widget values and wiring intact
  - generate uploads the picture, queues, saves the model with a sidecar, skips previews
  - a mesh with zero-length normals leaves this tool as valid glTF
"""
import array
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "TOOLS"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import backend_stub  # noqa: E402
import model3d  # noqa: E402

TOOL = os.path.join(REPO, "TOOLS", "model3d.py")
fail = 0


def check(cond, name, detail=""):
    global fail
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name, detail)
        fail += 1


def classes(graph):
    return sorted(n["class_type"] for n in graph.values())


def files(graph):
    return sorted(name for _folder, name, _cls, _field in model3d.required_files(graph))


def conversion():
    lock = json.load(open(os.path.join(REPO, "presets", "3d", "generation.json"), encoding="utf-8"))
    graphs = {v: model3d.build_graph(v, image="in.png", sheet_size=(4000, 1000)) for v in model3d.VARIANTS}

    for name, graph in graphs.items():
        dangling = [(nid, key) for nid, node in graph.items() for key, value in node["inputs"].items()
                    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in graph]
        check(not dangling, f"{name}: every link points at a node in the graph", str(dangling[:3]))
        check(any(n["class_type"] in model3d.SAVERS for n in graph.values()), f"{name}: something saves a file")
        check(not any(c in ("ComfySwitchNode", "Note", "MarkdownNote", "Preview3DAdvanced") for c in classes(graph)),
              f"{name}: no switch, note or 3D preview node is sent")
        check(not any(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", c) for c in classes(graph)),
              f"{name}: no unexpanded subgraph is sent")
        check(all((n["class_type"], k) not in model3d.UI_ONLY for n in graph.values() for k in n["inputs"]),
              f"{name}: no editor-only widget is sent")

    for name in ("pixal3d", "pixal3d-multi", "trellis2"):
        graph = graphs[name]
        got = sorted((n["inputs"]["seed"], n["inputs"]["steps"], n["inputs"]["cfg"], n["inputs"]["sampler_name"],
                      n["inputs"]["scheduler"], n["inputs"]["denoise"]) for n in graph.values() if n["class_type"] == "KSampler")
        want = sorted((row[0], row[2], row[3], row[4], row[5], row[6]) for row in lock["pixal3d"]["ksampler"])
        check(got == want, f"{name}: the four samplers carry the locked seed, steps, cfg, sampler and scheduler", str(got))
        decimate = [n["inputs"] for n in graph.values() if n["class_type"] == "DecimateMesh"][0]
        check([decimate["target_face_count"], decimate["placement_mode"]] == lock["pixal3d"]["decimate"],
              f"{name}: the decimate cap is the locked {lock['pixal3d']['decimate'][0]}")
        remesh = [n["inputs"] for n in graph.values() if n["class_type"] == "RemeshMesh"][0]
        check(remesh["resolution"] == lock["pixal3d"]["remesh_resolution"] and remesh["sign_mode"] == "solid"
              and remesh["sign_mode.fill"] == 20 and remesh["sign_mode.qef"] is False,
              f"{name}: a widget with nested options keeps each value under its own name", str(remesh))
        texture = [n["inputs"]["value"] for n in graph.values() if n["class_type"] == "PrimitiveInt"]
        check(texture == [lock["pixal3d"]["texture_resolution"][0]], f"{name}: texture resolution is the locked value")
        unwrap = [n["inputs"] for n in graph.values() if n["class_type"] == "UnwrapMesh"][0]
        check(isinstance(unwrap["resolution"], list) and unwrap["segmenter"] == "pec",
              f"{name}: a widget that is also wired takes the wire")

    check("pixal3d_int8_convrot.safetensors" in files(graphs["pixal3d"])
          and "trellis_2_int8_convrot.safetensors" not in files(graphs["pixal3d"])
          and "Pixal3DConditioning" in classes(graphs["pixal3d"]) and "Trellis2Conditioning" not in classes(graphs["pixal3d"]),
          "pixal3d: the stock TRELLIS.2 branch is gone, so its optional model is never asked for")
    check("trellis_2_int8_convrot.safetensors" in files(graphs["trellis2"])
          and "pixal3d_int8_convrot.safetensors" not in files(graphs["trellis2"])
          and "moge_2_vitl_normal_fp16.safetensors" not in files(graphs["trellis2"]),
          "trellis2: the Pixal3D branch (and the depth model only it uses) is gone")
    check(files(graphs["pixal3d-multi"]) == ["birefnet.safetensors", "dino_v3_L_naf_fp32.safetensors",
                                             "pixal3d_multiview_int8_convrot.safetensors",
                                             "trellis_2_shape_vae_bf16.safetensors", "trellis_2_texture_vae_bf16.safetensors"],
          "pixal3d-multi: file list", str(files(graphs["pixal3d-multi"])))

    # Idol: two nested subgraphs, values handed down through subgraph inputs.
    idol = graphs["idol"]
    check(files(idol) == ["birefnet.safetensors", "dino_v3_vit_h_bf16.safetensors", "flux2-vae.safetensors",
                          "idol_decoder_fp16.safetensors", "idol_fp16.safetensors"], "idol: file list", str(files(idol)))
    sampler = [n["inputs"] for n in idol.values() if n["class_type"] == "KSampler"][0]
    check((sampler["seed"], sampler["steps"], sampler["cfg"], sampler["sampler_name"]) == (46, 20, 3, "dpmpp_2m"),
          "idol: a value passed in through a subgraph input reaches the node inside", str(sampler))
    decode = [n["inputs"] for n in idol.values() if n["class_type"] == "VAEDecodeTripoSplat"][0]
    check(decode["num_gaussians"] == 262144 and decode["seed"] == 790219963981395, "idol: seed control values are dropped")
    to_mesh = [n["inputs"] for n in idol.values() if n["class_type"] == "SplatToMesh"][0]
    check([to_mesh[k] for k in ("resolution", "kernel", "smooth", "level", "min_component", "min_opacity",
                                "color_sharpen", "solid", "fill")] == lock["idol"]["splat_to_mesh"],
          "idol: splat-to-mesh carries the locked values")
    removal = [nid for nid, n in idol.items() if n["class_type"] == "RemoveBackground"]
    pre = [n["inputs"] for n in idol.values() if n["class_type"] == "TripoSplatPreprocessImage"][0]
    check(len(removal) == 1 and pre["mask"] == [removal[0], 0],
          "idol: an output of a nested subgraph is wired through both levels", str(pre))
    check(sorted(n["inputs"]["filename_prefix"] for n in idol.values() if n["class_type"] in ("SaveGLB", "SaveVideo"))
          == ["3d/model", "3d/model", "video/model"], "savers write under the run's name")

    # Installer and variants agree.
    installer = open(os.path.join(REPO, "TOOLS", "Install-3DModels.ps1"), encoding="utf-8").read()
    for name, graph in graphs.items():
        missing = [f for f in files(graph) if f not in installer]
        check(not missing, f"{name}: every file is one Install-3DModels.ps1 downloads", str(missing))

    # Multi-view inputs.
    crops = sorted((n["inputs"]["crop_region"]["x"], n["inputs"]["crop_region"]["width"], n["inputs"]["crop_region"]["height"])
                   for n in graphs["pixal3d-multi"].values() if n["class_type"] == "ImageCropV2")
    check(crops == [(0, 1000, 1000), (1000, 1000, 1000), (2000, 1000, 1000), (3000, 1000, 1000)],
          "a turnaround sheet is cut into four equal views", str(crops))
    two = model3d.build_graph("pixal3d-multi", views={"front": "f.png", "back": "b.png"})
    cond = [n["inputs"] for n in two.values() if n["class_type"] == "Pixal3DMultiViewConditioning"][0]
    loads = sorted(n["inputs"]["image"] for n in two.values() if n["class_type"] == "LoadImage")
    check(set(cond) >= {"front", "back"} and "left" not in cond and "right" not in cond and loads == ["b.png", "f.png"]
          and "ImageCropV2" not in classes(two) and classes(two).count("RemoveBackground") == 2,
          "--views loads each picture itself and drops the views that were not given", str(sorted(cond)))
    seeded = model3d.build_graph("pixal3d", image="in.png", seed=7)
    check({n["inputs"]["seed"] for n in seeded.values() if n["class_type"] == "KSampler"} == {7}, "--seed replaces every sampler seed")

    # Refusals inside the converter.
    def raises(fn, kind):
        try:
            fn()
        except kind:
            return True
        return False
    node = {"id": 1, "type": "X", "inputs": [{"name": "a", "widget": {"name": "a"}}], "widgets_values": [1, 2]}
    check(raises(lambda: model3d.widget_values(node), model3d.ConvertError), "more widget values than widgets is refused, not guessed")
    doc = {"nodes": [{"id": 1, "type": "A", "mode": 4, "inputs": [], "outputs": []},
                     {"id": 2, "type": "SaveGLB", "inputs": [{"name": "mesh", "link": 5}]}], "links": [[5, 1, 0, 2, 0, "MESH"]]}
    check(raises(lambda: model3d.template_to_api(doc), model3d.ConvertError), "a bypassed node that something reads is refused")
    check(raises(lambda: model3d.template_to_api({"nodes": [{"id": 1, "type": "KSampler", "inputs": []}], "links": []}),
                 model3d.ConvertError), "a template that saves nothing is refused")
    check(raises(lambda: model3d.build_graph("pixal3d", image="a.png", views={"front": "f.png"}), model3d.UsageError)
          and raises(lambda: model3d.build_graph("pixal3d-multi", views={"left": "l.png"}), model3d.UsageError)
          and raises(lambda: model3d.build_graph("nope", image="a.png"), model3d.UsageError), "wrong inputs are usage errors")
    switch = {"1": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
              "2": {"class_type": "ComfySwitchNode", "inputs": {"switch": ["1", 0], "on_true": ["3", 0], "on_false": ["4", 0]}},
              "3": {"class_type": "A", "inputs": {}}, "4": {"class_type": "B", "inputs": {}},
              "5": {"class_type": "SaveGLB", "inputs": {"mesh": ["2", 0]}}}
    model3d.fold_switches(switch)
    check(classes(model3d.prune(switch)) == ["A", "SaveGLB"], "a constant switch is folded and the other branch pruned")


def image_sizes(root):
    png = os.path.join(root, "a.png")
    with open(png, "wb") as fh:
        fh.write(backend_stub.png(37, 21))
    jpeg = os.path.join(root, "a.jpg")
    with open(jpeg, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                 b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 480, 640) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9")
    webp = os.path.join(root, "a.webp")
    with open(webp, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 30) + b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
                 + (799).to_bytes(3, "little") + (599).to_bytes(3, "little"))
    check(tuple(model3d.image_size(png)) == (37, 21) and tuple(model3d.image_size(jpeg)) == (640, 480)
          and tuple(model3d.image_size(webp)) == (800, 600), "image sizes are read from PNG, JPEG and WebP headers")
    return png


def make_glb(path, normals, tangents=None, positions=None, indices=(0, 1, 2)):
    """A one-triangle .glb with the given normals (and tangents)."""
    positions = positions or [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    blobs = [array.array("f", [c for p in positions for c in p]).tobytes(),
             array.array("f", [c for n in normals for c in n]).tobytes(),
             array.array("I", indices).tobytes()]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
                 {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
                 {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"}]
    attributes = {"POSITION": 0, "NORMAL": 1}
    if tangents:
        blobs.append(array.array("f", [c for t in tangents for c in t]).tobytes())
        accessors.append({"bufferView": 3, "componentType": 5126, "count": len(tangents), "type": "VEC4"})
        attributes["TANGENT"] = 3
    views, offset = [], 0
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(blob)})
        offset += len(blob)
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": offset}], "bufferViews": views, "accessors": accessors,
           "meshes": [{"primitives": [{"attributes": attributes, "indices": 2}]}]}
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    data = b"".join(blobs)
    with open(path, "wb") as fh:
        fh.write(b"glTF" + struct.pack("<II", 2, 28 + len(body) + len(data)) + struct.pack("<I", len(body)) + b"JSON" + body
                 + struct.pack("<I", len(data)) + b"BIN\x00" + data)
    return 28 + len(body)


def read_floats(path, start, count):
    with open(path, "rb") as fh:
        fh.seek(start)
        out = array.array("f")
        out.frombytes(fh.read(count * 4))
        return [round(v, 5) for v in out]


def glb_repair(root):
    path = os.path.join(root, "bad.glb")
    start = make_glb(path, normals=[(0, 0, 0), (0, 0, 2), (0, 0, 1)], tangents=[(0, 0, 0, -1), (1, 0, 0, 1), (3, 0, 0, 1)])
    before = open(path, "rb").read()
    fixed = model3d.repair_glb_vectors(path)
    after = open(path, "rb").read()
    check(fixed == {"normals": 2, "tangents": 2}, "zero and non-unit normals and tangents are counted", str(fixed))
    normals = read_floats(path, start + 36, 9)
    check(normals == [0, 0, 1, 0, 0, 1, 0, 0, 1], "a zero normal becomes its triangle's normal; a long one is normalised", str(normals))
    tangents = read_floats(path, start + 36 + 36 + 12, 12)
    check(abs(sum(v * v for v in tangents[0:3]) - 1) < 1e-4 and tangents[3] == -1 and tangents[8:12] == [1, 0, 0, 1],
          "a zero tangent becomes a unit vector and keeps its handedness", str(tangents))
    check(len(after) == len(before) and after[:start + 36] == before[:start + 36],
          "only the bytes of those vectors change: header, JSON and positions are untouched")
    check(model3d.repair_glb_vectors(path) == {"normals": 0, "tangents": 0} and open(path, "rb").read() == after,
          "a valid file is left exactly as it is")
    info = model3d.glb_summary(path)
    check(info.get("triangles") == 1, "the triangle count is read from the file", str(info))
    junk = os.path.join(root, "junk.glb")
    with open(junk, "wb") as fh:
        fh.write(b"not a glb at all")
    check(model3d.repair_glb_vectors(junk) == {"normals": 0, "tangents": 0}, "a file that is not a .glb is left alone")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tool(args, env, timeout=120):
    run = subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env, timeout=timeout)
    return run.returncode, run.stdout, run.stderr


def cli(root, picture):
    port = free_port()
    record = os.path.join(root, "record")
    env = dict(os.environ, COMFY_FORGE_PROBE_PORTS=str(port), COMFY_FORGE_NO_AUTOSTART="1")
    env.pop("COMFYUI_URL", None)
    url = f"http://127.0.0.1:{port}"

    code, out, err = tool(["variants", "--json"], env)
    table = json.loads(out) if code == 0 else {}
    check(set(table) == set(model3d.VARIANTS) and ["diffusion_models", "pixal3d_int8_convrot.safetensors"] in table["pixal3d"]["files"],
          "variants --json lists each variant's files with their model folders", out[:200])
    code, out, err = tool(["generate", "--image", picture, "--out", os.path.join(root, "o")], env)
    check(code == 3 and "UNVERIFIED" in out, "no backend and no start: exit 3", out + err)
    for args, what in ((["generate"], "no input"), (["generate", "--image", picture, "--prompt", "x"], "two inputs"),
                       (["generate", "--image", os.path.join(root, "none.png")], "a missing picture"),
                       (["generate", "--variant", "pixal3d", "--views", picture], "--views with a single-image variant"),
                       (["generate", "--variant", "pixal3d-multi", "--prompt", "x"], "--prompt with the multi-view variant")):
        code, out, err = tool(args, env)
        check(code == 2 and "error" in err, f"{what}: exit 2, before any backend work", out + err)
    code, out, err = tool(["build", "--image", picture, "--out", os.path.join(root, "g.json")], env)
    built = json.load(open(os.path.join(root, "g.json"))) if code == 0 else {}
    check(code == 0 and any(n["class_type"] == "Save3DAdvanced" for n in built.values()), "build writes the graph with no backend")

    server = subprocess.Popen([sys.executable, os.path.join(HERE, "fixtures", "backend_stub.py"), "--port", str(port),
                               "--record", record], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env=dict(env, COMFY_FORGE_TEST_MISSING=json.dumps(["trellis_2_int8_convrot.safetensors"])))
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/system_stats", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        code, out, err = tool(["check", "--variant", "all"], env)
        check(code == 0 and "ready: pixal3d, pixal3d-multi, idol" in out and "trellis2: not ready" in out,
              "check --variant all separates installed variants from missing ones", out + err)
        code, out, err = tool(["check", "--variant", "trellis2"], env)
        check(code == 1 and "Install-3DModels.ps1 -Set trellis2" in out, "a missing model names the installer set", out + err)

        dest = os.path.join(root, "models")
        code, out, err = tool(["generate", "--image", picture, "--name", "Rune Axe", "--out", dest], env)
        result = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
        check(code == 0 and result.get("variant") == "pixal3d", "generate runs against the backend it found by itself", out + err)
        model = result.get("model") or ""
        check(os.path.isfile(model) and model.endswith(".glb") and os.path.basename(model).startswith("Rune-Axe")
              and os.path.dirname(model) == os.path.abspath(dest), "the model is saved under the run's name", model)
        check(result.get("triangles") == 12 and result.get("files") == [model] and result.get("game_ready") is None,
              "the closing JSON line names the file and its triangle count; previews are not downloaded", str(result))
        side = json.load(open(model + ".json", encoding="utf-8")) if os.path.isfile(model + ".json") else {}
        check(side.get("tool") == "comfy-forge model3d" and side.get("image") == os.path.abspath(picture)
              and side.get("prompt_id"), "a sidecar records what the model was made from", str(side)[:200])
        rows = [json.loads(line) for line in open(os.path.join(record, "requests.jsonl"), encoding="utf-8")]
        uploads = [r["body"]["name"] for r in rows if r["route"] == "upload/image"]
        prompts = [r["body"]["prompt"] for r in rows if r["route"] == "prompt"]
        loaded = [n["inputs"]["image"] for n in prompts[-1].values() if n["class_type"] == "LoadImage"] if prompts else []
        check(len(uploads) == 1 and loaded == uploads, "the picture is uploaded and the graph loads that upload", str(loaded))
        code, out, err = tool(["generate", "--image", picture, "--name", "Rune Axe", "--out", dest], env)
        again = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
        check(code == 0 and again.get("model") != model and os.path.isfile(model), "a second run never overwrites the first", str(again))

        code, out, err = tool(["generate", "--variant", "idol", "--image", picture, "--out", dest, "--url", url], env)
        result = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
        check(code == 0 and str(result.get("splat")).endswith(".spz") and str(result.get("model")).endswith(".glb"),
              "idol returns the splat and the mesh", out + err)
        code, out, err = tool(["generate", "--variant", "pixal3d-multi", "--views", f"{picture},{picture}", "--out", dest], env)
        rows = [json.loads(line) for line in open(os.path.join(record, "requests.jsonl"), encoding="utf-8")]
        last = [r["body"]["prompt"] for r in rows if r["route"] == "prompt"][-1]
        cond = [n["inputs"] for n in last.values() if n["class_type"] == "Pixal3DMultiViewConditioning"][0]
        check(code == 0 and "front" in cond and "left" in cond and "back" not in cond, "--views sends the views that were given", out + err)
        code, out, err = tool(["generate", "--variant", "trellis2", "--image", picture, "--out", dest], env)
        check(code == 1 and "trellis_2_int8_convrot.safetensors" in out, "a variant whose model is missing fails before queueing", out + err)
        code, out, err = tool(["generate", "--image", picture, "--out", dest, "--url", "http://127.0.0.1:9"], env)
        check(code == 3, "an explicit --url that does not answer is exit 3, never another server", out + err)
    finally:
        server.kill()
        server.wait()


def main():
    root = tempfile.mkdtemp(prefix="cf-model3d-")
    try:
        conversion()
        picture = image_sizes(root)
        glb_repair(root)
        cli(root, picture)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if fail:
        print(f"MODEL3D: {fail} FAILED")
        return 1
    print("MODEL3D: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
