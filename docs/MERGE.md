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

Your commented donor set, by the quality each one was picked for:

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
- **Same block count.** The 2.9B base has 40 blocks. A 28-block checkpoint is a
  different lineage; pairing block 12 of one with block 12 of the other blends
  unrelated layers, and the result renders **black**. `merge_anima.py` stops
  with a message naming both counts. `--allow-topology-mismatch` forces it
  through anyway, for experiments.
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
