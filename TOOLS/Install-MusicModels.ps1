# Installs the ACE-Step 1.5 weights that the music workflows in presets/music/ load.
# Nothing is downloaded without a real run; -WhatIf prints the exact list first.
#
#   powershell -File TOOLS/Install-MusicModels.ps1 -WhatIf
#   powershell -File TOOLS/Install-MusicModels.ps1 -Set aio          # one 9.3 GB file
#   powershell -File TOOLS/Install-MusicModels.ps1 -Set turbo        # split files, 0.6B + 1.7B encoders
#   powershell -File TOOLS/Install-MusicModels.ps1 -Set xl-sft       # best quality, needs the 4B encoder
#
# Source: Hugging Face Comfy-Org/ace_step_1.5_ComfyUI_files (apache-2.0 per the
# model card). Every file is checked against its published byte size before it is
# moved into place; a short file is deleted and the run fails. Upstream publishes
# no checksums, so a size match proves the download finished, not that the bytes
# are the author's. Downloads use curl with -C -, so a partial file resumes.
#
# A CDN connection can get stuck far below the line speed. One that stays under
# -MinSpeedMB (default 5) for -SlowSeconds (default 30) is dropped and the download
# resumes on a new connection, up to -Reconnects (default 5) times; the last try has
# no floor, so a slow line still finishes. -MinSpeedMB 0 turns this off.
#
# Sets (cumulative files are shared, never downloaded twice):
#   aio       checkpoints/ace_step_1.5_turbo_aio.safetensors
#   turbo     turbo diffusion model + qwen 0.6B + qwen 1.7B + VAE
#   turbo-4b  turbo diffusion model + qwen 0.6B + qwen 4B + VAE
#   xl-turbo  XL turbo + qwen 0.6B + qwen 4B + VAE
#   xl-sft    XL SFT   + qwen 0.6B + qwen 4B + VAE
#   xl-base   XL base  + qwen 0.6B + qwen 4B + VAE
#   all       everything above
[CmdletBinding()]
param(
    [ValidateSet('aio', 'turbo', 'turbo-4b', 'xl-turbo', 'xl-sft', 'xl-base', 'all')]
    [string]$Set = 'aio',
    # Explicit ComfyUI root (the folder that holds models/).
    [string]$ComfyRoot,
    # Explicit SwarmUI root; its dlbackend\comfy\ComfyUI is used.
    [string]$SwarmRoot,
    [switch]$WhatIf,
    [switch]$Force,
    # Reconnect when a connection stays below this many MB/s for -SlowSeconds (0 = never).
    [double]$MinSpeedMB = 5,
    # How many times to reconnect before the final attempt, which has no floor.
    [int]$Reconnects = 5,
    # How long a connection may stay under -MinSpeedMB before it is dropped.
    [int]$SlowSeconds = 30,
    # Download source; a mirror with the same layout works. Tests point it at a local stub.
    [string]$BaseUrl = 'https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$HF = $BaseUrl.TrimEnd('/')
# name = destination under models/ ; value = repo path and published size in bytes
$catalog = [ordered]@{
    'aio.ckpt'      = @{ Rel = 'checkpoints\ace_step_1.5_turbo_aio.safetensors';           Repo = 'checkpoints/ace_step_1.5_turbo_aio.safetensors';                       Bytes = 10025478736 }
    'unet.turbo'    = @{ Rel = 'diffusion_models\acestep_v1.5_turbo.safetensors';          Repo = 'split_files/diffusion_models/acestep_v1.5_turbo.safetensors';          Bytes = 4787825604 }
    'unet.xlturbo'  = @{ Rel = 'diffusion_models\acestep_v1.5_xl_turbo_bf16.safetensors';  Repo = 'split_files/diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors';  Bytes = 9974719892 }
    'unet.xlsft'    = @{ Rel = 'diffusion_models\acestep_v1.5_xl_sft_bf16.safetensors';    Repo = 'split_files/diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors';    Bytes = 9974719930 }
    'unet.xlbase'   = @{ Rel = 'diffusion_models\acestep_v1.5_xl_base_bf16.safetensors';   Repo = 'split_files/diffusion_models/acestep_v1.5_xl_base_bf16.safetensors';   Bytes = 9974719930 }
    'clip.06'       = @{ Rel = 'text_encoders\qwen_0.6b_ace15.safetensors';                Repo = 'split_files/text_encoders/qwen_0.6b_ace15.safetensors';                Bytes = 1191588248 }
    'clip.17'       = @{ Rel = 'text_encoders\qwen_1.7b_ace15.safetensors';                Repo = 'split_files/text_encoders/qwen_1.7b_ace15.safetensors';                Bytes = 3708523360 }
    'clip.4b'       = @{ Rel = 'text_encoders\qwen_4b_ace15.safetensors';                  Repo = 'split_files/text_encoders/qwen_4b_ace15.safetensors';                  Bytes = 8379154232 }
    'vae'           = @{ Rel = 'vae\ace_1.5_vae.safetensors';                              Repo = 'split_files/vae/ace_1.5_vae.safetensors';                              Bytes = 337431732 }
}
$sets = @{
    'aio'      = @('aio.ckpt')
    'turbo'    = @('unet.turbo', 'clip.06', 'clip.17', 'vae')
    'turbo-4b' = @('unet.turbo', 'clip.06', 'clip.4b', 'vae')
    'xl-turbo' = @('unet.xlturbo', 'clip.06', 'clip.4b', 'vae')
    'xl-sft'   = @('unet.xlsft', 'clip.06', 'clip.4b', 'vae')
    'xl-base'  = @('unet.xlbase', 'clip.06', 'clip.4b', 'vae')
    'all'      = @($catalog.Keys)
}
$wanted = @($sets[$Set] | Select-Object -Unique)

function Resolve-ComfyRoot {
    if ($ComfyRoot) {
        if (Test-Path -LiteralPath (Join-Path $ComfyRoot 'models')) { return (Resolve-Path -LiteralPath $ComfyRoot).Path }
        return $null
    }
    $swarm = $SwarmRoot
    if (-not $swarm) {
        # The CLI's install record names the SwarmUI this stack uses.
        foreach ($state in @((Join-Path $env:LOCALAPPDATA 'comfy-kit\anima-swarmui.json'),
                             (Join-Path $env:LOCALAPPDATA 'Ultimate-AI-Starter-Bundle\anima-swarmui.json'))) {
            if (-not (Test-Path -LiteralPath $state)) { continue }
            try {
                foreach ($f in @((Get-Content -LiteralPath $state -Raw | ConvertFrom-Json).frontends)) {
                    if ($f.kind -eq 'SwarmUI' -and $f.root -and (Test-Path -LiteralPath $f.root)) { $swarm = $f.root; break }
                }
            } catch { }
            if ($swarm) { break }
        }
    }
    if (-not $swarm -and $env:SWARMUI_ROOT) { $swarm = $env:SWARMUI_ROOT }
    if ($swarm) {
        $cand = Join-Path $swarm 'dlbackend\comfy\ComfyUI'
        if (Test-Path -LiteralPath (Join-Path $cand 'models')) { return $cand }
    }
    if ($env:COMFYUI_ROOT -and (Test-Path -LiteralPath (Join-Path $env:COMFYUI_ROOT 'models'))) { return $env:COMFYUI_ROOT }
    foreach ($c in @((Join-Path $HOME 'ComfyUI'), (Join-Path $HOME 'Documents\ComfyUI'), 'C:\ComfyUI',
                     'C:\ComfyUI_windows_portable\ComfyUI', 'D:\ComfyUI')) {
        if (Test-Path -LiteralPath (Join-Path $c 'models')) { return $c }
    }
    return $null
}

$total = 0L
foreach ($k in $wanted) { $total += $catalog[$k].Bytes }

$root = Resolve-ComfyRoot
if (-not $root) {
    Write-Host 'error  Could not locate a ComfyUI root with a models/ directory.' -ForegroundColor Red
    Write-Host '       Pass -ComfyRoot <path> (standalone) or -SwarmRoot <path> (SwarmUI install).'
    exit 1
}
Write-Host "ComfyUI root: $root"
Write-Host ("set {0}: {1} file(s), {2:n2} GB if none are present" -f $Set, $wanted.Count, ($total / 1GB))
Write-Host ''

$missing = 0
foreach ($k in $wanted) {
    $item = $catalog[$k]
    $dest = Join-Path $root (Join-Path 'models' $item.Rel)
    if ((Test-Path -LiteralPath $dest) -and -not $Force) {
        $have = (Get-Item -LiteralPath $dest).Length
        if ($have -eq $item.Bytes) { Write-Host ("have  {0}" -f $item.Rel); continue }
        # Never overwrite a file this script cannot prove is its own.
        Write-Host ("warn  {0} exists with {1:n0} bytes, expected {2:n0}; left alone (use -Force to replace, the old file is kept as .replaced-*)" -f $item.Rel, $have, $item.Bytes) -ForegroundColor Yellow
        $missing++
        continue
    }
    if ($WhatIf) { Write-Host ("would get {0} ({1:n2} GB)" -f $item.Rel, ($item.Bytes / 1GB)); continue }

    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    $tmp = "$dest.part"
    Write-Host ("get   {0} ({1:n2} GB)" -f $item.Rel, ($item.Bytes / 1GB))
    # A CDN connection can settle at a small fraction of the line speed and stay
    # there for the whole file (seen: 1.9 MB/s on a link a fresh connection ran at
    # 64 MB/s). Drop a connection that stays under -MinSpeedMB for -SlowSeconds and resume on
    # a new one. The last attempt has no floor, so a genuinely slow line still finishes.
    #
    # Retries live here, not in curl's --retry: curl's own retry restarts the file at
    # byte 0 (measured with a stub server), throwing away what was downloaded. A new
    # curl with -C - recomputes the offset from the .part file every time.
    $slowLeft = $Reconnects
    $netLeft = 3
    $code = 1
    while ($true) {
        if ((Test-Path -LiteralPath $tmp) -and (Get-Item -LiteralPath $tmp).Length -eq $item.Bytes) { $code = 0; break }
        $floor = @()
        if ($slowLeft -gt 0 -and $MinSpeedMB -gt 0) { $floor = @('--speed-limit', [string]([int]($MinSpeedMB * 1MB)), '--speed-time', [string]$SlowSeconds) }
        & curl.exe -L -C - --fail @floor -o $tmp "$HF/$($item.Repo)"
        $code = $LASTEXITCODE
        if ($code -eq 0) { break }
        if ($code -eq 28 -and $floor.Count) {
            # The speed floor tripped: drop this connection and resume on a new one.
            $slowLeft--
            Write-Host ("      slow connection (under {0} MB/s for {1} s), reconnecting; {2} left" -f $MinSpeedMB, $SlowSeconds, $slowLeft)
            continue
        }
        # Network hiccups (resolve, connect, TLS, reset, empty reply): wait and resume.
        if (@(6, 7, 18, 28, 35, 52, 55, 56, 92) -contains $code -and $netLeft -gt 0) {
            $netLeft--
            Write-Host ("      network error (curl exit {0}), resuming in 5 s; {1} left" -f $code, $netLeft)
            Start-Sleep -Seconds 5
            continue
        }
        break   # an HTTP error (22) or anything else is a real failure
    }
    if ($code -ne 0) { throw "download failed for $($item.Rel) (exit $code) - re-run to resume" }
    $got = (Get-Item -LiteralPath $tmp).Length
    if ($got -ne $item.Bytes) {
        Remove-Item -LiteralPath $tmp -Force
        throw "$($item.Rel): got $got bytes, expected $($item.Bytes); the partial file was removed - re-run"
    }
    if (Test-Path -LiteralPath $dest) {
        $backup = "$dest.replaced-$(Get-Date -Format yyyyMMddHHmmss)"
        Move-Item -LiteralPath $dest -Destination $backup
        Write-Host "      kept the previous file as $backup"
    }
    Move-Item -LiteralPath $tmp -Destination $dest
    Write-Host ("  ok  {0:n0} bytes" -f $got)
}

Write-Host ''
if ($WhatIf) { Write-Host 'above is -WhatIf only; nothing was downloaded.'; exit 0 }
if ($missing -gt 0) { Write-Host "$missing file(s) need attention (see warnings)."; exit 1 }
Write-Host 'done. Check with: python TOOLS/music.py check --variant <variant>'
exit 0
