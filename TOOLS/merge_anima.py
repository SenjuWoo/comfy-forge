#!/usr/bin/env python3
"""
Offline Anima checkpoint merger.

Why this exists: the HaDeS 4-way merge node only patches a model in memory while
ComfyUI is running, and its save node is bypassed by default. This tool writes a
real .safetensors checkpoint on disk so a merge can be shared, reloaded and
verified. It reproduces the node's math exactly (see MERGE MATH below), so a
merge made here matches the same settings in the graph.

Requires: torch + safetensors (both ship with ComfyUI's python_embeded).
"""
import argparse
import hashlib
import re
import json
import os
import struct
import sys
import time

import torch
from safetensors import safe_open

# ---------------------------------------------------------------------------
# MERGE MATH - copied from the HaDeS merger so results match the node.
#
#   task-arithmetic : out = base + scale * sum(w_i * (theta_i - base)) / sum(w_i)
#   weighted-sum    : out = sum(w_i * theta_i) / sum(w_i)
#
# The node's "density" / kept-fraction only affects TIES and DARE; Task
# Arithmetic ignores it, so this tool does not take a density value at all.
# Tensors whose name contains "llm_adapter" are kept from the base when
# protect-adapter is on, because Anima trains that adapter with lr=0.
# ---------------------------------------------------------------------------

DTYPE_NAMES = {
    torch.bfloat16: "BF16",
    torch.float16: "F16",
    torch.float32: "F32",
}
NAME_DTYPES = {v: k for k, v in DTYPE_NAMES.items()}
ITEM_SIZE = {"BF16": 2, "F16": 2, "F32": 4}


def read_header(path):
    """Return (tensors dict, metadata dict) from a safetensors file header."""
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        header = json.loads(f.read(n))
    meta = header.pop("__metadata__", {}) or {}
    return header, meta


def numel(shape):
    n = 1
    for d in shape:
        n *= d
    return n


def tensor_bytes(t):
    """Raw little-endian bytes of a contiguous CPU tensor, any float dtype."""
    return t.contiguous().view(torch.uint8).numpy().tobytes()


def open_lazy(path):
    """Memory-mapped handle so donors are never fully loaded."""
    return safe_open(path, framework="pt", device="cpu")


# Anima checkpoints in the wild use two namespaces for the same weights:
#   "net.blocks.0..."                      (modelspec/Comfy-style diffusion file)
#   "model.diffusion_model.blocks.0..."    (full checkpoint with encoder + VAE)
# Matching on the raw name finds zero overlap between the two, so keys are
# compared after dropping the wrapper prefix. Output always uses the BASE
# spelling, because the merged file must keep the base topology.
KEY_PREFIXES = ("model.diffusion_model.", "diffusion_model.", "net.")


def canon(key):
    for prefix in KEY_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def donor_index(header):
    """canonical name -> actual key in that donor file."""
    return {canon(k): k for k in header}


def block_indices(header):
    """Which transformer blocks the file contains, e.g. {0..39}."""
    found = set()
    for key in header:
        match = re.match(r"(?:^|\.)blocks\.(\d+)\.", canon(key))
        if match:
            found.add(int(match.group(1)))
    return found


def get(handle, key):
    return handle.get_tensor(key)


def merge_key(method, base, donors, weights, scale):
    """One tensor of the merge, accumulated in float32."""
    b = base.to(torch.float32)
    total = sum(weights)
    acc = None
    for donor, w in zip(donors, weights):
        t = donor.to(torch.float32)
        part = (t - b) * (w / total) if method == "task-arithmetic" else t * (w / total)
        acc = part if acc is None else acc + part
    out = b + scale * acc if method == "task-arithmetic" else scale * acc
    return out


def write_checkpoint(path, keys, shapes, dtypes, producer, metadata):
    """Stream a checkpoint to disk one tensor at a time (bounded RAM).

    Each tensor keeps its own dtype: Anima files mix BF16 weights with F32
    scale_weight tensors, and one global dtype would desync the header.
    """
    header = {"__metadata__": metadata}
    offset = 0
    for key in keys:
        nbytes = numel(shapes[key]) * ITEM_SIZE[dtypes[key]]
        header[key] = {"dtype": dtypes[key], "shape": list(shapes[key]),
                       "data_offsets": [offset, offset + nbytes]}
        offset += nbytes
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((-len(raw)) % 8)  # header must be 8-byte aligned

    tmp = path + ".part"
    digest = hashlib.sha256()
    with open(tmp, "wb") as f:
        f.write(struct.pack("<Q", len(raw)))
        f.write(raw)
        digest.update(raw)
        for key in keys:
            blob = producer(key)
            if blob is None:
                raise RuntimeError(f"no value produced for tensor '{key}'")
            f.write(blob)
            digest.update(blob)
    os.replace(tmp, path)
    return digest.hexdigest(), offset


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge Anima checkpoints into a real .safetensors file.")
    ap.add_argument("--base", required=True, help="ancestor checkpoint (Anima-2.9B-preview-v1)")
    ap.add_argument("--donor", action="append", required=True,
                    help="checkpoint to merge in; repeat once per model (up to 4)")
    ap.add_argument("--weights", default=None,
                    help="comma-separated per-donor weights, e.g. 0.85,0.75,0.60,0.50")
    ap.add_argument("--scale", type=float, default=1.0, help="merge strength (default 1.0)")
    ap.add_argument("--method", default="task-arithmetic",
                    choices=["task-arithmetic", "weighted-sum"])
    ap.add_argument("--out", required=True, help="output .safetensors path")
    ap.add_argument("--title", default=None, help="modelspec.title for the merged file")
    ap.add_argument("--dtype", default=None, choices=["bf16", "fp16", "fp32"],
                    help="output dtype (default: same as base)")
    ap.add_argument("--no-protect-adapter", action="store_true",
                    help="merge llm_adapter tensors instead of keeping the base copy")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would merge, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    ap.add_argument("--allow-topology-mismatch", action="store_true",
                    help="merge even when the donor has a different block count (usually garbage)")
    args = ap.parse_args(argv)

    if args.out and not args.force and os.path.exists(args.out):
        sys.exit(f"refusing to overwrite {args.out} (use --force)")
    if len(args.donor) > 4:
        sys.exit("at most 4 donors, matching the 4-way merge node")

    if args.weights:
        weights = [float(w) for w in args.weights.split(",")]
        if len(weights) != len(args.donor):
            sys.exit(f"--weights has {len(weights)} values but there are "
                     f"{len(args.donor)} donors")
    else:
        weights = [1.0] * len(args.donor)
    if sum(weights) <= 0:
        sys.exit("donor weights must add up to more than zero")

    started = time.time()
    base_header, base_meta = read_header(args.base)

    donors = [open_lazy(p) for p in args.donor]
    donor_headers = [read_header(p)[0] for p in args.donor]
    donor_keys = [donor_index(h) for h in donor_headers]
    base = open_lazy(args.base)

    keys, shapes, dtypes, merged, kept, skipped = [], {}, {}, 0, 0, 0
    for key, info in base_header.items():
        keys.append(key)
        shapes[key] = tuple(info["shape"])
        # Keep the base dtype unless the caller asked for a global conversion.
        dtypes[key] = {"bf16": "BF16", "fp16": "F16", "fp32": "F32",
                       None: info["dtype"]}[args.dtype]
        if key.endswith(".scale_weight") or info["dtype"] not in NAME_DTYPES:
            skipped += 1
            continue
        if args.no_protect_adapter is False and "llm_adapter" in key:
            kept += 1
            continue
        ck = canon(key)
        if any(ck not in dk for dk in donor_keys):
            skipped += 1
            continue
        for i, dk in enumerate(donor_keys):
            donor_shape = tuple(donor_headers[i][dk[ck]]["shape"])
            if donor_shape != tuple(info["shape"]):
                sys.exit(f"shape mismatch on '{key}': {info['shape']} vs {donor_shape} "
                         f"in {os.path.basename(args.donor[i])} - different Anima "
                         f"topologies cannot merge")
        merged += 1

    # Layer count is the real compatibility check. Two Anima checkpoints can
    # share tensor names and shapes yet hold a different number of blocks (the
    # 2.9B base is layer-expanded). Blending block N of a 40-block model with
    # block N of a 28-block model mixes unrelated weights and renders black.
    base_blocks = block_indices(base_header)
    for i, header in enumerate(donor_headers):
        donor_blocks = block_indices(header)
        if donor_blocks and base_blocks and donor_blocks != base_blocks:
            msg = (f"{os.path.basename(args.donor[i])} has {len(donor_blocks)} blocks, "
                   f"the base has {len(base_blocks)} - not the same Anima lineage, so "
                   f"the merge would pair unrelated layers")
            if not args.allow_topology_mismatch:
                print("error: " + msg, file=sys.stderr)
                print("       anchor on a checkpoint from the same lineage (e.g. --base with one "
                      "of the donors), or pass --allow-topology-mismatch to merge anyway",
                      file=sys.stderr)
                return 3  # distinct code: a caller can retry with a same-lineage anchor
            print("warning: " + msg)

    print(f"base      {os.path.basename(args.base)}  ({len(base_header)} tensors)")
    for path, w in zip(args.donor, weights):
        print(f"donor     {os.path.basename(path)}  weight {w:g}")
    print(f"method    {args.method}  scale {args.scale:g}  dtype {args.dtype or 'same as base'}")

    if merged == 0:
        sys.exit("nothing to merge: every tensor is protected or missing from a donor")

    def producer(key):
        ck = canon(key)
        keep = (key.endswith(".scale_weight")
                or base_header[key]["dtype"] not in NAME_DTYPES
                or (not args.no_protect_adapter and "llm_adapter" in key)
                or any(ck not in dk for dk in donor_keys))
        if keep:
            return tensor_bytes(get(base, key).to(NAME_DTYPES[dtypes[key]]))
        values = [get(d, dk[ck]) for d, dk in zip(donors, donor_keys)]
        value = merge_key(args.method, get(base, key), values, weights, args.scale)
        return tensor_bytes(value.to(NAME_DTYPES[dtypes[key]]))

    if args.dry_run:
        print(f"dry run    would merge {merged}, keep {kept} adapter, skip {skipped}")
        return 0

    # Identity: keep the base's topology facts, never its provenance.
    # `workflow` / `prompt` describe how the BASE was generated (they name the
    # base's own models), and `modelspec.hash_sha256` belongs to the base's
    # weights, so both would claim this file is the base. Dropped.
    DROP = ("workflow", "prompt", "__spacer", "modelspec.hash_sha256")
    meta = {k: v for k, v in base_meta.items() if k not in DROP}
    meta["modelspec.title"] = args.title or f"{os.path.splitext(os.path.basename(args.out))[0]}"
    meta["modelspec.description"] = (
        f"Task-arithmetic merge of {len(args.donor)} donor(s) over "
        f"{os.path.basename(args.base)} (weights {', '.join(f'{w:g}' for w in weights)}, "
        f"scale {args.scale:g}).")
    meta["modelspec.merge"] = json.dumps({
        "method": args.method,
        "scale": args.scale,
        "weights": weights,
        "donors": [os.path.basename(p) for p in args.donor],
        "base": os.path.basename(args.base),
        "adapter_protected": not args.no_protect_adapter,
    }, sort_keys=True)

    digest, total_bytes = write_checkpoint(args.out, keys, shapes, dtypes,
                                           producer, meta)
    check_header, _ = read_header(args.out)
    if len(check_header) != len(keys):
        sys.exit(f"write verification failed: {len(check_header)} tensors on disk, "
                 f"{len(keys)} expected")
    print(f"merged    {merged} tensors, kept {kept} llm_adapter, skipped {skipped}")
    print(f"wrote     {args.out}  ({total_bytes / 1e9:.2f} GB, {len(keys)} tensors)")
    print(f"sha256    {digest}")
    print(f"elapsed   {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
