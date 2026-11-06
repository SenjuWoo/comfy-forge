# WooMix (Anima) — model description

Paste this into the **model page description**. It keeps the structure of the
v1.5 page (philosophy → settings → negatives) and adds the donor breakdown,
because "what is this a merge of" is the first question anyone asks.

---

## About This Model

**The middle ground, and why it needed a second pass.**

Most anime checkpoints on Anima pick a side. They either bake in heavy contrast
so every render looks striking at thumbnail size and blows out once you actually
look at it, or they stay so neutral that everything comes out foggy and
underlit. WooMix landed in the middle on purpose: clean anime tones, a natural
dynamic range, detail you can crop into.

v1.5 proved that middle ground works. It also showed where it was thin — hands
and limbs in scenes with more than one character, and shadows that occasionally
crushed to black in dramatic lighting.

v1.75 fixes those with four donors instead of a rebuild, at a blend strength low
enough that nothing you liked changes character. If v1.5 is the model you
already use, v1.75 is the same model with fewer broken hands.

### What each donor contributes

| Donor | Weight | What it is there for |
|---|---|---|
| **Versatility** — Diving-Anima v9.0 | 0.70 | range of subjects and angles; stops every prompt collapsing to the same face |
| **Details** — Terra Rising Unity 3.0 Turbo | 0.90 | surface density: skin, fabric, hair, environment |
| **Composition** — hentai_studio_quality | 0.55 | framing and scene structure, most visible with multiple characters |
| **Consistency** — One obsession animeV5 turbo | 0.45 | same character across seeds |

Task-arithmetic merge over the v1.5 base at **strength 0.30**. Full checkpoint:
UNET + text encoder + VAE in one file, nothing extra to download.

## Recommended Generation Settings

| Setting | Value |
|---|---|
| CFG | 3.0 – 3.5 (3.2 is the sweet spot) |
| Steps | 30 – 40 |
| Sampler | ER-SDE, or DPM++ 2M SDE |
| Scheduler | Beta, Simple, KL Optimal or SGM Uniform |
| VAE | `qwen_image_vae` (bundled) |
| Resolution | 1024×1024 and up |

Optional: if you have the **RES4LYF** node pack, the `beta57` scheduler
(`beta_scheduler(0.5, 0.7)`) is the closest match to the HaDeS `anima_beta57`
setting and gives slightly softer low-frequency rolloff than plain `Beta`.

## Negative Prompt Strategies

Three tiers. Start with Standard; move up only if you are fighting artifacts,
because a long negative also flattens colour.

**Minimalist** — when the prompt is clean and you want maximum freedom:

```
worst quality, low quality, lowres
```

**Standard** — the default:

```
worst quality, low quality, lowres, score_1, score_2, score_3,
bad hands, missing fingers, extra fingers, monochrome, greyscale
```

**Heavy Clean** — multi-character scenes and hands:

```
worst quality, low quality, lowres, score_1, score_2, score_3, score_4,
bad anatomy, bad hands, missing fingers, extra fingers, fused fingers,
extra limbs, missing limbs, deformed, disfigured, watermark, signature,
text, jpeg artifacts, monochrome, greyscale, 2koma, 4koma
```

## Credits & License

Base: **Anima** by CircleStone Labs. v1.5 base and this merge by the model
author. Donors: Diving-Anima, Terra Rising: Unity (AntonioArts26),
hentai_studio_quality, One obsession — credit them if you repost.

Released under the **CircleStone Labs Non-Commercial License v1.2**, inherited
from the Anima base. No commercial use. Do not resell or put behind a paywall.
