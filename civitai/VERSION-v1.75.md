# WooMix (Anima) — v1.75 version notes

> **Do not publish yet.** The four donors could not be verified as merge-permitted
> (see `PERMISSIONS.md`). Keep the file private until each donor's
> `allowDerivatives` is confirmed.

Paste this into **About this version** on the version-edit page.

---

v1.75 is a refinement pass, not a rebuild. v1.5 is still the base and still the
look — this version nudges it with four hand-picked donors instead of replacing
its identity.

**What the four donors do**

| Donor | Weight | Role |
|---|---|---|
| Versatility — Diving-Anima v9.0 | 0.70 | range: keeps unusual subjects and camera angles from collapsing into the same face |
| Details — Terra Rising Unity 3.0 Turbo | 0.90 | surface: skin, fabric, hair strands, environmental texture |
| Composition — hentai_studio_quality | 0.55 | framing and scene structure, especially multi-character |
| Consistency — One obsession animeV5 turbo | 0.45 | keeps a character recognisable across seeds |

The blend is task arithmetic at **strength 0.30**, so most of what you already
like about v1.5 survives. That number is deliberate: at full strength the donors
flatten v1.5's contrast. At 0.30 you get their detail without losing its punch.

**What actually changed, measured**

Across 8 fixed prompts and seeds, against v1.5:

- hands and limbs in two-character scenes resolve more often (the main failure of v1.5)
- shadows stay open instead of crushing to black
- measured sharpness holds near v1.5 while detail density goes up

It is an incremental improvement and it is meant to be. If you want a bigger
departure, that is a different model, not a version bump.

**Same settings as v1.5.** Nothing you already use needs to change. `beta57`
becomes selectable if you have RES4LYF installed; it is not required.

**Credits.** v1.5 base by the same author. Donors: Diving-Anima, Terra Rising:
Unity (AntonioArts26), hentai_studio_quality, One obsession. License follows the
Anima base: **CircleStone Labs Non-Commercial License v1.2** — no commercial use.
