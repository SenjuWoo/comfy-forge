# Finds the ComfyUI folder that holds `models/`, for the weight installers.
# Dot-sourced by Install-3DModels.ps1 and Install-MusicModels.ps1 so the two
# cannot drift apart again (one read the install record, the other did not).
#
# Order: -ComfyRoot, -SwarmRoot, the SwarmUI named in comfy-forge's install
# record, SWARMUI_ROOT, COMFYUI_ROOT, then the usual standalone folders.
# Never throws: a candidate on a drive that does not exist is simply not a match
# (Join-Path raised "Cannot find drive" for D:\ComfyUI on a machine with no D:).

function Test-ComfyModelsRoot([string]$Root) {
    if ([string]::IsNullOrWhiteSpace($Root)) { return $false }
    try { return (Test-Path -LiteralPath ([IO.Path]::Combine($Root, 'models')) -PathType Container) }
    catch { return $false }
}

function Get-RecordedSwarmRoot {
    foreach ($state in @((Join-Path $env:LOCALAPPDATA 'comfy-kit\anima-swarmui.json'),
                         (Join-Path $env:LOCALAPPDATA 'Ultimate-AI-Starter-Bundle\anima-swarmui.json'))) {
        if (-not (Test-Path -LiteralPath $state)) { continue }
        try {
            foreach ($f in @((Get-Content -LiteralPath $state -Raw | ConvertFrom-Json).frontends)) {
                if ($f.kind -eq 'SwarmUI' -and $f.root -and (Test-Path -LiteralPath $f.root)) { return $f.root }
            }
        } catch { }
    }
    return $null
}

function Resolve-ComfyModelsRoot([string]$ComfyRoot, [string]$SwarmRoot) {
    if ($ComfyRoot) {
        # An explicit root is used or refused; it never falls through to a guess.
        if (Test-ComfyModelsRoot $ComfyRoot) { return (Resolve-Path -LiteralPath $ComfyRoot).Path }
        return $null
    }
    $swarms = @($SwarmRoot)
    if (-not $SwarmRoot) { $swarms = @((Get-RecordedSwarmRoot), $env:SWARMUI_ROOT) }
    foreach ($swarm in $swarms) {
        if (-not $swarm) { continue }
        $cand = [IO.Path]::Combine($swarm, 'dlbackend', 'comfy', 'ComfyUI')
        if (Test-ComfyModelsRoot $cand) { return $cand }
        if ($SwarmRoot) { return $null }
    }
    $candidates = @($env:COMFYUI_ROOT,
                    [IO.Path]::Combine($HOME, 'ComfyUI'),
                    [IO.Path]::Combine($HOME, 'Documents', 'ComfyUI'),
                    [IO.Path]::Combine($HOME, 'ComfyUI_windows_portable', 'ComfyUI'),
                    'C:\ComfyUI', 'C:\ComfyUI_windows_portable\ComfyUI', 'D:\ComfyUI')
    foreach ($c in $candidates) {
        if (Test-ComfyModelsRoot $c) { return $c }
    }
    return $null
}
