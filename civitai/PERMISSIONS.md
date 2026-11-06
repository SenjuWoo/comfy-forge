# Merge permissions — WooMix (Anima) v1.75

Status: **NOT CLEARED FOR PUBLIC RELEASE.** Keep the file local/private until the
four donors below are identified and their `allowDerivatives` is confirmed.

## Verified

| Model | Civitai | Derivatives | Commercial | Different licence | Credit |
|---|---|---|---|---|---|
| **WooMix (Anima) v1.5** (base) | 2723359 | **Yes** | Image, RentCivit, Rent, Sell, SellMerge | Yes | not required |
| **Anima-2.9B** (root) | 2855007 | **Yes** | Image, RentCivit, Rent, Sell, SellMerge | **No** | not required |

Local `woomixAnima_v15.safetensors` hash-matches Civitai `8223B9152A6B…`, so that
identity is certain.

`allowDifferentLicense: No` on Anima is the binding constraint: every derivative —
including WooMix and this v1.75 — must carry the same licence. Anima's own page
says *"Model weights are released under the CircleStone Labs Non-Commercial
License, falling under derivative model category."* So: **non-commercial, no
paywalling, licence inherited, no relicensing.** WooMix's own permissive flags
cannot relax that.

## Not verified — this is what blocks publication

None of the four donors match any Civitai file by hash, and each carries a
`modelspec.hash_sha256` that differs from its own file's hash:

| Donor (your label) | Declared hash | Actual file hash |
|---|---|---|
| Versetility — Diving-Anima v9.0 | `87e63efc…` | `CBD74390…` |
| Details — Terra Rising Unity 3.0 Turbo | `cedc3758…` | `C27E0AE6…` |
| Composition — hentai_studio_quality | `2057f767…` | `C41F29FE…` |
| Consistensy — One obsession animeV5 turbo | `749cb2e6…` | `90842940…` |

All four were re-saved at some point, so hash identity is gone. The only link
left is the name, and those names are your own prefixes — not the originals.
Permission cannot be established from the files themselves.

Closest named match found: **One obsession_Anima** (Civitai 2695493) is
`allowDerivatives: No`, `allowNoCredit: No`. If that is the donor, merging it is
not permitted at all, not merely publishing.

## What to check before publishing

For each of the four original pages:

1. `allowDerivatives` must be **Yes** — this is the merge permission.
2. `allowNoCredit` — if No, the donor must be credited by name.
3. `allowCommercialUse` — commercial already ends at "non-commercial" from Anima,
   so this only matters for whether the donor forbids it outright.
4. `allowDifferentLicense` — must be Yes if you intend any licence other than the
   donor's own.

If all four pass: publish **non-commercial**, **CircleStone Labs Non-Commercial
License v1.2**, with all donors credited.

If any fails: publish nothing. A private/local merge is fine — distributing it is
not.
