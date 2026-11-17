# Images: `generate`

One verb renders through SwarmUI's API: text to image, image to image, inpainting, LoRAs, transparent cut-outs, tileable textures and an upscale pass. SwarmUI builds the right graph for each model family (Anima, FLUX, SDXL, Z-Image), so one command line works for all of them.

```powershell
powershell -File TOOLS\image-stack.ps1 generate -Prompt "a lighthouse on a basalt cliff at dawn" -Out out\shots -Json
```

Nothing has to be running first. When SwarmUI does not answer, `generate` starts it headless (no window, no browser), waits until its ComfyUI backend has loaded (about 45 seconds on the reference machine) and leaves it running for the next render. `image-stack.ps1 stop` ends it. `-NoStart` refuses instead of starting.

## Options

| Option | What it does | SwarmUI parameter |
|---|---|---|
| `-Prompt "<text>"` | what to draw | `prompt` |
| `-Negative "<text>"` | what to avoid; replaces the preset's negative prompt | `negativeprompt` |
| `-Preset "<name>"` | sampler, scheduler, steps, CFG (`presets` lists them) | several |
| `-Model <name>` | checkpoint: full name or a bare file name | `model` |
| `-Width` `-Height` | size in pixels (default 1024 x 1024) | `width`, `height` |
| `-Seed n` | repeat a render; default picks one and reports it | `seed` |
| `-Count n` | images in the batch; seeds count up from the first | `images` |
| `-Steps n` `-Cfg n` | override the preset | `steps`, `cfgscale` |
| `-InitImage <file>` | start from this picture (image to image) | `initimage` |
| `-Creativity 0..1` | how far to move away from it (SwarmUI's default is 0.6) | `initimagecreativity` |
| `-Mask <file>` | repaint only the white area of the mask (needs `-InitImage`) | `maskimage` |
| `-Lora name[:weight],...` | add LoRAs; names resolve like `-Model` | `loras`, `loraweights` |
| `-Transparent` | remove the background; the PNG has an alpha channel | `removebackground` |
| `-Tileable` | make the image repeat without a seam (see the limit below) | `seamlesstileable` |
| `-Upscale 1.5` | a second pass at a larger size (1 to 4) | `refinerupscale` |
| `-Set key=value,...` | any other SwarmUI parameter by its id | as given |
| `-Out <folder>` | where the files go (default `out\`) | |
| `-Json` | end the output with one JSON line | |
| `-NoStart` | do not start the backend | |
| `-Port n` | a SwarmUI the caller started; never starts another | |

## Which model is used

1. `-Model`, when given. A bare name such as `woomixAnima_v180` is matched against SwarmUI's list; a name that fits two files is refused with both paths.
2. The preset's own model, when the preset names one. If that file is not installed the render is refused: a preset is tuned for its model.
3. `COMFY_FORGE_MODEL`, when set.
4. Otherwise the first model SwarmUI lists for the preset's family (an Anima preset gets an Anima checkpoint), shallowest folder first, then by name. The choice is printed. With no model of that family the render is refused, because a model of another family renders noise.

## What comes back

Each image is saved to `-Out` with a `.json` sidecar: preset, model, prompt, negative prompt, size, steps, CFG, sampler, scheduler, the seed of that image and every option used. `-Json` adds one closing line:

```json
{"files":["...png"],"seed":1067814254,"seeds":[1067814254],"model":"Anima/Anime/woomixAnima_v180.safetensors","preset":"Anima - Author Daily (Euler + SGM)","width":1024,"height":1024}
```

To repeat a render, pass the sidecar's `seed`, `preset` and `model` back.

## Recipes

```powershell
# a sprite or icon with a transparent background
image-stack.ps1 generate -Prompt "a red health potion, game icon, centered" -Transparent -Out out\icons

# change an existing picture, keeping its composition
image-stack.ps1 generate -Prompt "the same axe, golden blade" -InitImage axe.png -Creativity 0.5

# repaint one area: white in the mask is repainted
image-stack.ps1 generate -Prompt "a glowing rune" -InitImage shield.png -Mask rune-area.png -Creativity 0.8

# a character LoRA at 0.8 and a style LoRA at full weight
image-stack.ps1 generate -Prompt "..." -Lora "mychar:0.8,inkstyle"

# a larger final image from the same composition
image-stack.ps1 generate -Prompt "..." -Upscale 1.5

# a wide frame, 16-bit output: any SwarmUI parameter
image-stack.ps1 generate -Prompt "..." -Set "aspectratio=16:9,colordepth=16bit"
```

An image made here goes straight into the 3D tool (`python TOOLS/model3d.py generate --image <file>`), or `model3d.py generate --prompt "..."` does both steps.

## Limits

- `-Tileable` is SwarmUI's seamless mode, which works with UNet models (SDXL). Anima, FLUX and Z-Image are transformer models; for those, make the texture tile in Asset Forge (`texture-tileable`).
- `-Transparent` uses SwarmUI's background removal, which downloads its model the first time it is used.
- Presets carry settings, not prompts (four pose and inpaint presets carry a prompt of their own, used when `-Prompt` is not given).
- Text in the prompt is sent as UTF-8; names and tags in any script arrive intact.
- Quality is not checked. Look at the image.
