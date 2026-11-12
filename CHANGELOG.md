# Changelog

## v0.6.2 - Locked 3D generation settings; game-ready handoff

The Pixal3D and Idol widgets stay on the stock values that produced the measured
meshes (Pixal3D single-image axe: 699,878 tris, 4096 albedo). Those values are
recorded in `presets/3d/generation.json` and checked by `TESTS/test_3d_generation.py`.
Sampler, CFG, flow shift and the 700,000-triangle generation cap are not the
size control.

`TOOLS/game-ready.ps1` sends a finished `.glb` to Asset Forge `mesh-gameready`.
Asset Forge runs Blender. This repo does not decimate or resize textures itself.
A `.spz` is left as a preview.

The default budget is `game`: 20,000 triangles and a 1024 texture. A result over
that cap fails. `hero` is 30,000 and is not a Roblox mesh. `accessory` is 4,000.
A 700,000-triangle sculpt does not pass. Rules: `docs/GAME-READY.md`.

One measured prop (699,878 triangles, 61.5 MB, three maps at 4096 and 2048) went through the default command and came out at 17,000 triangles, 21,186 vertices, 6.2 MB, and three 1024 maps. Structural checks passed. The official glTF validator reported 0 errors and 0 warnings. Engine import was not run.

## v0.6.1 - 3D templates verified end-to-end; AssetForge decision guide

The three 3D templates shipped in v0.6.0 were run end-to-end on this machine
(2026-11-12, RTX 4080 SUPER, SwarmUI driving ComfyUI 0.44.0): all pass.

- `presets/3d/3d_idol_image_to_gaussian_splat.json` — fur bear fixture:
  `.spz` 3.1 MB + `.glb` 20.4 MB (847,804 tris), no embedded textures, orbit MP4 written. Runtime ~3.5 min.
- `presets/3d/3d_pixal3d_trellis2_image_to_model.json` — viking_wolf_rune_axe.png:
  `.glb` 61.5 MB, 699,878 tris, double-sided, occlusion + normal material, 3 embedded PBR PNGs
  (4096x4096 base color + normal, 2048x2048 ORM/AO). Runtime ~4 min.
- `presets/3d/3d_pixal3d_multi_views.json` — 4-view mech turnaround sheet:
  `.glb` 699,770 tris, same PBR material structure. Runtime ~4.5 min.
- Templates kept at stock (upstream RescaleCFG 0.5/0.7, CFGOverride 1/0.769/0.667, flow shift 5, euler + SD3 sampler).
  All three run `fixed` seeds.
- `docs/3D-MODELS.md` rewritten: verified runtime/outputs/face counts; measured, not estimates.
- NEW `docs/PRODUCTION-WORKFLOWS.md`: which tool for which job. AssetForge is the deterministic,
  QA-gated production lane (2D sprites/atlases, procedural props with baked exports, glb optimize/retopo,
  roblox/godot budgets); comfy-forge is the generative AI lane (Anima renders, dataset captions, image->3D).
  Pattern: concept image -> comfy-forge 3D template (sub-asset) -> AssetForge retopo/LOD/export.
- `docs/ASSETFORGE.md` kept honest on AssetForge: its delegation through `comfy-generate` is UNVERIFIED
  (AssetForge has never run on this machine) even though SwarmUI is running now.
- README 3D section updated to verified; layout list updated.

No behaviour changes to code.

## v0.6.0 - comfy-kit is now comfy-forge; 3D image-to-model support

Two things in one release:

1. **Renamed: comfy-kit -> comfy-forge** (repository `SenjuWoo/comfy-kit` -> `SenjuWoo/comfy-forge`; GitHub redirects the old URL). On disk this checkout moves to `..\comfy-forge`. The `%LOCALAPPDATA%\comfy-kit\` state folder KEEPS its old name on purpose: the ~1.8 GB WD14 tagger weights and the install-state record live there, and renaming would force re-downloads. `COMFY_KIT_ROOT` likewise keeps its name - AssetForge reads it, and the env var is the compatibility contract.
   - banner, README badges/URLs, docs, CLI help/comments, test identifiers and the default `client_id`/`User-Agent` strings now say comfy-forge.
   - git history keeps the old name; CHANGELOG history is accurate to its period and was not rewritten.

2. **New: 3D image-to-model support.** `presets/3d/` ships three ComfyUI templates (copies of the MIT workflow templates from Comfy-Org, with provenance in `presets/3d/NOTICE.md`), and `docs/3D-MODELS.md` compares them for real workflows:
   - Idol (fine-tuned TripoSplat, Apache-2.0) - single image -> Gaussian splat; fastest, cheapest, previews/AR. Not a PBR mesh.
   - Pixal3D single image (MIT) - one concept image -> textured `.glb` with full PBR maps + UVs. The recommendation for game props.
   - Pixal3D multi-view (MIT) - a turnaround sheet -> consistent textured mesh. The recommendation for characters built from turnaround sheets (the Character Turnaround Sheet Generator template is the intended input).
   Recommended pick overall: **Pixal3D**. Idol loses on shipping-quality axes (no unwrapped PBR mesh; splat-only native output).
   - `TOOLS/Install-3DModels.ps1` downloads the weights opt-in (`-Set idol|pixal3d|trellis2|all`, `-WhatIf` lists first, resumable `curl -C -`). No 3D weights are committed.
   - `README.md` gains a 3D section with the same decision table, and `docs/ASSETFORGE.md` keeps the AssetForge pairing accurate across the rename.
   - IMPORTANT, HONEST STATUS: neither model has been run on this machine - SwarmUI/ComfyUI is not running. Templates, nodes and file lists are verified against the upstream notes; renders are UNVERIFIED.

No code paths changed besides branding. Tests and preflight are unchanged.

## v0.5.17 - AssetForge, the documented companion

AssetForge (https://github.com/SenjuWoo/AssetForge) is a separate toolkit for producing,
validating and exporting game assets. This release documents how the two work together and
recommends the pairing - no code in comfy-kit changed beyond the README pointer.

- New docs/ASSETFORGE.md: what AssetForge delegates to this repo (the
  comfy-generate recipe runs image-stack.ps1 generate), how to make comfy-kit ready first
  (install, then launch-windows.bat, then status), how to point AssetForge at this folder
  (COMFY_KIT_ROOT, project integrations.comfy_kit, markers), a typical asset session, and
  what AssetForge will and won't do.
- README gains an AssetForge section and the layout list names DATASET.md/ASSETFORGE.md.
- Verification table is honest: AssetForge delegating a render is UNVERIFIED on this
  machine (SwarmUI is not running); the markers it needs are present; the CLI verbs
  this repo ships are exercised by the existing test suite.

## v0.5.16 - a corrupt image never reaches the tagger

Regression introduced by 0.5.15, reproduced on the released build first.

Moving validation to the end of `build_entries()` meant an unreadable image could be handed
to the tagger first. WD14's preprocessing opens the file with PIL, so a folder holding one
good uncaptioned PNG and one corrupt PNG died with an uncaught
`UnidentifiedImageError` instead of reporting the bad file. Reproduced with a tagger that
genuinely decodes its input.

Sources are now decoded once, up front, before any resume, conflict or inference work. An
unreadable image is recorded as `unreadable` with the decoder's reason, skipped, and the run
exits non-zero; the other images are processed normally and the corrupt one never enters the
archive. The caption check after the build stays where it was, so a manually resolved conflict
is still validated - images are simply no longer decoded twice.

### Verified

```
probe_v0516.py on 0.5.15  UnidentifiedImageError, bad_marked_unreadable false   -> FAIL
probe_v0516.py on 0.5.16  no crash, exit 2, bad unreadable, good exported       -> PASS
test_dataset_prep.py      83 assertions                                        -> PASS
smoke_mixed + probe_v0510/0512/0514/0515  still PASS                           -> PASS
```
## v0.5.15 - a resolved conflict is still validated

One hole from the 0.5.14 audit, reproduced on the released build first.

### Resolved conflicts skipped validation

The `resolved` branch accepted a caption and marked the entry `ok` without checking that the
image still decodes or that the caption has content. A conflict could therefore be "resolved"
into an invalid pair:

- empty the kept caption, resume: exit 0, `status: ok`, empty caption inside a verified zip;
- corrupt the source image, edit the caption to resolve, resume: exit 0, unreadable bytes
  inside a verified zip.

Both reproduced. The zip hash proves the bytes match the source, not that the source is an
image - so the archive check could not catch either case.

### One validation point instead of a branch

The per-branch readability check is gone. After every entry is built, each one claiming
success is checked once: non-empty caption, then a real decode of its source image. Failures
become `empty-caption` or `unreadable`, are excluded from the archive and make the run exit
non-zero. Manually written captions are never rewritten - a rejected image leaves your caption
exactly where it was.

### Verified

```
probe_v0515.py on 0.5.14  empty_resolved_rejected false, corrupt_resolved_rejected false,
                          both not_archived false                          -> FAIL
probe_v0515.py on 0.5.15  all seven true                                   -> PASS
test_dataset_prep.py      78 assertions                                    -> PASS
smoke_mixed.py            PASS
probe_v0510 / probe_resume / probe_v0512 / probe_v0514  still PASS         -> PASS
merge, workflow, powershell suites  PASS
```
## v0.5.14 - a conflict stays a conflict until you say otherwise

Two resume holes from the 0.5.13 audit, each reproduced on the released build first.

### A second resume destroyed the caption a conflict had preserved

Export `red_hair`, edit the exported caption to `custom hand caption`, change the image,
resume: exit 2, status `conflict`, caption kept - correct. Resume again without touching
anything: exit 0, the caption was overwritten with `red hair` and the entry became `ok`.
Reproduced.

The conflict was recorded, but the next run treated `status != ok` as "recompute from source",
which is right for a failure and wrong for your edit. Conflicts are now their own state: while
the output caption still says what was preserved, every resume reports the same conflict and
writes nothing. Resolving it is deliberate - edit that caption, or rebuild with `--force`.

### Nested output folders escaped the manifest check

The "no manifest, no resume" guard listed `.txt` files in the output root only. A dataset with
`nested/a.png` and `nested/a.txt` slipped past it: delete the manifest, edit the nested
caption, change the source, resume - exit 0 and the edit was overwritten. Reproduced.

The check now walks the whole output tree, so any existing caption without a trustworthy
manifest refuses the run before a single byte is written. A corrupt manifest behaves the same
way. `--force` remains the deliberate rebuild.

### Verified

```
probe_v0514.py on 0.5.13  conflict_survives_second false, third false,
                          nested_no_manifest_refused false,
                          nested_output_untouched false                     -> FAIL
probe_v0514.py on 0.5.14  all eight true                                    -> PASS
test_dataset_prep.py      71 assertions                                     -> PASS
smoke_mixed.py            PASS
probe_v0510 / probe_resume / probe_v0512  earlier blockers still fixed      -> PASS
```
## v0.5.13 - resume trusts evidence, not assumptions

Three resume failures from the 0.5.12 audit, each reproduced on the released build first.

### A source caption edit was ignored

Resume only compared images. Edit `red_hair` to `green_hair` in the grabber `.txt`, leave the
picture alone, rerun with `--resume`: exit 0, exported caption still `red hair`. Reproduced.

The manifest now records a hash of every source caption beside the image hash. A changed source
caption regenerates the caption, unless you edited it by hand - then it is a `conflict`, kept,
excluded and reported.

### A deleted manifest made resume unsafe

Delete `dataset-manifest.json`, change the image, update its `.txt`, resume: the old caption
survived beside the new image and the run claimed success. Reproduced.

Resume without a readable manifest is now refused outright, with the reason and the way out
(`--force` to rebuild from source, or a new `--out`). There is no honest guess to make here.

### A changed uncaptioned image failed, then "succeeded" stale

The tagger decision was made before entries could be reclassified as needing inference, so a
changed uncaptioned image hit `caption needs regenerating but no tagger is loaded`. Worse, that
failed run rewrote the manifest with the new source hash, and the next resume reported success
while serving the old caption. Both reproduced.

Kinds are now resolved before the tagger decision, so nothing needs inference after the fact.
Entries are only resumable when the previous run recorded them as `ok`; a failed entry stays
invalid and is rebuilt on the next attempt instead of being blessed by the manifest it helped
write.

### Settings are part of the history

`--type`, `--trigger`, `--concept`, thresholds, `--max-tags`, `--exclude`, `--drop-artists` and
the tagger are recorded. Resume refuses to mix captions built under different settings, names
the ones that differ, and asks for `--force`.

### Verified

```
probe_v0512.py on 0.5.12  caption_edit false, no_manifest false, tagger_reload false,
                          second_resume_repairs false, settings false   -> FAIL
probe_v0512.py on 0.5.13  all true                                      -> PASS
test_dataset_prep.py      62 assertions                                 -> PASS
smoke_mixed.py            18 assertions                                 -> PASS
probe_v0510.py / probe_resume.py  earlier blockers still fixed          -> PASS
```
## v0.5.12 - resume stops lying about what changed

Two resume failures from the 0.5.11 audit, each reproduced on the released build first.

### Resume paired a changed image with a stale caption

Export `red_hair`, replace the picture with a green one and its source caption with
`green_hair`, rerun with `--resume`: the output held the new green image and the old `red
hair` caption, and the command returned success. Reproduced on 0.5.11.

Resume now compares the current source hash against the hash recorded in the manifest, so it
can tell "already done" from "the input changed":

- image changed, caption untouched - the caption is regenerated from the new source;
- image changed, caption hand-edited - the caption is kept, the entry is marked `conflict`,
  excluded from the archive, and the run exits non-zero;
- caption emptied - marked `empty-caption`, excluded from the archive, non-zero exit.

Nothing is silently discarded and nothing is silently trusted.

### Resume accepted an empty caption

A whitespace-only `.txt` in the output folder was exported as an empty caption, archived, and
reported as success. Reproduced. An empty resumed caption is now `empty-caption`, kept out of
the archive, with a non-zero exit, and the archive is not advertised as verified when it holds
no usable pairs.

### Cleanup

- `--link` is gone. A hard link shares bytes with the source, so editing the output would edit
  the original - that breaks the one promise this tool makes. Copying is the only option now.
- `docs/DATASET.md` no longer claims style mode drops the trigger; it has kept it since 0.5.11.
- The doc now names the three different mechanisms: deterministic tag conversion, WD14 vision
  tagging limited to booru vocabulary, and lexical concept matching that invents nothing.

### Verified

```
probe_resume.py on 0.5.11  changed_image_does_not_keep_stale_caption false,
                           empty_resumed_caption_rejected false,
                           edited_caption_preserved_and_flagged false      -> FAIL
probe_resume.py on 0.5.12  true, true, true                                -> PASS
test_dataset_prep.py       55 assertions                                   -> PASS
smoke_mixed.py             18 assertions                                   -> PASS
probe_v0510.py             the three earlier blockers still fixed          -> PASS
full suite                 merge, workflow, powershell, preflight 16/16    -> PASS
```
## v0.5.11 - dataset prep that tells the truth about what it did

Three blockers from 0.5.10, each reproduced on the released build before the fix, plus the
concept handling that was documented but not implemented.

### Auto mode demanded ONNX Runtime for a folder it did not need to tag

`build_entries()` constructed the tagger before checking whether any image lacked a caption,
so a grabber export with complete captions died with `ModuleNotFoundError: onnxruntime`.
Reproduced with `onnxruntime` blocked: `auto` exited with an ImportError while holding every
caption it needed.

The tool now plans first and loads the tagger only if at least one image actually needs
inference. A fully captioned folder runs with no ONNX Runtime, no weights and no GPU.

### A corrupt image could be exported and called verified

Image validation only ran on the inference path. `--mode convert` with a broken PNG and a
valid `.txt` reported success, and the zip verifier checked filenames and captions, not bytes.

Every image is now validated before export in every mode. Corruption is reported with the
decoder's reason, the entry is excluded from the archive, and the run exits non-zero. The
verifier hashes the archived image bytes against the source, so a file with the right name
and the wrong contents fails.

### --force left stale images beside fresh captions

`write_dataset()` skipped any destination that already existed. Change a picture, rerun with
`--force`, and the output kept the old image with the new caption - the worst possible pairing
for training.

Destinations are now compared by hash and refreshed when the source differs, with source
hashes recorded in the manifest. Captions and images are written through a temporary file and
an atomic rename, so an interrupted run cannot leave half a pair. `--resume` keeps captions
you edited and still revalidates and reports every entry.

### The concept sentence now changes the captions

The concept was recorded and then mostly ignored. Two real changes, both documented:

- Tags matching a word from the concept move to the front of the caption, after the trigger.
  Say "painterly fantasy" and a `painterly` tag leads.
- Style filtering uses tag categories read straight from the tagger's CSV - no ORT, no model
  load - so `convert` mode can drop a `hatsune miku` tag too. With no CSV present nothing is
  filtered; the tags are kept and the review file says so.

The trigger now applies to **every** concept type. Previously `--type style` silently dropped
a trigger the user had typed; a style token is exactly what a style LoRA needs.

### Verified

```
probe_v0510.py on 0.5.10  auto_without_onnxruntime false, corrupt_image_rejected false,
                          force_refreshes_image false        -> FAIL
probe_v0510.py on 0.5.11  true, true, true                    -> PASS
test_dataset_prep.py      24 assertions                       -> PASS
smoke_mixed.py            18 assertions, mixed grabber folder -> PASS
```

`smoke_mixed.py` runs a folder holding existing captions, a missing caption, a corrupt image, an
edited image, an edited caption, plus style and character concepts through `--zip`, `--force` and
`--resume`, and hashes every source before and after to prove nothing was written to it.
## v0.5.10 - dataset prep that cannot damage a grabber folder

Three bugs in 0.5.9, each reproduced on the released build before the fix, plus the
grabber workflow the tool needed to have in the first place.

### The source captions were overwritten

Default behaviour wrote `.txt` next to the images, so pointing it at an imgbrd-grabber
folder replaced the tags the grabber had collected. Reproduced: source caption hash
changed after a plain run.

Every run now writes to a separate output folder - `<images>/anima-dataset` by default,
`--out` to choose - and refuses to write into a non-empty one without `--force`. Sources
are never opened for writing.

### The archive caption disagreed with the output caption

`build_zip()` read captions from the image folder while `--out` had written them
elsewhere, so a converted caption could pair with a different caption in the zip.
Reproduced: archive body was `1girl, solo, long_hair...` while the output folder held the
converted text.

One entry list now drives the output folder, the review file, the manifest and the zip,
so the caption in the archive is the caption that was written. `--zip` reopens the archive
and fails the run if any pair is missing or differs.

### Resume lost images from the manifest

`--resume` skipped images that already had captions and dropped them from the manifest,
so a resumed run could report zero images while the archive held all of them. Resume now
records those entries as `resumed`, and the manifest counts every image it saw.

### Grabber input, three modes

- `auto` (default): convert an existing `.txt`, tag only the images without one.
- `convert`: existing captions only. No tagger, no ONNX Runtime, no weights.
- `retag`: tagger output merged into your tags as a baseline; additions are reported,
  nothing is replaced silently.

`--type character|style|outfit|object` changes the caption policy: style drops the trigger
and character/copyright tags so a fictional identity is not forced onto every image, while
character/outfit/object keep the trigger. The concept sentence is no longer pasted into
every caption unless `--append-concept` is passed.

Also handled: `score_7` keeps its underscore, duplicate tags collapse, empty or malformed
captions are reported, `same.png`/`same.jpg` get separate caption pairs, unreadable images
are excluded with the reason, EXIF rotation and alpha are applied before tagging, and a
partial run exits non-zero instead of reporting success. `_review.tsv` lists every caption
for inspection before training.

### Verified

```
probe_p0.py on 0.5.9  FAIL  (source_caption_preserved false, zip mismatch, manifest 1)
probe_p0.py on 0.5.10 PASS  (true, true, every image counted)
test_dataset_prep.py  PASS  22 assertions, stub tagger, no weights
real run, eva02       6/6 exported, archive verified, source caption byte-identical
```

## v0.5.9 - caption a LoRA dataset from a folder

Training LoRAs on Civitai needs captions; producing them by hand for a few hundred
images is the slow part. This adds the front half and nothing else: no trainer, no
dependency on ComfyUI's Python environment, no upload.

`TOOLS/anima_dataset.py` walks a folder, runs a WD14 v3 tagger over every image, and
writes a `.txt` beside it: trigger word, your concept sentence, then tags sorted by
confidence. `--zip` also produces the archive Civitai's trainer takes, with images,
captions, `trigger.txt` and a manifest.

Captions warn about the failures that quietly ruin a LoRA - a tag present in every
caption (it gets baked in), captions with almost no tags, and images where the tagger
had no confidence at all.

Tagger weights are Apache-2.0 and installed outside the repository by
`TOOLS/Install-Tagger.ps1`; inference uses the ONNX Runtime ComfyUI already ships.

Verified on six renders: `eva02` 1.3 s/image, `swinv2` 0.7 s/image on CPU, captions
read `1girl, red eyes, school uniform, cherry blossoms, white hair` for a schoolgirl
portrait. A normalised-RGB input makes the same model answer `monochrome, no humans`
for a colour image - the tool reads BGR at 0-255, which is what WD14 expects.

`TESTS/test_dataset_prep.py` covers tag filtering, caption order, the leak warning,
resume, dry-run and the archive, using a stub tagger so CI never downloads weights.

## v0.5.8 - metadata that was never written, a test that never wrote

Three findings from review of 0.5.7, each reproduced before the fix.

### Metadata copying was unreachable

`_copy_exclusive()` ended its failure path with `raise`, and `shutil.copystat()` sat
after that raise. A successful cross-volume copy therefore never copied metadata:
source mtime 1600000000 arrived as "now", and on POSIX a 0600 source became 0644.

Reproduced on 0.5.7 - `finding1_copystat_reached: false`; 0.5.8 - `true`. The stat copy
now runs after a completed write and inside the protected scope, so a failure there
still removes the partial this code created.

### The partial-copy test measured an empty file

The test copied a 4096-byte source with a 4 MiB chunk and `fail_after=1000`, and the
injection fired before any write - so it asserted cleanup of a zero-byte file while
reporting that a partial was removed.

Injection now fires after bytes are on disk. Reproduced on 0.5.7 -
`finding2_partial_bytes: 0`; 0.5.8 - `4096`. The test observes the partial at the
moment `_copy_exclusive` deletes it and asserts it existed, held bytes, and that the
failure name the byte count.

### The trusted output root was inferred from the thing it was meant to verify

`infer_output_root()` walked up from the backend-reported path to any directory named
`output` with a sibling `models`. Whoever fabricates the report also fabricates that
shape, so the check proved nothing.

`resolve_output_root()` replaces it: an explicit `--output-root`, `COMFYUI_OUTPUT_DIR`,
or the frontend installation record this machine already has. No backend-reported path
is consulted. When none of those names an existing directory the run fails closed with
the flag to pass. Reproduced on 0.5.7 - `finding3_forged_root: accepted`; 0.5.8 -
`refused`.

The bundled backend writes to `<frontend>/dlbackend/comfy/ComfyUI/output`, which is
checked before the frontend's own `Output` tree; `--output-root` remains the override
for nonstandard installs.

### Verified

```
probe_058.py on 0.5.7 ..... FAIL (false, 0, accepted)
probe_058.py on 0.5.8 ..... PASS (true, 4096, refused)
test_merge_artifacts.py ... PASS
Test-CliSurface.ps1 ....... PASS (install skipped)
test_workflow_convert.py .. PASS
preflight.ps1 ............. 16 passed, 0 failed, 0 unverified
```

## v0.5.7 - a lost race no longer deletes the winner

Three findings from an external review of 0.5.6, each reproduced before it was fixed.

### P0 - the destination race deleted another writer's file

`expose()` refused an existing destination, then opened it exclusively - correct - but
on any failure it unlinked the destination if one existed. If another process created
that file between the check and the open, `_copy_exclusive` raised `FileExistsError`
and the handler deleted *their* file.

Reproduced against 0.5.6: `competing file survives: False`.

Cleanup now belongs to whoever created the file. `expose()` never unlinks the
destination; `_copy_exclusive` removes only the partial it created, and a lost race
propagates untouched. Failure injection moved inside the real writer (`fail_after`),
so tests exercise the ownership rule instead of substituting their own writer.

### P1 - the CLI-surface test could run the real installer

It recognised two hard-coded paths (`S:\Misc\SwarmUI`, `C:\SwarmUI`). The CLI also
discovers a frontend through `SWARMUI_ROOT` and through its saved state file, so an
install at any other path would have made a read-only test run `install`.

The test now resolves the frontend the way the CLI does - env var, both state
locations (`comfy-kit` and the legacy bundle path), then the conventional folders -
and skips `install` whenever any of them exists. `-DiscoveryOnly` exposes the
resolution for verification, and a regression sets `SWARMUI_ROOT` to a temporary
directory to prove a nonstandard install is still found.

### P2 - backend-reported paths were trusted too broadly

`claim_saved()` took `root` as optional and `main()` never passed it, so any file with
the run's prefix anywhere on disk could be claimed and deleted. `preview_path()`
joined the backend's filename onto the saved path, so `../` traversal survived as long
as the basename matched.

`root` is now required with no default, containment is checked on real paths, and the
trusted root is inferred only from a ComfyUI-shaped tree (`output` next to `models`) or
supplied with `--output-root`. Preview filenames must be simple basenames with the
expected prefix and `type: output`; traversal and subfolders are refused.

### Verified

```
before: probe_race.py ....... competing file survives: False
after:  probe_race.py ....... competing file survives: True
test_merge_artifacts.py ..... PASS
Test-CliSurface.ps1 ......... PASS (install skipped, nonstandard root discovered)
test_workflow_convert.py .... PASS
compileall TOOLS TESTS ...... ok
```

## v0.5.6 - the merge test only deletes what it created

The live merge test wrote a multi-gigabyte checkpoint and trusted whatever path the
backend reported. A mocked or buggy backend could hand it a user's model, and the
test would delete it in cleanup. Ownership is now proven before anything is
registered for deletion, and cleanup no longer hides failures.

- **Ownership is the run's unique prefix, checked before deletion.** Every saver
  node is given `comfy-kit-4way-<run id>`; a reported `saved_path` is claimed only
  when its basename carries that prefix, it is a regular file, and (when the model
  directory is known) it sits inside it. Zero or several matches are refused with
  the reported paths in the message - ambiguity is never guessed at.
- **The preview is registered before `/view`, not after.** A failed download used
  to leave the PNG behind; it is now claimed and registered first, so every failure
  path after that takes it with it.
- **Cleanup reports what survived.** Gone paths are ignored; a permission or
  file-lock failure is printed with its reason and returned, and a run that leaves
  artifacts behind exits FAIL instead of PASS.
- **Publication is exclusive.** `expose()` checked `exists()` and then copied, so a
  file created in between was overwritten. The copy now opens the destination with
  `O_CREAT|O_EXCL` and streams into it: the second writer loses instead of
  destroying the first. The same-volume hard link was already exclusive.
- **New failure-injection coverage:** unexpected backend paths, a pre-existing user
  file, a preview download that dies, a locked file during cleanup, keep mode, and
  a forced exists()-lies race proving the exclusive open, not the pre-check, is the
  guard.

## v0.5.5 - the CLI surface is tested, not just parsed

0.5.4 restored `Get-NodePackDrift` and added `status` as a gate. That covered one
verb. `install`, `generate` and `merge` still had no regression coverage, and the
failure that started this - a helper deleted by a refactor - could happen again in
any of them.

`TESTS/Test-CliSurface.ps1` now checks four things:

- **every read-only verb runs**: `help`, `presets`, `status`, `mcp` must exit 0 and
  print what they claim to print
- **the call graph resolves**: both script and libraries are parsed, and any call
  to a function that is neither defined locally nor resolvable as a command fails
  the test. That is the deleted-helper bug caught statically, before a user runs it.
  `Get-NodePackDrift` also keeps a named check.
- **error paths refuse cleanly**: `merge` with no donors, `merge` with a missing
  donor file, `generate` with an unknown preset must exit non-zero with a clear
  message - and `merge` must write nothing when it refuses.
- **`install` is not run where a real SwarmUI exists**: it reports `skip` rather
  than touching an installation. On CI, with no SwarmUI, it must refuse cleanly.

Verified by mutation: renaming `Get-NodePackDrift` fails the suite three ways
(verb, call graph, named check), then passes again when restored.

The test found one real defect on a machine without SwarmUI: `install` printed
"nothing to do" and exited 0. It now refuses and names the launcher to run first.
The unknown-preset check also no longer depends on child-process `Write-Host`
output being captured - the exit code is the contract, the text is asserted when
it arrives.

## v0.5.4 - `status` works again

The 0.5.1 refactor that moved the node-pack evaluator into
`TOOLS/node-pack-lib.ps1` also deleted `Get-NodePackDrift`, which sat in the same
block. Every release since then shipped a `status` verb that threw
`CommandNotFoundException` after printing the model list. No test ran the verb, so
nothing caught it.

`Get-NodePackDrift` is restored, and `status` is now a preflight and CI check:
the verb has to run, not merely parse.

## v0.5.3 - cleanup covers the render preview

The merge test removed its checkpoint and the model link, but left the 0.35 MB
preview PNG that SaveImage writes into output/ - one file per run, forever. It is
now registered with the rest and removed unless --keep-artifacts is set.

## v0.5.2 - maintenance

Follow-up to 0.5.1. No behaviour changes for a normal run.

### Fixed

- **A failed merge test could leave 4.18 GB behind.** The saved checkpoint was
  only registered for cleanup after the file had been exposed to `UNETLoader`, so
  a failure in between (unreadable safetensors, name collision, link error) left
  the whole merge in `output/`. It is now registered the moment it exists, and a
  cross-volume copy that dies midway removes its own half-written file instead of
  leaving a truncated model. Collision refusal is unchanged, and no pre-existing
  file is ever removed.
- **`0.5.1-rc.1` satisfied the `0.5.1` heading check.** The changelog test matched
  a version prefix. It now compares the version token exactly, and the matcher is
  self-tested against `0.5.1-rc.1`, `0.5.10` and an unversioned heading.

### Added

- `TESTS/test_merge_artifacts.py` - failure injection for the merge test's
  artifacts: hard link vs copy, collision refusal, mid-copy failure, free-space
  refusal, and cleanup in keep and remove modes.

## v0.5.1 - safety patch after v0.5.0

`v0.5.0` shipped with a stable tag on a tree that still declared `0.5.0-rc.5`,
so the tag, the changelog and STATE.md disagreed. That is fixed here, and the
three user-data bugs found afterwards are closed.

### Fixed

- **The live merge test could delete a user's model.** It derived a predictable
  name under `models/diffusion_models`, unlinked whatever was there and linked the
  new merge over it. Every run now uses a unique name, refuses a collision
  outright, copies only when the volumes differ (after a free-space check) and
  removes nothing except the two files it created. `--keep-artifacts` leaves them
  for inspection.
- **The installer deleted partial files it did not own.** A `.partial` with a
  missing or unrecognised `.id` sidecar was treated as garbage. Ownership is now
  structural: staging lives in `.stage-<identity-hash>/` and only a directory
  whose `id` matches the expected `sha256|bytes` is resumed or removed. Anything
  else is left byte-for-byte where it is.
- **Crash recovery guessed from filenames.** If the destination was missing, the
  newest `.replaced-*` was moved back - including a historical backup the user had
  deliberately kept, and it ran before the lock. Recovery now happens inside the
  lock, from a transaction marker written before the move, and only for the same
  artifact identity. A complete staged file is published without another network
  request.
- **Resume did not work, and a 416 crashed the install.** Staging names changed
  per run, so `curl -C -` never had a partial. A server that rejects ranges
  (curl exit 33) is now handled as "restart clean" instead of an error the user
  cannot act on.
- **The shipped workflow was never executed by the tests.** The live test rebuilt
  an equivalent graph in Python with different weights and a bypassed saver. It
  now converts `workflows/anima-4way-merge.json` itself - the same loaders, the
  same weights, the same `anima_beta57` scheduler - and the saver is enabled by
  default so a beginner actually gets a file.
- **A failing `git status` could read as `locked`.** It is now `unverifiable`,
  because a status command that did not run proves nothing about the tree.
- **A failed clone blocked every later install.** A pack folder without `.git` is
  moved aside (contents preserved) instead of making `git clone` fail forever; a
  real checkout is never touched or reset.
- **The state file was written into another tool's folder.** `comfy-kit` now owns
  `%LOCALAPPDATA%\comfy-kit`, while still reading a record left at the old
  bundle path.

### Added

- `TESTS/workflow_to_api.py` - converts the shipped UI workflow with explicit
  widget tables and hard failures on any mismatch.
- `TESTS/test_workflow_convert.py` - asserts the shipped recipe, the enabled
  saver and that drift (short widget list, unknown class) is rejected.
- `TESTS/Test-Release.ps1` - VERSION, top changelog heading, STATE and (on a tag)
  the tag name must agree; a released tag may not declare an RC version.
- `.github/workflows/release-verify.yml` - runs those checks on the tagged tree.
- `TOOLS/node-pack-lib.ps1` + `TESTS/Test-NodePackState.ps1` - real temporary git
  repositories covering locked, dirty, drift, foreign, no-origin, failing-status
  and partial-clone states.
- `TESTS/Test-ModelInstall.ps1` now drives real HTTP against
  `TESTS/fixtures/http_range_server.py`: Range honoured (206), ignored (200),
  rejected (416), complete-partial, and user-owned partial preservation.

## v0.5.0-rc.5 - correctness and honest tests

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0, HaDeS 0.4.7)

```
integration_merge_graph.py
  donors 4 | merger HaDeSAnimaMultiModelMerger | method Task Arithmetic
  saver  -> S:\...\output\comfy-kit-4way-proof_00001_.safetensors (4.18 GB)
  saved  685 tensors, loadable by safetensors
  visible to UNETLoader in models/diffusion_models
  render mean=157.0 range=0-255
  MERGE GRAPH: PASS (merge -> save -> reload -> render)
```

This is the shipped four-way path executed end to end for the first time - the
previous integration test only generated an image from an existing checkpoint.


### Fixed

- **The fallback merge dropped `--report`.** The retry rebuilt its argument list
  by hand, so a full checkpoint merged through `-AcceptFallbackAnchor` was
  reported as diffusion-only and routed to `models/diffusion_models`, where
  `CheckpointLoaderSimple` cannot see it. One argument builder now serves every
  execution, and a missing, unreadable or mismatched report stops the run instead
  of guessing a category.
- **"Full checkpoint" now means model + encoder + VAE.** A file with only one of
  the two is treated as diffusion-only and says so.
- **`Test-NodePack` still meant "the folder exists".** Status and installation now
  share one state evaluator; only `locked` or `dirty` counts as installed.
- **The pack list was duplicated** between a hardcoded array and the lock. The
  lock is now the only source, a missing lock refuses to install anything, and
  clone/checkout/verification failures all reach the exit code.
- **A missing git origin could still read as `locked`**, and equivalent GitHub
  origin spellings were compared as raw strings. Origin is now normalised and an
  absent origin is `unverifiable`.
- **Resumable downloads were not resumable.** Staging used a fresh GUID per run,
  so `curl -C -` never had a partial to continue. The partial is now stable and
  tied to the expected SHA-256 and size, a partial from a different artifact is
  discarded, a second installer is refused by a lock file, and a server that
  ignores the range is detected and restarted cleanly.
- **The publication-failure test never reached publication.** It placed a
  directory at the destination, which the installer rejects before downloading.
  There is now a publication seam that fails after the backup exists, plus crash
  recovery for the window between `move aside` and `publish`, and an explicit
  message naming the backup when a restore itself fails.
- **First-run installs failed on a missing model category.** The staging
  directory is created with the destination.
- **`validate-lock.py` could turn a 404 into UNVERIFIED.** It now confirms the
  repository is reachable before deciding, classifies 401/403/rate-limit as
  unknown, rejects malformed entries and non-object packs, and has a mock-based
  self-test.
- **`check_deps.py` exited 0 with `status: failed`.** Exit 2 now means a mandatory
  requirement is missing or failed; `--report-only` is for the installer, which
  installs what is missing.
- **MCP reads had no deadline** and the port probe accepted any open socket. Reads
  are bounded, and the probe requires a real `/system_stats` answer.
- **Preflight judged a command that never launched by the previous exit code.**
  It now reports "could not run" and can emit JSON evidence with commit, version
  and per-check state.

### Added

- `TESTS/integration_merge_graph.py` - the four-way HaDeS graph through the real
  API: merge, save, reload through `UNETLoader`, render, with pixel verification.
- `TESTS/fixtures/http_range_server.py` - a local fixture that honours or ignores
  `Range:`, used to prove the resume logic against real bytes.

## v0.5.0-rc.4 - closeout

### Fixed

- **A logging helper that does not exist could lose a checkpoint.** The model
  installer moved an existing file aside, then called `Write-UabsNote` - defined
  nowhere in this repo - and the backup move sat outside the `try`. One missing
  function and the destination was gone. Model installation is now one
  transaction in `TOOLS/model-install-lib.ps1`: stage, verify size and SHA-256,
  back up, publish, roll back. Logging failures are swallowed by design.
- **The staging path was doubled.** The wrapper passed `file.part` into a function
  that appended `.part` again. There is now exactly one staging path, unique per
  run, cleaned only by the operation that owns it.
- **The lock pinned a commit that does not exist.** `fb2b26f` is a ComfyUI
  commit: the build machine's pack folder carried ComfyUI's `.git`, so the
  installer recorded the wrong repository's HEAD. HaDeS is now pinned to
  `3c0a603` (v0.4.7), verified by cloning it and confirming 25 node classes,
  including `HaDeSAnimaMultiModelMerger` and `HaDeSAnimaModelSaver`.
- **A failed checkout counted as installed.** Install now verifies HEAD after
  checkout and fails; pack state distinguishes `locked / drift / dirty /
  foreign-repo / unverifiable / missing` instead of "folder exists".
- **Encoder and VAE tensors could be blended.** The merge now classifies every
  tensor (`diffusion / encoder / vae / adapter / scale / other`) and only
  diffusion weights merge; everything else is copied from the base.
- **A checkpoint with two spellings of one tensor** (`net.x` and
  `model.diffusion_model.x`) was merged by silently picking one; it is refused.
- **Merge failures deleted an unrelated `<output>.part`.** The writer owns its
  unique staging file; the outer handler no longer touches anything.
- **Full checkpoints were mirrored into `diffusion_models`**, where
  `CheckpointLoaderSimple` cannot see them. The merge now reports whether the
  result carries encoder+VAE and routes it to `models/checkpoints` or
  `models/diffusion_models` accordingly.
- **Dependency validation could pass while unresolved.** Optional extras are no
  longer treated as mandatory, a missing `packaging` is a failed validation, and
  unsupported or unverified requirements make the install exit nonzero.
- **`Extensions` was treated as `custom_nodes`.** Removed: a node pack installed
  there registers nothing.

### Added

- `TOOLS/validate-lock.py` - schema plus "does this commit exist in the repo the
  lock names", with UNVERIFIED when the network cannot answer.
- `TOOLS/preflight.ps1` - one command, every local check, an explicit release
  verdict that blocks on failures *and* on unverified checks.
- `docs/MAINTAINERS.md` - ownership map, the rules that exist because they were
  violated, and the release sequence.
- MCP proof is now a real workflow: `TESTS/test_mcp_handshake.py --workflow`
  enqueues through the MCP tool and waits for a rendered image.

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0, HaDeS 0.4.7)

```
integration_live.py --url http://127.0.0.1:7821
  backend ComfyUI 0.44.0 | HaDeS 25 nodes | euler/beta57 | 512x512 mean=119.4
  INTEGRATION: PASS
test_mcp_handshake.py --workflow
  generate_image -> enqueued 665c13d5 | rendered ComfyUI_00001_.png
  MCP: PASS (workflow executed)
```

## v0.5.0-rc.3 - third candidate

### Fixed

- **`--dtype` rewrote tensors it must not touch.** The requested dtype was
  assigned to every tensor, so `--dtype fp16` turned an int64 counter, a bool
  mask, a `.scale_weight`, a protected adapter or an encoder tensor into FP16.
  Conversion now applies only to tensors that are actually blended; everything
  else keeps its original dtype and bytes. Non-float tensors are never converted.
- **Unknown safetensors dtypes crashed with `KeyError`** or would have been
  guessed. FP8 and complex dtypes are now sized for byte-exact copying, and any
  dtype this build cannot describe aborts the merge before writing anything.
- **The writer replaced the destination before proving the file was loadable.**
  It now stages to a unique temporary file, checks every tensor's byte length
  against the header, reopens the result with the safetensors library, and only
  then publishes. A failed verification leaves an existing destination intact and
  cleans up its staging file.
- **Input/output identity was compared as path text.** It now uses real file
  identity, so relative paths, hard links and symlinks are caught.
- **HaDeS declares its dependencies in `pyproject.toml`**, which the installer
  never read: it looked only for `requirements.txt` and found nothing. A new
  resolver (`TOOLS/check_deps.py`) reads both, keeps version constraints, reports
  directives it cannot replay instead of dropping them, and is covered by tests.
- **`Find-Python` returned the bare name `python`**, which `Test-Path` cannot
  test, so dependency work silently skipped. It now returns a verified executable
  path, and refuses to fall back to the global interpreter when a frontend exists
  without one.
- **A model download could destroy an existing checkpoint** whose filename
  matched the manifest but whose bytes did not. The installer now keeps it as a
  `.replaced-*` backup and restores it if publication fails.
- **`image-stack.ps1` contained two copies of every function**, the second older
  than the first: PowerShell executed the stale one, so new code was dead.
  Removed 281 duplicate lines.

### Added

- `config/dependencies.lock.json` pins HaDeS and RES4LYF by commit, with the
  node classes they provide. `install` checks out the pin; `status` reports drift
  and never resets a checkout.
- `TESTS/integration_live.py` submits a real API-format graph to a live ComfyUI,
  waits for completion, downloads the image and rejects a flat/black frame.
- `docs/TESTING.md`, `docs/UPDATING.md`, `TESTS/Test-ModelInstall.ps1`,
  `TOOLS/check_deps.py --self-test`, and PowerShell 5.1 steps in CI.

### Verified live (RTX 4080 SUPER, ComfyUI 0.44.0)

```
backend     ComfyUI 0.44.0
nodes       1432 registered
HaDeS       25 nodes, e.g. HaDeSAnimaModelBlockMerger
checkpoint  Anima\woomixAnima_v175.safetensors
sampler     euler / beta57
image       comfy-kit-integration_00001_.png (359817 bytes)
pixels      512x512 mean=119.4 range=0-255
INTEGRATION: PASS
```

This is the first time the shipped path is proven to execute: HaDeS had never
registered on the live backend before this round (a stale process was serving
the old node list).

## v0.5.0-rc.2 - second candidate

### Fixed

- **Integer and bool tensors crashed the writer** (`KeyError: I64`); full
  checkpoints carry step counters and masks. The dtype table now covers every
  safetensors dtype, non-float tensors are copied from the base verbatim, and
  they are reported separately instead of counted as "skipped".
- **An output path equal to an input checkpoint** is refused, with or without
  `--force`.
- **Inherited identity metadata** (`author`, `license`, `license_url`, `date`)
  is dropped like `workflow`, `prompt` and `hash_sha256` already were.
- **Dependency constraints were dropped** (`numpy>=1.26.4` became `numpy`) and
  pip was invoked through a concatenated shell string. Requirements now travel as
  argv with their constraints, torch is never replaced, and an unsatisfied
  requirement fails the install instead of printing a warning.
- **Standalone ComfyUI could not receive node packs**: `Get-ComfyNodeDir`
  required SwarmUI. It now resolves standalone roots (env, portable, venv).

### Changed

- Workflow and MCP validation return **UNVERIFIED (exit 3)** instead of PASS when
  they cannot verify; CI turns that into a warning, never a silent pass.
- The workflow validator no longer hardcodes one machine: paths come from
  `COMFYUI_MODEL_DIRS` / `COMFYUI_ROOT`, and `--self-test` proves the lineage gate
  with synthetic 40-block and 28-block headers.
- Mirror backups are GUID-suffixed, copies are verified by SHA-256, and a failed
  replacement restores the file it displaced.
- `TESTS/Test-Version.ps1` fails when VERSION, CHANGELOG and STATE disagree.

## v0.5.0-rc.1 - release candidate

### Fixed

- **Four-way merges silently used three donors.** A fallback that anchored on
  donor #1 also removed it from the donor list, so weights renormalised over the
  rest: 31.48/27.78/22.22/18.52 became 0/40.54/32.43/27.03. The anchor now keeps
  its weight (zero task vector) and the fallback is opt-in.
- **The mirror could delete the file it was mirroring** and verified links by
  name. It now compares volume serial + file id, detects in-place targets, sets
  collisions aside and verifies copies.
- **Node-pack dependencies were never installed.** Requirements are read from each
  pack's manifest and installed into the frontend's own interpreter.
- **MCP config pointed at 8188.** `comfyui-mcp` ignores `COMFYUI_HOST`/`PORT`;
  `--comfyui-url` is what it honours.
- **The shipped workflow paired a 40-block base with 28-block donors.**

## v0.4.2 — keep the kit generic

A model-release package (`civitai/`) was committed here for a personal
checkpoint. That was out of scope for a general toolkit and has been removed;
those materials live outside this repo. The merge fixes from v0.4.0 are
unaffected.

## v0.4.0 — full checkpoints and honest hashes

### Fixed

- **Merging a full checkpoint crashed.** Scalar (0-dim) tensors cannot be viewed
  as `uint8`; `tensor_bytes` now reshapes first. This blocked every merge whose
  base carried an encoder or VAE.
- **The reported `sha256` was not the file's hash.** The writer digested
  `header || data` and skipped the 8-byte length prefix, so the printed value
  never matched `sha256sum` — a hash you could not publish. The prefix is now
  included; the printed digest and an independent `sha256sum` agree.
- **`merge` could claim a link that did not exist.** `New-Item -ItemType HardLink`
  reported success while leaving a separate copy in ComfyUI's tree. The link is
  now made with `mklink /H` and verified with `fsutil hardlink list`.

## v0.3.1 — a merge no longer pretends to be its base

### Fixed

- A merged checkpoint inherited the base's `workflow` and `prompt` blocks and its
  `modelspec.hash_sha256`, so the file claimed to be the base model (and the hash
  no longer matched the weights). Both are dropped; `modelspec.title`,
  `modelspec.description` and `modelspec.merge` now describe the blend, and the
  topology facts (`architecture`, `resolution`) are kept.
- Verified the merge math independently: reconstructed output vs the formula over
  12 tensors, max deviation `0.0008` (bf16 rounding).

## v0.3.0 — RES4LYF samplers in SwarmUI

### Added

- `install` clones **RES4LYF** alongside HaDeS, so `beta57`, `bong_tangent`,
  `res_2s`, `res_2m` and `rk_beta` exist. SwarmUI merges ComfyUI's `KSampler`
  lists into its own dropdowns at backend connect, so they become selectable
  after a restart (schedulers 9 → 11, samplers 45 → 64 here).
- Three presets on the new schedules: `Anima - Official Best (ER-SDE + beta57)`,
  `Anima - Author Daily (Euler + beta57)`, `Anima - Combo (res_2s + bong_tangent)`.
- `install` also installs `pywavelets`, which RES4LYF imports.

### Fixed

- `presets/ANIMA-HANDOFF.md` claimed RES4LYF names could not be used from
  SwarmUI. That was wrong: `ComfyUIBackendExtension.AssignValuesFromRaw` merges
  the live KSampler list. Corrected with the measured numbers.
- Documented that HaDeS's `anima_beta57` is `beta57` (same beta 0.5/0.7 schedule).

## v0.2.1 — where the merge actually lands

### Fixed

- A merge was written only to SwarmUI's `Models\Stable-Diffusion\Anima\`, so
  ComfyUI, the HaDeS workflow and the MCP could not see it. `merge` now hard-links
  the result into ComfyUI's `models\diffusion_models\` as well (a copy only when
  the trees are on different volumes). `-NoMirror` opts out.
- `LICENSE` carried a trailing note that made GitHub classify the repo as
  `NOASSERTION`. It is plain MIT again; the third-party note lives in the README.

### Repo

- `main` is protected: the `test` check is required, force pushes and branch
  deletion are blocked, and conversation resolution is on.
- Secret scanning, push protection and Dependabot security updates enabled.

## v0.2.0 — comfy-kit

Renamed from `uabs-image-stack`, and the four-way merge now works on real files.

### Added

- `install` fetches **everything** that is missing, not just models: the Anima
  custom node pack (`custom_nodes/hades-anima-suite`), `safetensors`/`numpy` in
  ComfyUI's python, and a check for Node.js. torch is never replaced — ComfyUI's
  CUDA build stays untouched.
- `status` reports each requirement with the command that fixes it.
- `merge` anchors on the first donor automatically when the requested base is a
  different Anima lineage, and says so. An explicit `-Base` is never overridden.
- `docs/banner.svg`, a real sample render in the README.

### Fixed

- **The 40-block trap.** `Anima-2.9B-preview-v1` has 40 blocks; the four Anima
  fine-tunes have 28. Blending them by name loads fine and renders pure black.
  The merger now returns a distinct exit code for that case and the CLI retries
  with a same-lineage anchor instead of writing a dead checkpoint.
- `move explicit-base capture`: the retry never fired because the default base
  was assigned before the "did the user pick one" check.

### Verified

- Four-donor merge (0.85/0.75/0.60/0.50) anchored on `Versetility`: 567 tensors
  merged, 4.18 GB written, rendered a coherent 1024×1024 image with
  `Anima - Official Best (ER-SDE + SGM)`.

## v0.1.0 — first usable starter pack

Extracted from Ultimate-AI-Starter-Bundle and turned into something a new user
can actually run.

### Added

- `TOOLS/image-stack.ps1` — one entry point: `status`, `install`, `presets`,
  `generate`, `merge`, `mcp`. Reads the installer's state file instead of
  rescanning drives, talks to SwarmUI's HTTP API, and discovers the ComfyUI port
  by probing `/system_stats`.
- `TOOLS/merge_anima.py` — offline Anima checkpoint merger. Writes a real
  `.safetensors` with bounded memory (streamed tensor by tensor), records the
  merge settings in `modelspec.merge`, and reproduces the
  `HaDeSAnimaMultiModelMerger` Task Arithmetic formula exactly.
- `workflows/anima-4way-merge.json` — the 4-way merge + generate graph, with the
  operator's donor weights (0.85 / 0.75 / 0.60 / 0.50, scale 1.0).
- `docs/MERGE.md` — how a merge works, the parameter meanings, compatibility
  rules and how to check the result.
- `TESTS/test_merge.py` — 18 assertions on the merge math, namespaces, adapter
  protection, dtype handling and refusal cases.
- `TESTS/Test-PsSyntax.ps1` — parse gate for the PowerShell tools.
- `TESTS/test_mcp_handshake.py` — starts `comfyui-mcp`, completes the MCP
  handshake and lists the tools it exposes.
- `.github/workflows/ci.yml` — runs all of the above on every push.

### Fixed

- MCP wiring: the upstream server defaults to ComfyUI on 8188, which is wrong
  for a SwarmUI install (its backend starts at 7820). The `mcp` verb now probes
  the live port and emits the correct config.
- Presets carry `vae: "Automatic"`, which SwarmUI rejects outright. The CLI
  strips it before sending.
- The merged-checkpoint download URL was built as `View/View/...` because
  SwarmUI already prefixes returned image paths with `View/`.

### Replaced

- A vendored 168 MB checkout of the third-party `comfyui-mcp` server is no longer
  part of the project. It is used as a pinned npm dependency
  (`comfyui-mcp@0.52.206`) instead.
