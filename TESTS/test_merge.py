#!/usr/bin/env python3
"""
Merge math check. Builds three tiny checkpoints with known numbers, merges them
and asserts the result matches the formula the ComfyUI node uses. No GPU, no
real model, runs in about a second.

Run: python TESTS/test_merge.py
"""
import os
import struct
import sys

import torch
from safetensors.torch import load_file, save_file

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "TOOLS"))
import merge_anima  # noqa: E402

FAIL = 0


def close(t, want):
    """bf16 keeps ~3 significant digits, so compare within bf16 resolution."""
    return torch.allclose(t.float(), want, atol=0.02)


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
    save_file(base, base_path, metadata={"modelspec.title": "base", "modelspec.architecture": "anima"})
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
    meta = read_meta(out_path)
    check(meta.get("modelspec.title") == "test-merge", "title written")
    check(meta.get("modelspec.architecture") == "anima", "base metadata preserved")
    check("task-arithmetic" in meta.get("modelspec.merge", ""), "merge provenance recorded")

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

    print("MERGE MATH: " + ("PASS" if FAIL == 0 else f"{FAIL} FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
