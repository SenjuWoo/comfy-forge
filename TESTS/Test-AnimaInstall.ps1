<#
Optional Anima stack: proves the gate, the bounded discovery and the offline
path without a GPU, a model download or a real frontend.
#>
[CmdletBinding()]
param([string]$PackRoot)
$ErrorActionPreference = 'Stop'
if (-not $PackRoot) { $PackRoot = Split-Path -Parent $PSScriptRoot }
$installer = Join-Path $PackRoot 'TOOLS\Ensure-AnimaSwarmUI.ps1'
$fail = 0
function Check($Condition, [string]$Name) {
  if ($Condition -eq $true) { Write-Host "ok   $Name" } else { Write-Host "FAIL $Name" -ForegroundColor Red; $script:fail++ }
}
$fx = Join-Path ([IO.Path]::GetTempPath()) ('uabs-anima-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$empty = Join-Path $fx 'empty'; New-Item -ItemType Directory -Force -Path $empty | Out-Null
$stateDir = Join-Path $fx 'state'; New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

# 1. No frontend: quiet no-op, no state claim, no download.
$out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -CheckOnly -Refresh -NoKnownRoots -SearchRoot $empty -StateDir $stateDir 2>&1 | Out-String
Check ($LASTEXITCODE -eq 0) 'no frontend exits 0'
Check ($out -match 'No SwarmUI or ComfyUI') 'no frontend says so and stops'

# 2. Fake SwarmUI is detected by its markers and only reports a would-download.
$swarm = Join-Path $fx 'SwarmUI'
New-Item -ItemType Directory -Force -Path (Join-Path $swarm 'Data'), (Join-Path $swarm 'Models') | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $swarm 'SwarmUI.sln'), (Join-Path $swarm 'Data\Backends.fds') | Out-Null
$out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -CheckOnly -SwarmUIRoot $swarm -StateDir $stateDir 2>&1 | Out-String
Check ($LASTEXITCODE -eq 0) 'fake SwarmUI check exits 0'
Check ($out -match 'SwarmUI: ' + [regex]::Escape($swarm)) 'SwarmUI markers accepted'
Check ($out -match 'would download') 'missing files are reported, not fetched'

# 3. Fake standalone ComfyUI is detected.
$comfy = Join-Path $fx 'ComfyUI'
New-Item -ItemType Directory -Force -Path (Join-Path $comfy 'comfy'), (Join-Path $comfy 'models') | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $comfy 'main.py'), (Join-Path $comfy 'comfy\samplers.py') | Out-Null
$out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -CheckOnly -ComfyUIRoot $comfy -StateDir $stateDir 2>&1 | Out-String
Check ($out -match 'ComfyUI: ' + [regex]::Escape($comfy)) 'ComfyUI markers accepted'

# 4. Offline is not a failure: the optional stack skips instead of erroring.
$out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Offline -SwarmUIRoot $swarm -StateDir $stateDir 2>&1 | Out-String
Check ($LASTEXITCODE -eq 0) 'offline run exits 0'
Check ($out -match 'offline mode, not downloaded') 'offline says the file was skipped'
Check (@(Get-ChildItem -LiteralPath $swarm -Recurse -Filter '*.part' -ErrorAction SilentlyContinue).Count -eq 0) 'offline wrote no partial file'

# 5. The module keeps its safety properties.
$text = [IO.File]::ReadAllText($installer)
Check ($text -notmatch "\@\('C','D','E'") 'no drive-letter scan loop'
Check ($text -match 'Get-UabsCachedFrontends') 'frontends are reused from state'
# The downloader now lives in the shared transaction library, so the property
# is asserted against the file that actually runs curl.
$libText = [IO.File]::ReadAllText((Join-Path $PackRoot 'TOOLS\model-install-lib.ps1'))
Check ($libText -match '-sS') 'curl keeps the silent meter'
Check ($libText -match '-C -') 'curl keeps resumable downloads'
Check ($libText -notmatch 'Write-UabsNote') 'the transaction does not depend on a helper the fallbacks lack'
$manifest = [IO.File]::ReadAllText((Join-Path $PackRoot 'presets\anima-manifest.json')) | ConvertFrom-Json
Check (@($manifest.files).Count -ge 3) 'manifest lists the stack'
$bad = @($manifest.files | Where-Object { ([string]$_.sha256) -notmatch '^[0-9a-f]{64}$' -or $_.bytes -le 0 -or $_.url -notmatch '^https://' })
Check ($bad.Count -eq 0) 'every file is https with a pinned 64-hex hash'
$presets = [IO.File]::ReadAllText((Join-Path $PackRoot 'presets\presets.json')) | ConvertFrom-Json
Check (@($presets.PSObject.Properties).Count -ge 35) 'presets parse as entries'

# SwarmUI selects samplers/schedulers by the names ComfyUI reports, so a preset
# must never carry a HaDeS-only alias like anima_beta57 - it would fail at
# generation time. RES4LYF names are fine because install fetches that pack.
$badNames = @($presets.PSObject.Properties | Where-Object {
  $pm = $_.Value.param_map
  ($pm.sampler -eq 'anima_beta57') -or ($pm.scheduler -eq 'anima_beta57') -or
  ($pm.scheduler -eq 'anima_lightning_tdm')
})
Check ($badNames.Count -eq 0) 'no preset uses a HaDeS-only sampler alias'
$beta57 = @($presets.PSObject.Properties | Where-Object { $_.Value.param_map.scheduler -eq 'beta57' })
Check ($beta57.Count -ge 1) 'beta57 presets exist for the RES4LYF path'

Remove-Item -Recurse -Force $fx -ErrorAction SilentlyContinue
if ($fail) { throw "$fail Anima check(s) failed" }
Write-Host 'ANIMA INSTALL GATE: PASS'
