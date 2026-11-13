# Installs the weights for the 3D workflows shipped in presets/3d/.
# Nothing is downloaded without -Download; -WhatIf shows the exact list first.
#
#   powershell -File TOOLS/Install-3DModels.ps1 -WhatIf
#   powershell -File TOOLS/Install-3DModels.ps1 -Set idol
#   powershell -File TOOLS/Install-3DModels.ps1 -Set trellis2   # stock TRELLIS.2 path
#   powershell -File TOOLS/Install-3DModels.ps1 -Set all         # idol + pixal3d
#
# License per file (as listed on the model cards): Idol = Apache-2.0,
# Pixal3D / TRELLIS.2 / TripoSplat = MIT. BiRefNet (background removal) is a small
# convenience download shared by both stacks.
#
# Downloads use curl with -C -, so a partial file resumes instead of restarting.
# Upstream publishes no checksums for these weights, so integrity is not verified
# beyond the download completing; delete a bad file and re-run if it fails to load.
#
[CmdletBinding()]
param(
    [ValidateSet('idol', 'pixal3d', 'trellis2', 'all')]
    [string]$Set = 'all',
    # Explicit ComfyUI root. Default: SWARMUI_ROOT/dlbackend/comfy/ComfyUI if that
    # exists, else $env:COMFYUI_ROOT, else the usual standalone locations.
    [string]$ComfyRoot,
    # Explicit SwarmUI root (from which dlbackend/comfy/ComfyUI is derived).
    [string]$SwarmRoot,
    [switch]$WhatIf,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$HF = 'https://huggingface.co'
$files = [ordered]@{
    # set 'idol' — fine-tuned TripoSplat, works with the TripoSplat nodes
    'idol:diffusion_models\idol_fp16.safetensors'                              = "$HF/Comfy-Org/Idol/resolve/main/diffusion_models/idol_fp16.safetensors"
    'idol:clip_vision\dino_v3_vit_h_bf16.safetensors'                          = "$HF/Comfy-Org/Idol/resolve/main/clip_vision/dino_v3_vit_h_bf16.safetensors"
    'idol:vae\idol_decoder_fp16.safetensors'                                   = "$HF/Comfy-Org/Idol/resolve/main/vae/idol_decoder_fp16.safetensors"
    'idol:vae\flux2-vae.safetensors'                                           = "$HF/Comfy-Org/Idol/resolve/main/vae/flux2-vae.safetensors"
    # set 'pixal3d' — single-image path (int8) and multi-view path (int8)
    'pixal3d:diffusion_models\pixal3d_int8_convrot.safetensors'                = "$HF/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors"
    'pixal3d:diffusion_models\pixal3d_multiview_int8_convrot.safetensors'      = "$HF/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_multiview_int8_convrot.safetensors"
    'pixal3d:clip_vision\dino_v3_L_naf_fp32.safetensors'                      = "$HF/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors"
    'pixal3d:vae\trellis_2_shape_vae_bf16.safetensors'                         = "$HF/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors"
    'pixal3d:vae\trellis_2_texture_vae_bf16.safetensors'                       = "$HF/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors"
    # shared by both stacks, and used by the image-to-model workflow (MoGe depth/FOV)
    'pixal3d:geometry_estimation\moge_2_vitl_normal_fp16.safetensors'          = "$HF/Comfy-Org/MoGe/resolve/main/geometry_estimation/moge_2_vitl_normal_fp16.safetensors"
    'pixal3d:background_removal\birefnet.safetensors'                          = "$HF/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors"
    'idol:background_removal\birefnet.safetensors'                             = "$HF/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors"
    # set 'trellis2' — stock TRELLIS.2 path used by the image-to-model switch
    'trellis2:diffusion_models\trellis_2_int8_convrot.safetensors'             = "$HF/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors"
}
# sets are cumulative: idol = idol files, pixal3d = pixal3d files, all = idol+pixal3d
$sets = @{
    idol     = @('idol')
    pixal3d  = @('pixal3d')
    trellis2 = @('pixal3d', 'trellis2')   # trellis2 reuse: same VAE/CLIP/MoGe stack
    all      = @('idol', 'pixal3d')
}
$wanted = $sets[$Set]
$selected = [ordered]@{}
foreach ($key in $files.Keys) {
    $tag = $key.Split(':')[0]
    if ($wanted -contains $tag) { $selected[$key] = $files[$key] }
}
# de-duplicate shared URLs (BiRefNet and the trellis stack appear in both groups)
$seen = @{}
foreach ($key in @($selected.Keys)) {
    if ($seen.ContainsKey($selected[$key])) { $selected.Remove($key) } else { $seen[$selected[$key]] = $true }
}

function Resolve-ComfyRoot {
    if ($ComfyRoot -and (Test-Path -LiteralPath $ComfyRoot)) { return (Resolve-Path -LiteralPath $ComfyRoot).Path }
    $swarm = if ($SwarmRoot) { $SwarmRoot } elseif ($env:SWARMUI_ROOT) { $env:SWARMUI_ROOT } else { $null }
    if ($swarm) {
        $cand = Join-Path $swarm 'dlbackend\comfy\ComfyUI'
        if (Test-Path -LiteralPath (Join-Path $cand 'models')) { return $cand }
    }
    if ($env:COMFYUI_ROOT -and (Test-Path -LiteralPath $env:COMFYUI_ROOT)) { return $env:COMFYUI_ROOT }
    foreach ($c in @((Join-Path $HOME 'ComfyUI'), (Join-Path $HOME 'Documents\ComfyUI'), 'C:\ComfyUI',
                     (Join-Path $HOME 'ComfyUI_windows_portable\ComfyUI'), 'C:\ComfyUI_windows_portable\ComfyUI',
                     'D:\ComfyUI')) {
        if (Test-Path -LiteralPath (Join-Path $c 'models')) { return $c }
    }
    return $null
}

$root = Resolve-ComfyRoot
if (-not $root) {
    Write-Host 'Could not locate a ComfyUI root with a models/ directory.' -ForegroundColor Red
    Write-Host 'Pass -ComfyRoot <path> (standalone install) or -SwarmRoot <path> (SwarmUI install).'
    exit 1
}

Write-Host "ComfyUI root: $root"
Write-Host ''
foreach ($key in $selected.Keys) {
    $rel = $key.Split(':', 2)[1]
    $dest = Join-Path $root (Join-Path 'models' $rel)
    if (Test-Path -LiteralPath $dest) {
        $have = (Get-Item -LiteralPath $dest).Length
        Write-Host ("have {0} ({1:n0} bytes)" -f $rel, $have)
        continue
    }
    if ($WhatIf) {
        Write-Host ("would get {0}" -f $rel)
        continue
    }
    $dir = Split-Path $dest -Parent
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $tmp = "$dest.part"
    Write-Host "get  $rel"
    & curl.exe -L -C - --fail --retry 3 --retry-delay 5 -o $tmp $selected[$key]
    if ($LASTEXITCODE -ne 0) { throw "download failed for $rel (exit $LASTEXITCODE) - re-run to resume" }
    Move-Item -LiteralPath $tmp -Destination $dest -Force
    Write-Host ("  ok {0} ({1:n0} bytes)" -f $rel, (Get-Item -LiteralPath $dest).Length)
}
Write-Host ''
if ($WhatIf) {
    Write-Host "above is -WhatIf only; nothing was downloaded."
    Write-Host "see docs/3D-MODELS.md for which set you actually want."
    exit 0
}
Write-Host 'done. Open the matching presets/3d/*.json workflow in ComfyUI and check the notes.'
exit 0
