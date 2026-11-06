# Merging Anima checkpoints

A merge takes several fine-tunes of the same ancestor and writes one new
checkpoint that behaves like a blend of them. It is useful when no single
checkpoint has every quality you want: one is versatile, one has detail, one has
composition, one has consistency.

This repo does it as **task arithmetic**, the same math as the
`HaDeSAnimaMultiModelMerger` node in `workflows/anima-4way-merge.json`:

```
out = base + scale * Σ wᵢ (θᵢ − base) / Σ wᵢ
```

In words: for every weight, measure how far each donor moved away from the
shared ancestor, average those movements with your weights, and apply the
average to the ancestor. `scale` is the strength of that movement.

## The four parts

| Part | What it is | Your value |
|---|---|---|
| **base** | the ancestor every donor was fine-tuned from | `Anima-2.9B-preview-v1.safetensors` |
| **donors** | the fine-tunes to blend | one `--donor` per file, up to 4 |
| **weights** | how much each donor's *change* counts | `0.85, 0.75, 0.60, 0.50` |
| **scale** | overall strength of the blend | `1.0` = plain average |

Weights are relative: `0.85,0.75,0.60,0.50` behaves exactly like
`1.0,0.88,0.71,0.59`. Only their ratio matters. `scale 2.0` doubles every
donor's movement away from the base, which pushes differences harder and can
overcook the model; `scale 0.5` is a gentler blend.

Your commented donor set, by the quality each one was picked for. Note that the
first one becomes the **anchor** for this set — see the lineage rule below:

| Slot | File | Weight |
|---|---|---|
| A versatility | `Versetility - Diving-Anima_v9.0.safetensors` | 0.85 |
| B detail | `Details - Terra Rising Unity 3.0 - Turbo.safetensors` | 0.75 |
| C composition | `Composition - hentai_studio_quality-000008.safetensors` | 0.60 |
| D consistency | `Consistensy - One obsession_animaV5turbo.safetensors` | 0.50 |

## Run it

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 merge `
  -Donors "S:\models\Versetility - Diving-Anima_v9.0.safetensors",
          "S:\models\Details - Terra Rising Unity 3.0 - Turbo.safetensors",
          "S:\models\Composition - hentai_studio_quality-000008.safetensors",
          "S:\models\Consistensy - One obsession_animaV5turbo.safetensors" `
  -Weights 0.85,0.75,0.60,0.50 -Scale 1.0
```

Without `-Out`, the file is written to
`<SwarmUI>\Models\Stable-Diffusion\Anima\Anima-4way-<timestamp>.safetensors` and
SwarmUI's model list is refreshed, so it is selectable right away.

Under the hood it calls `TOOLS\merge_anima.py`, which needs `torch`,
`safetensors` and `numpy`. All three ship with the Python that ComfyUI installs,
so the wrapper finds that interpreter for you. Nothing is installed globally.

## Compatibility rules

A merge only makes sense between checkpoints from the same lineage. The tool
checks this and refuses rather than writing a broken file:

- **Same architecture.** Anima merges only with Anima.
- **Same block count.** This is the one that bites. `Anima-2.9B-preview-v1` has
  **40 blocks**; the four fine-tunes above have **28**. Pairing block 12 of a
  40-block model with block 12 of a 28-block model blends unrelated layers: the
  file loads, the sampler runs, and the PNG is pure black with no error. Verified
  both ways on real files.

  `merge_anima.py` counts the blocks and stops. With `-AcceptFallbackAnchor` the
  CLI anchors on the first donor and keeps it in the donor list with its weight
  (zero task vector), which is the merge those files can actually support:

  ```
  base      Versetility - Diving-Anima_v9.0.safetensors  (685 tensors)
  donor     Details - Terra Rising Unity 3.0 - Turbo   weight 0.75
  donor     Composition - hentai_studio_quality        weight 0.60
  donor     Consistensy - One obsession                weight 0.50
  merged    567 tensors, kept 118 llm_adapter, skipped 0
  ```

  An explicit `-Base` is never overridden: if you name a base, that is what gets
  used, and a lineage mismatch is an error. `--allow-topology-mismatch` forces a
  cross-lineage merge for experiments.
- **Same shape per tensor.** A mismatch stops the merge with the tensor name.
- **Key spelling may differ.** A full checkpoint spells weights
  `model.diffusion_model.blocks.0…` while a diffusion-only file spells them
  `net.blocks.0…`. The tool normalises both and always writes the base spelling.
- **Encoder and VAE are never merged.** They come from the base, like the node.
- **`llm_adapter` weights are kept from the base.** Anima trains that adapter
  with lr=0, so blending it is not wanted. `--no-protect-adapter` disables this.

## How to know the merge worked

A merge that writes a file can still be useless. Two checks, in order:

1. **Self-merge sanity.** Merging the base with itself at weight 1.0 must
   produce a bit-identical file. If it does, the merge writer is correct.
2. **Render it.** Generate one image with the merged checkpoint before you build
   a workflow around it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 generate `
  -Model "Anima/Anima-4way-20261105-120000.safetensors" `
  -Prompt "a rain-soaked neon alley at night"
```

A completely black PNG means the weights are incompatible, not that the sampler
is broken. Check the block count first.

## Method reference

| `--method` | Formula | Use |
|---|---|---|
| `task-arithmetic` (default) | `base + scale · Σ wᵢ(θᵢ − base) / Σ wᵢ` | blend fine-tune differences; what the graph uses |
| `weighted-sum` | `Σ wᵢθᵢ / Σ wᵢ` | plain average of the whole weights, no base needed |

`density` / kept-fraction from the node is not exposed here: it only affects TIES
and DARE, and Task Arithmetic ignores it.

## Why the command line instead of the node

The ComfyUI node patches a model **in memory while ComfyUI runs** and its save
node ships bypassed. Nothing lands on disk, so you cannot share, compare or keep
the result. The CLI writes a real `.safetensors` with the merge settings recorded
in its metadata (`modelspec.merge`), which makes a merge reproducible later:

```powershell
python TOOLS\merge_anima.py --base <file> --donor <file> --weights 0.5 --out <file> --dry-run
```

`--dry-run` reports how many tensors would merge, how many are protected, and
how many the donor does not have, without writing anything.

## The anchor rule (read this before blaming a weak merge)

Task arithmetic is `M = B + scale * sum(w_i * (D_i - B)) / sum(w_i)`.

At `scale = 1` the base cancels algebraically and the result is
`sum(w_i * D_i) / sum(w_i)` — a pure weighted average of the donors. That is why
the denominator must include **every** donor you asked for, including one that
also happens to serve as the base.

When the requested base is a different lineage (40-block Anima-2.9B against
28-block fine-tunes), there is no common ancestor to anchor on. The CLI can
anchor on one donor instead, but it keeps that donor **in the donor list with
its weight**: its task vector is zero, its weight still counts.

| Recipe | A | B | C | D |
|---|---|---|---|---|
| weights as requested | 0.85 | 0.75 | 0.60 | 0.50 |
| share at scale 1, anchor kept | 31.48% | 27.78% | 22.22% | 18.52% |
| share if the anchor is dropped | 0% | 40.54% | 32.43% | 27.03% |

The second-row behaviour is what this tool does. The third row is what an
earlier version did, and it silently rewrote the recipe — so the fallback is now
opt-in (`-AcceptFallbackAnchor`) and the merge refuses to run without it.

`scale` is a strength, not a second weight: `0` returns the base untouched,
`1` is the full blend, `2` extrapolates past it.
