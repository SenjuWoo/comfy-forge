#!/usr/bin/env python3
"""
Merge math check. Builds three tiny checkpoints with known numbers, merges them
and asserts the result matches the formula the ComfyUI node uses. No GPU, no
real model, runs in about a second.

Run: python TESTS/test_merge.py
"""
import os
import json
import struct
import sys

import torch
from safetensors.torch import load_file, save_file

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "TOOLS"))
import merge_anima  # noqa: E402

FAIL = 0


def close(t, want, atol=0.02):
    """bf16 keeps ~3 significant digits, so compare within bf16 resolution.
    Float32 fixtures can tighten atol to prove exact arithmetic."""
    return torch.allclose(t.float(), want, atol=atol)


def check(condition, name):
    global FAIL
    print(("ok   " if condition else "FAIL ") + name)
    if not condition:
        FAIL += 1


def read_meta(path):
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        return __import__("json").loads(f.read(n)).get("__metadata__", {})


def main():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="anima-merge-test-")
    base_path = os.path.join(tmp, "base.safetensors")
    a_path = os.path.join(tmp, "donor-a.safetensors")
    b_path = os.path.join(tmp, "donor-b.safetensors")
    out_path = os.path.join(tmp, "out.safetensors")

    # base=0, a=1, b=3 everywhere; the adapter and a non-float tensor must survive.
    base = {
        "net.blocks.0.w": torch.zeros(4, 4, dtype=torch.bfloat16),
        "net.blocks.1.w": torch.zeros(2, dtype=torch.bfloat16),
        "net.llm_adapter.blocks.0.w": torch.zeros(4, 4, dtype=torch.bfloat16),
        "net.blocks.0.scale_weight": torch.ones(2, dtype=torch.float32),
    }
    a = {k: torch.ones_like(v) for k, v in base.items()}
    b = {k: torch.full_like(v, 3) for k, v in base.items()}
    # Full checkpoints carry components the donors do not have (encoder, VAE)
    # and 0-dim scalar tensors, which cannot be viewed as uint8 directly.
    base["cond_stage_model.transformer.w"] = torch.zeros(3, dtype=torch.bfloat16)
    base["first_stage_model.scalar"] = torch.tensor(0.5)
    save_file(base, base_path, metadata={
        "modelspec.title": "the-base", "modelspec.architecture": "anima",
        # Provenance that belongs to the BASE only: a merge must not keep it.
        "workflow": "{\"nodes\":[]}", "prompt": "{\"1\":{}}",
        "modelspec.hash_sha256": "0xdeadbeef", "__spacer": "   ",
    })
    save_file(a, a_path)
    save_file(b, b_path)

    rc = merge_anima.main([
        "--base", base_path, "--donor", a_path, "--donor", b_path,
        "--weights", "0.6,0.4", "--scale", "1.0", "--method", "task-arithmetic",
        "--out", out_path, "--title", "test-merge",
    ])
    check(rc == 0, "task-arithmetic exits 0")
    check(os.path.exists(out_path), "checkpoint written")

    got = load_file(out_path)
    # out = base + sum(w*(theta-base))/sum(w) = (0.6*1 + 0.4*3) = 1.8
    check(close(got["net.blocks.0.w"], torch.full((4, 4), 1.8)),
          "task-arithmetic value is 1.8")
    check(close(got["net.blocks.1.w"], torch.full((2,), 1.8)),
          "second tensor uses the same formula")
    check(close(got["net.llm_adapter.blocks.0.w"], torch.zeros(4, 4)),
          "llm_adapter kept from base")
    check(torch.allclose(got["net.blocks.0.scale_weight"].float(), torch.ones(2)),
          "scale_weight untouched")
    check(got["net.blocks.0.w"].dtype == torch.bfloat16, "output keeps base dtype")
    check("cond_stage_model.transformer.w" in got, "base-only component kept")
    check(abs(float(got["first_stage_model.scalar"]) - 0.5) < 1e-6,
          "0-dim scalar tensor round-trips")
    meta = read_meta(out_path)
    check(meta.get("modelspec.title") == "test-merge", "title written")
    check(meta.get("modelspec.architecture") == "anima", "base metadata preserved")
    check("task-arithmetic" in meta.get("modelspec.merge", ""), "merge provenance recorded")
    check("workflow" not in meta and "prompt" not in meta,
          "base generation graph not inherited")
    check("modelspec.hash_sha256" not in meta, "stale base hash dropped")
    check(meta.get("modelspec.description", "").startswith("Task-arithmetic merge"),
          "merge description written")

    # scale 2.0 extrapolates: base(0) + 2 * 1.8 = 3.6
    scaled = os.path.join(tmp, "scaled.safetensors")
    merge_anima.main([
        "--base", base_path, "--donor", a_path, "--donor", b_path,
        "--weights", "0.6,0.4", "--scale", "2.0", "--out", scaled,
    ])
    check(close(load_file(scaled)["net.blocks.0.w"], torch.full((4, 4), 3.6)),
          "scale 2.0 extrapolates to 3.6")

    # weighted-sum: (0.6*1 + 0.4*3) / 1.0 = 1.8 as well, but from raw tensors
    ws = os.path.join(tmp, "weighted.safetensors")
    merge_anima.main([
        "--base", base_path, "--donor", a_path, "--donor", b_path,
        "--weights", "0.6,0.4", "--method", "weighted-sum", "--out", ws,
    ])
    check(close(load_file(ws)["net.blocks.0.w"], torch.full((4, 4), 1.8)),
          "weighted-sum value is 1.8")

    # Namespace mismatch: a full checkpoint spells the same weights
    # "model.diffusion_model.X" while a diffusion file spells them "net.X".
    ns_path = os.path.join(tmp, "donor-full.safetensors")
    save_file({"model.diffusion_model.blocks.0.w": torch.ones(4, 4, dtype=torch.bfloat16),
               "model.diffusion_model.blocks.1.w": torch.ones(2, dtype=torch.bfloat16),
               "cond_stage_model.encoder.w": torch.zeros(4, dtype=torch.bfloat16)},
              ns_path)
    ns_out = os.path.join(tmp, "namespaced.safetensors")
    rc = merge_anima.main([
        "--base", base_path, "--donor", ns_path, "--weights", "1.0", "--out", ns_out,
    ])
    ns = load_file(ns_out)
    check(rc == 0 and "net.blocks.0.w" in ns, "cross-namespace donor merges into base spelling")
    check(close(ns["net.blocks.0.w"], torch.ones(4, 4)), "cross-namespace values used")
    check("cond_stage_model.encoder.w" not in ns, "donor-only tensors are not copied")

    # A donor with a different block count is a different lineage: refuse it
    # rather than pair unrelated layers and render black.
    deep_path = os.path.join(tmp, "donor-extra-blocks.safetensors")
    save_file({"model.diffusion_model.blocks.0.w": torch.ones(4, 4, dtype=torch.bfloat16),
               "model.diffusion_model.blocks.1.w": torch.ones(2, dtype=torch.bfloat16),
               "model.diffusion_model.blocks.2.w": torch.ones(2, dtype=torch.bfloat16)},
              deep_path)
    try:
        code = merge_anima.main(["--base", base_path, "--donor", deep_path,
                                 "--out", os.path.join(tmp, "deep.safetensors")])
        check(code == 3, "different block count is refused")
    except SystemExit as exc:
        check("blocks" in str(exc), "different block count is refused")

    # Guard rails: no overwrite without --force, and mismatched weights are refused.
    try:
        merge_anima.main(["--base", base_path, "--donor", a_path, "--out", out_path])
        check(False, "refuses to overwrite")
    except SystemExit:
        check(True, "refuses to overwrite")
    try:
        merge_anima.main(["--base", base_path, "--donor", a_path, "--donor", b_path,
                          "--weights", "1.0", "--out", os.path.join(tmp, "x.safetensors")])
        check(False, "weight count must match donors")
    except SystemExit:
        check(True, "weight count must match donors")

    # --- four-donor recipe: the anchor must stay in the denominator ----------
    # Classic topology fallback anchors on donor #1. If that anchor is dropped
    # from the donor list and the weights renormalised over the rest, the blend
    # silently becomes 0/40.5/32.4/27.0 instead of 31.5/27.8/22.2/18.5.
    # Values: A=0 B=1 C=2 D=4, weights .85/.75/.60/.50 (sum 2.70).
    quad = {}
    for name, val in (("A", 0.0), ("B", 1.0), ("C", 2.0), ("D", 4.0)):
        quad[name] = os.path.join(tmp, f"quad-{name}.safetensors")
        save_file({"net.blocks.0.w": torch.full((2, 2), val, dtype=torch.float32)}, quad[name])
    qw = "0.85,0.75,0.60,0.50"
    qout = os.path.join(tmp, "quad-out.safetensors")
    rc = merge_anima.main(["--base", quad["A"],
                           "--donor", quad["A"], "--donor", quad["B"],
                           "--donor", quad["C"], "--donor", quad["D"],
                           "--weights", qw, "--scale", "1.0", "--out", qout])
    check(rc == 0, "four-donor merge with an anchor donor runs")
    got = load_file(qout)["net.blocks.0.w"].float()
    want = (0 * .85 + 1 * .75 + 2 * .60 + 4 * .50) / 2.70      # 1.46296
    dropped = (1 * .75 + 2 * .60 + 4 * .50) / 1.85             # 2.13243 = the bug
    check(close(got, torch.full((2, 2), want), atol=1e-4),
          f"anchor keeps its weight in the denominator (want {want:.5f})")
    check(not close(got, torch.full((2, 2), dropped), atol=1e-4),
          "anchor is not silently dropped from the recipe")

    # scale 0 must return the base untouched; scale 2 extrapolates.
    for sc, expect in (("0", 0.0), ("0.5", want / 2), ("2", want * 2)):
        o = os.path.join(tmp, f"quad-s{sc}.safetensors")
        merge_anima.main(["--base", quad["A"],
                          "--donor", quad["A"], "--donor", quad["B"],
                          "--donor", quad["C"], "--donor", quad["D"],
                          "--weights", qw, "--scale", sc, "--out", o])
        check(close(load_file(o)["net.blocks.0.w"].float(),
                    torch.full((2, 2), expect), atol=1e-4),
              f"scale {sc} follows the task-arithmetic formula")

    # Non-finite input must be refused, not written.
    try:
        merge_anima.main(["--base", quad["A"], "--donor", quad["B"],
                          "--weights", "nan", "--out", os.path.join(tmp, "nan.safetensors")])
        check(False, "NaN weight is refused")
    except SystemExit:
        check(True, "NaN weight is refused")
    try:
        merge_anima.main(["--base", quad["A"], "--donor", quad["B"], "--donor", quad["C"],
                          "--weights", "0,0", "--out", os.path.join(tmp, "zero.safetensors")])
        check(False, "zero total weight is refused")
    except SystemExit:
        check(True, "zero total weight is refused")


    # --- an input checkpoint is never a valid output ------------------------
    try:
        merge_anima.main(["--base", quad["A"], "--donor", quad["B"],
                          "--out", quad["A"], "--force"])
        check(False, "refuses to overwrite an input checkpoint (even with --force)")
    except SystemExit:
        check(True, "refuses to overwrite an input checkpoint (even with --force)")

    # --- integer / bool tensors are preserved, never blended ----------------
    ib = {"net.blocks.0.w": torch.zeros(2, 2, dtype=torch.bfloat16),
          "net.blocks.0.steps": torch.tensor([7, 9], dtype=torch.int64),
          "net.blocks.0.mask": torch.tensor([True, False])}
    ib_donor = {"net.blocks.0.w": torch.ones(2, 2, dtype=torch.bfloat16),
                "net.blocks.0.steps": torch.tensor([1, 1], dtype=torch.int64),
                "net.blocks.0.mask": torch.tensor([False, True])}
    ibp, ibdp, ibo = (os.path.join(tmp, f"ib{x}.safetensors") for x in ("-b", "-d", "-o"))
    save_file(ib, ibp, metadata={"modelspec.author": "someone", "modelspec.license": "NC"})
    save_file(ib_donor, ibdp)
    merge_anima.main(["--base", ibp, "--donor", ibdp, "--weights", "1.0", "--out", ibo])
    got = load_file(ibo)
    check(got["net.blocks.0.steps"].dtype == torch.int64, "int tensor keeps its dtype")
    check(torch.equal(got["net.blocks.0.steps"], ib["net.blocks.0.steps"]),
          "int tensor is copied from the base, not blended")
    check(got["net.blocks.0.mask"].dtype == torch.bool, "bool tensor keeps its dtype")
    check(torch.equal(got["net.blocks.0.mask"], ib["net.blocks.0.mask"]),
          "bool tensor is copied from the base")
    meta = read_meta(ibo)
    check("modelspec.author" not in meta and "modelspec.license" not in meta,
          "inherited author/license metadata is dropped")



    # --- --dtype must not touch tensors it is not allowed to convert ---------
    dp = os.path.join(tmp, "dtype-base.safetensors")
    ddp = os.path.join(tmp, "dtype-donor.safetensors")
    dout = os.path.join(tmp, "dtype-out.safetensors")
    dtype_base = {
        "net.blocks.0.w": torch.full((2, 2), 1.0, dtype=torch.bfloat16),
        "net.blocks.0.scale_weight": torch.tensor([0.25], dtype=torch.float32),
        "net.blocks.0.steps": torch.tensor([7], dtype=torch.int64),
        "net.blocks.0.mask": torch.tensor([True, False], dtype=torch.bool),
        "llm_adapter.w": torch.full((2, 2), 3.0, dtype=torch.bfloat16),
        "vae.dec.w": torch.full((2, 2), 0.5, dtype=torch.bfloat16),
    }
    dtype_donor = {
        "net.blocks.0.w": torch.full((2, 2), 5.0, dtype=torch.bfloat16),
        "net.blocks.0.scale_weight": torch.tensor([0.25], dtype=torch.float32),
        "net.blocks.0.steps": torch.tensor([7], dtype=torch.int64),
        "net.blocks.0.mask": torch.tensor([True, False], dtype=torch.bool),
        "llm_adapter.w": torch.full((2, 2), 4.0, dtype=torch.bfloat16),
    }
    save_file(dtype_base, dp)
    save_file(dtype_donor, ddp)
    merge_anima.main(["--base", dp, "--donor", ddp, "--weights", "1.0",
                      "--out", dout, "--dtype", "fp16"])
    got = load_file(dout)
    check(got["net.blocks.0.w"].dtype == torch.float16, "--dtype fp16 converts a merged tensor")
    check(got["net.blocks.0.scale_weight"].dtype == torch.float32,
          "--dtype fp16 leaves scale_weight at its original dtype")
    check(got["net.blocks.0.steps"].dtype == torch.int64,
          "--dtype fp16 does not turn an int64 counter into a float")
    check(int(got["net.blocks.0.steps"][0]) == 7, "int64 counter keeps its value")
    check(got["net.blocks.0.mask"].dtype == torch.bool,
          "--dtype fp16 does not turn a bool mask into a float")
    check(got["llm_adapter.w"].dtype == torch.bfloat16,
          "protected adapter keeps its dtype under conversion")
    check(got["vae.dec.w"].dtype == torch.bfloat16,
          "base-only component keeps its dtype under conversion")

    # --- an unsupported dtype is refused, not guessed ------------------------
    odd = os.path.join(tmp, "odd.safetensors")
    with open(odd, "wb") as f:
        # F4 is a real safetensors dtype but sub-byte: no byte-sized table can
        # describe it, so it must be refused rather than guessed.
        hdr = json.dumps({"t": {"dtype": "F4", "shape": [2], "data_offsets": [0, 1]}}).encode()
        hdr += b" " * ((-len(hdr)) % 8)
        f.write(struct.pack("<Q", len(hdr)) + hdr + bytes(1))
    before = os.listdir(tmp)
    try:
        merge_anima.main(["--base", odd, "--donor", odd, "--weights", "1.0",
                          "--out", os.path.join(tmp, "odd-out.safetensors")])
        check(False, "an unknown tensor dtype aborts instead of writing a bad file")
    except SystemExit:
        check(True, "an unknown tensor dtype aborts instead of writing a bad file")
    check("odd-out.safetensors" not in os.listdir(tmp),
          "a refused merge leaves no output file")

    # --- a failed verification must not destroy the previous destination -----
    keep = os.path.join(tmp, "existing.safetensors")
    save_file({"a": torch.zeros(1)}, keep)
    original = open(keep, "rb").read()
    real_verify = merge_anima.verify_checkpoint

    def boom(*a, **k):
        raise RuntimeError("simulated verification failure")

    merge_anima.verify_checkpoint = boom
    try:
        merge_anima.main(["--base", dp, "--donor", ddp, "--weights", "1.0",
                          "--out", keep, "--force"])
        check(False, "a failed verification raises")
    except RuntimeError:
        check(True, "a failed verification raises")
    finally:
        merge_anima.verify_checkpoint = real_verify
    check(open(keep, "rb").read() == original,
          "a failed verification leaves the previous checkpoint untouched")
    leftovers = [f for f in os.listdir(tmp) if ".part-" in f]
    check(not leftovers, "a failed write cleans up its staging file")

    print("MERGE MATH: " + ("PASS" if FAIL == 0 else f"{FAIL} FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
