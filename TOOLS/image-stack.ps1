<#
.SYNOPSIS
  One entry point for the Anima image stack: check it, install it, generate
  from a preset, merge checkpoints, and wire an agent to ComfyUI.

.DESCRIPTION
  comfy-kit keeps the Anima path on rails: `install` fetches everything that is
  missing (model files, the Anima node pack, the python merge modules), and the
  other verbs do one job each.

  This script is deliberately a thin orchestrator. It never re-implements model
  download (Ensure-AnimaSwarmUI.ps1 owns that) or merge math
  (TOOLS\merge_anima.py owns that). It finds what is installed, then calls the
  right thing and reports the real result.

  Verbs:
    status                        what is installed, running, and available
    install [-IncludeOptional]    fetch everything missing: models, node pack, deps
    presets [-Name <preset>]      list presets, or show one preset's settings
    generate -Prompt <text> [-Preset <name>] [-Model <name>]
                                 1024x1024 render through SwarmUI's API
    merge -Donors a,b [-Weights 0.85,0.75] [-Out <file>] [-Scale 1.0]
                                 4-way Anima checkpoint merge to a real file
    mcp [-Write <path>] [<agent>] print or write a ComfyUI MCP client config

.EXAMPLE
  powershell -File TOOLS\image-stack.ps1 status
  powershell -File TOOLS\image-stack.ps1 generate -Prompt "neon rain city" -Preset "Anima - Author Daily (Euler + SGM)"
  powershell -File TOOLS\image-stack.ps1 merge -Donors "a.safetensors","b.safetensors" -Weights 0.85,0.75
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('status', 'install', 'presets', 'generate', 'merge', 'mcp', 'help')]
  [string]$Verb = 'status',

  [string]$Preset = 'Anima - Author Daily (Euler + SGM)',
  [string]$Prompt = 'a weathered stone lighthouse on a basalt cliff at dawn, sea spray, volumetric light, cinematic',
  [string]$Model = 'Anima/Anima-2.9B-preview-v1.safetensors',
  [int]$Width = 1024,
  [int]$Height = 1024,
  [int]$Steps = 0,
  [double]$Cfg = 0,
  [int]$Seed = -1,
  [int]$Count = 1,
  [string]$Out = '',
  [string]$Name = '',
  [string[]]$Donors = @(),
  [string]$Weights = '',
  [string]$Base = '',
  [double]$Scale = 1.0,
  [string]$Method = 'task-arithmetic',
  [switch]$IncludeOptional,
  [switch]$Force,
  [switch]$Write,
  [switch]$NoMirror,
  [string]$Agent = 'hermes',
  [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot          # repo root (this file lives in TOOLS\)
$StatePath = Join-Path $env:LOCALAPPDATA 'Ultimate-AI-Starter-Bundle\anima-swarmui.json'

function Say($Message) { Write-Host $Message }
function Fail($Message) { Write-Host "error  $Message" -ForegroundColor Red; exit 1 }
function Ok($Message) { Write-Host "ok     $Message" -ForegroundColor Green }
function Note($Message) { Write-Host "..     $Message" }

# ---------------------------------------------------------------------------
# Discovery - read what the installer already recorded instead of re-scanning
# every drive. The installer owns detection; this only reads its result.
# ---------------------------------------------------------------------------
function Test-Frontend {
  # Same markers the installer uses, so a recorded path that has since been
  # moved or deleted is not trusted just because it is written in the state file.
  param([string]$Kind, [string]$Root)
  if (-not $Root -or -not (Test-Path -LiteralPath $Root)) { return $false }
  if ($Kind -eq 'SwarmUI') {
    return (Test-Path -LiteralPath (Join-Path $Root 'SwarmUI.sln')) -and
           (Test-Path -LiteralPath (Join-Path $Root 'Data\Backends.fds')) -and
           (Test-Path -LiteralPath (Join-Path $Root 'Models'))
  }
  return (Test-Path -LiteralPath (Join-Path $Root 'main.py')) -and
         (Test-Path -LiteralPath (Join-Path $Root 'comfy\samplers.py')) -and
         (Test-Path -LiteralPath (Join-Path $Root 'models'))
}

function Get-Frontends {
  if (-not (Test-Path -LiteralPath $StatePath)) { return @() }
  $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
  return @($state.frontends | Where-Object { Test-Frontend -Kind $_.kind -Root $_.root })
}

function Get-SwarmRoot {
  $swarm = Get-Frontends | Where-Object { $_.kind -eq 'SwarmUI' } | Select-Object -First 1
  if ($swarm) { return $swarm.root }
  if ($env:SWARMUI_ROOT -and (Test-Path -LiteralPath $env:SWARMUI_ROOT)) { return $env:SWARMUI_ROOT }
  return $null
}

function Get-ComfyRoot {
  $comfy = Get-Frontends | Where-Object { $_.kind -eq 'ComfyUI' } | Select-Object -First 1
  if ($comfy) { return $comfy.root }
  if ($env:COMFYUI_ROOT -and (Test-Path -LiteralPath $env:COMFYUI_ROOT)) { return $env:COMFYUI_ROOT }
  return $null
}

# SwarmUI's web port comes from its own settings file, not a guess.
function Get-SwarmPort {
  $swarm = Get-SwarmRoot
  if (-not $swarm) { return 7801 }
  $settings = Join-Path $swarm 'Data\Settings.fds'
  if (Test-Path -LiteralPath $settings) {
    $line = Select-String -LiteralPath $settings -Pattern '^\s*Port\s*:\s*(\d+)' | Select-Object -First 1
    if ($line) { return [int]$line.Matches[0].Groups[1].Value }
  }
  return 7801
}

function Test-SwarmUp([int]$P) {
  try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$P/API/GetNewSession" -Method Post `
      -ContentType 'application/json' -Body '{}' -TimeoutSec 4 -UseBasicParsing
    return $true
  }
  catch { return $false }
}

function New-SwarmSession([int]$P) {
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:$P/API/GetNewSession" -Method Post `
    -ContentType 'application/json' -Body '{}' -TimeoutSec 10 -UseBasicParsing
  return $r.session_id
}

# ComfyUI can be a standalone install (8188) or SwarmUI's own backend, which
# starts at BackendStartingPort (7820). Probe for /system_stats instead of
# assuming a port number.
function Get-ComfyUIPort {
  foreach ($p in @(8188) + (7820..7832)) {
    try {
      $r = Invoke-RestMethod -Uri "http://127.0.0.1:$p/system_stats" -TimeoutSec 2 -UseBasicParsing
      if ($r.system) { return $p }
    }
    catch { }
  }
  return 0
}

function Find-Python {
  $swarm = Get-SwarmRoot
  $candidates = @()
  if ($swarm) {
    $candidates += (Join-Path $swarm 'dlbackend\comfy\python_embeded\python.exe')
    $candidates += (Join-Path $swarm 'dlbackend\comfy\ComfyUI\venv\Scripts\python.exe')
  }
  $comfy = Get-ComfyRoot
  if ($comfy) {
    $candidates += (Join-Path $comfy 'venv\Scripts\python.exe')
    $candidates += (Join-Path (Split-Path -Parent $comfy) 'python_embeded\python.exe')
  }
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
  return 'python'   # last resort: whatever is on PATH
}

function Read-Presets {
  $path = Join-Path $Root 'presets\presets.json'
  if (-not (Test-Path -LiteralPath $path)) { Fail "missing $path" }
  return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
}

# SwarmUI rejects a literal VAE named "Automatic"; that preset value means
# "let the server choose", so it is dropped before the request goes out.
function Convert-PresetParams($paramMap) {
  $body = @{}
  foreach ($prop in $paramMap.PSObject.Properties) {
    if ([string]$prop.Value -eq 'Automatic') { continue }
    $body[$prop.Name] = $prop.Value
  }
  return $body
}

# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------
function Show-Status {
  Say '== image stack status =='
  $swarm = Get-SwarmRoot
  $comfy = Get-ComfyRoot
  if ($swarm) { Ok "SwarmUI  $swarm" } else { Note 'SwarmUI  not found (run install once it exists)' }
  $cp = Get-ComfyUIPort
  if ($comfy) { Ok "ComfyUI  $comfy (standalone)" }
  elseif ($cp) { Note "ComfyUI  none standalone; SwarmUI's backend answers on $cp" }
  else { Note 'ComfyUI  not found' }

  $presets = Read-Presets
  $animaCount = @($presets.PSObject.Properties | Where-Object { $_.Name -like 'Anima*' }).Count
  Ok ("presets  {0} total, {1} Anima" -f @($presets.PSObject.Properties).Count, $animaCount)

  if ($swarm) {
    $modelDir = Join-Path $swarm 'Models\Stable-Diffusion'
    $anima = @(Get-ChildItem -LiteralPath $modelDir -Recurse -Filter '*.safetensors' -ErrorAction SilentlyContinue)
    if ($anima.Count) {
      Ok ("models   {0} checkpoint(s) under Stable-Diffusion" -f $anima.Count)
      foreach ($m in $anima | Select-Object -First 8) { Say ("         {0}  {1:N1} GB" -f $m.Name, ($m.Length / 1GB)) }
    }
    else { Note 'models   no checkpoints yet' }
  }

  $port = if ($Port) { $Port } else { Get-SwarmPort }
  if (Test-SwarmUp $port) { Ok "server   SwarmUI answering on http://127.0.0.1:$port" }
  else {
    $launcher = if ($swarm) { Join-Path $swarm 'launch-windows.bat' } else { '<SwarmUI>\launch-windows.bat' }
    Note ("server   SwarmUI is not running - start it with {0}" -f $launcher)
  }

  $cp = Get-ComfyUIPort
  if ($cp) { Ok "comfyui  ComfyUI API on port $cp (MCP target)" } else { Note 'comfyui  no ComfyUI API port answered' }

  Show-Requirements
}

# ---------------------------------------------------------------------------
# Requirements - everything the kit needs beyond the model files. `status`
# reports them, `install` fixes every one it can without changing an existing
# ComfyUI install's own packages.
# ---------------------------------------------------------------------------
function Get-ComfyModelDir {
  # ComfyUI's own model tree. SwarmUI keeps Models\Stable-Diffusion; a raw
  # ComfyUI install (and the HaDeS workflow) reads this folder instead, so a
  # merge has to exist in both places to be pickable everywhere.
  $candidates = @()
  $swarm = Get-SwarmRoot
  if ($swarm) { $candidates += (Join-Path $swarm 'dlbackend\comfy\ComfyUI\models\diffusion_models') }
  $comfy = Get-ComfyRoot
  if ($comfy) { $candidates += (Join-Path $comfy 'models\diffusion_models') }
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
  return $null
}

function Get-ComfyNodeDir {
  $swarm = Get-SwarmRoot
  if (-not $swarm) { return $null }
  foreach ($p in @((Join-Path $swarm 'dlbackend\comfy\ComfyUI\custom_nodes'),
                   (Join-Path $swarm 'Extensions'))) {
    if (Test-Path -LiteralPath $p) { return $p }
  }
  return $null
}

# Custom node packs this kit relies on. RES4LYF is what puts beta57 / res_2s /
# rk_beta into ComfyUI's scheduler list, which SwarmUI then merges into its own
# Scheduler dropdown on startup (ComfyUIBackendExtension.AssignValuesFromRaw).
$NodePacks = @(
  @{ Name = 'hades-anima-suite'
     Url  = 'https://github.com/Devildonia/HaDeS-Anima-Suite.git'
     Why  = 'HaDeS Anima nodes: 4-way merge graph, anima_beta57 scheduler' },
  @{ Name = 'RES4LYF'
     Url  = 'https://github.com/ClownsharkBatwing/RES4LYF.git'
     Why  = 'extra samplers/schedulers: beta57, res_2s, rk_beta, bong_tangent' }
)

function Test-NodePack([string]$Name) {
  $dir = Get-ComfyNodeDir
  if (-not $dir) { return $false }
  return (Test-Path -LiteralPath (Join-Path $dir $Name))
}

function Test-PythonDeps([string]$Python) {
  if (-not $Python) { return $false }
  & $Python -c "import torch, safetensors, numpy" 2>$null | Out-Null
  return ($LASTEXITCODE -eq 0)
}

function Test-CommandExists([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Show-Requirements {
  $py = Find-Python
  $rows = @()

  $rows += @{ Name = 'python'; Ok = (Test-Path -LiteralPath $py); Detail = $py;
              Fix = 'install ComfyUI or put python on PATH' }
  $rows += @{ Name = 'merge deps'; Ok = (Test-PythonDeps $py); Detail = 'torch, safetensors, numpy';
              Fix = 'image-stack.ps1 install' }
  $rows += @{ Name = 'node/npx'; Ok = ((Test-CommandExists 'node') -and (Test-CommandExists 'npx')); Detail = 'for the ComfyUI MCP server';
              Fix = 'install Node.js 18+' }
  foreach ($pack in $NodePacks) {
    $rows += @{ Name = 'nodes'; Ok = (Test-NodePack $pack.Name); Detail = $pack.Why;
                Fix = 'image-stack.ps1 install' }
  }
  $rows += @{ Name = 'git'; Ok = (Test-CommandExists 'git'); Detail = 'for node-pack installs';
              Fix = 'install Git' }

  foreach ($r in $rows) {
    if ($r.Ok) { Ok ("{0,-11} {1}" -f $r.Name, $r.Detail) }
    else { Write-Host ("missing    {0,-11} {1}  -> {2}" -f $r.Name, $r.Detail, $r.Fix) -ForegroundColor Yellow }
  }
}

function Install-MissingRequirements {
  $fixed = 0

  # Python merge dependencies. torch is never replaced: ComfyUI ships a CUDA
  # build that must match its own setup, so only the small modules are added.
  $py = Find-Python
  if (Test-Path -LiteralPath $py) {
    $missing = @()
    foreach ($mod in @('torch', 'safetensors', 'numpy')) {
      & $py -c "import $mod" 2>$null | Out-Null
      if ($LASTEXITCODE -ne 0) { $missing += $mod }
    }
    if ($missing -contains 'torch') {
      Write-Host 'note       torch is missing from the ComfyUI python; install ComfyUI/its requirements first' -ForegroundColor Yellow
    }
    $pip = @($missing | Where-Object { $_ -ne 'torch' })
    if ($pip.Count) {
      Note ("installing python modules: " + ($pip -join ', '))
      & $py -m pip install --quiet --disable-pip-version-check @pip
      if ($LASTEXITCODE -eq 0) { Ok 'python modules installed'; $fixed++ }
      else { Write-Host 'warning    pip install failed; merges will not run' -ForegroundColor Yellow }
    }
  }
  else { Write-Host 'warning    no python found; merges are unavailable' -ForegroundColor Yellow }

  # Node packs. The CLI merge does not need them, but the ComfyUI workflow does,
  # and RES4LYF is what makes beta57 selectable in SwarmUI.
  $dir = Get-ComfyNodeDir
  foreach ($pack in $NodePacks) {
    if (Test-NodePack $pack.Name) { continue }
    if ($dir -and (Test-CommandExists 'git')) {
      $dest = Join-Path $dir $pack.Name
      Note ("installing node pack {0} ({1})" -f $pack.Name, $pack.Why)
      & git clone --depth 1 --quiet $pack.Url $dest
      if ($LASTEXITCODE -eq 0) { Ok "node pack -> $dest"; $fixed++ }
      else { Write-Host ("warning    {0} clone failed" -f $pack.Name) -ForegroundColor Yellow }
    } else {
      Write-Host 'warning    cannot install node packs: needs a ComfyUI custom_nodes folder and git' -ForegroundColor Yellow
      break
    }
  }
  $packsAdded = @($NodePacks | Where-Object { -not (Test-NodePack $_.Name) }).Count -eq 0
  if ($packsAdded -and $fixed -gt 0) {
    Note 'restart SwarmUI so it re-reads the sampler/scheduler list from ComfyUI'
  }

  if (-not (Test-CommandExists 'node')) {
    Write-Host 'warning    Node.js is missing; the ComfyUI MCP server needs it (https://nodejs.org/)' -ForegroundColor Yellow
  }

  if ($fixed -eq 0) { Ok 'nothing to install' } else { Ok "$fixed component(s) installed" }
}

function Invoke-Install {
  $script = Join-Path $PSScriptRoot 'Ensure-AnimaSwarmUI.ps1'
  if (-not (Test-Path -LiteralPath $script)) { Fail "missing $script" }
  $cliArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script)
  if ($IncludeOptional) { $cliArgs += '-IncludeOptional' }
  & powershell @cliArgs
  if ($LASTEXITCODE -ne 0) { Fail "installer exited $LASTEXITCODE" }
  Write-Host ''
  Say '== remaining requirements =='
  Install-MissingRequirements
}

function Show-Presets {
  $presets = Read-Presets
  if ($Name) {
    $entry = $presets.PSObject.Properties | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if (-not $entry) { Fail "no preset named '$Name' (run: image-stack.ps1 presets)" }
    Say $entry.Name
    Say ("  " + $entry.Value.description)
    foreach ($p in $entry.Value.param_map.PSObject.Properties) { Say ("  {0,-18} {1}" -f $p.Name, $p.Value) }
    return
  }
  Say ("{0} presets:" -f @($presets.PSObject.Properties).Count)
  foreach ($p in $presets.PSObject.Properties) { Say ("  " + $p.Name) }
}

function Invoke-Generate {
  $swarm = Get-SwarmRoot
  if (-not $swarm) { Fail 'no SwarmUI install found; see: image-stack.ps1 install' }
  $port = if ($Port) { $Port } else { Get-SwarmPort }
  if (-not (Test-SwarmUp $port)) {
    Fail "SwarmUI is not running on port $port - start $(Join-Path $swarm 'launch-windows.bat') and retry"
  }

  $presets = Read-Presets
  $entry = $presets.PSObject.Properties | Where-Object { $_.Name -eq $Preset } | Select-Object -First 1
  if (-not $entry) { Fail "no preset named '$Preset' (run: image-stack.ps1 presets)" }

  $body = Convert-PresetParams $entry.Value.param_map
  $body['session_id'] = New-SwarmSession $port
  $body['prompt'] = $Prompt
  $body['images'] = $Count
  $body['model'] = $Model
  $body['width'] = $Width
  $body['height'] = $Height
  $body['seed'] = $Seed
  if ($Steps -gt 0) { $body['steps'] = $Steps }
  if ($Cfg -gt 0) { $body['cfgscale'] = $Cfg }

  $json = $body | ConvertTo-Json -Depth 6 -Compress
  Note ("preset {0} -> {1}x{2}, {3} image(s)" -f $Preset, $Width, $Height, $Count)
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/API/GenerateText2Image" -Method Post `
    -ContentType 'application/json' -Body $json -TimeoutSec 900 -UseBasicParsing
  if ($r.error) { Fail ("SwarmUI refused: " + $r.error) }

  if (-not $Out) { $Out = Join-Path $Root 'out' }
  New-Item -ItemType Directory -Force -Path $Out | Out-Null
  foreach ($rel in $r.images) {
    $file = Join-Path $Out (Split-Path -Leaf $rel)
    # SwarmUI returns paths like "View/local/raw/<date>/<file>.png"; older
    # builds return the bare "local/raw/..." path. Handle both.
    $viewPath = if ($rel -like 'View/*') { $rel } else { "View/$rel" }
    $parts = @($viewPath -split '/' | ForEach-Object { [uri]::EscapeDataString($_) })
    $url = "http://127.0.0.1:$port/" + ($parts -join '/')
    Invoke-WebRequest -Uri $url -OutFile $file -TimeoutSec 300 -UseBasicParsing | Out-Null
    Ok ("wrote {0}  ({1:N0} KB)" -f $file, ((Get-Item -LiteralPath $file).Length / 1KB))
  }
}

function Invoke-Merge {
  # Accept -Donors a,b as well as -Donors a,b,c: some shells (and copy-paste
  # from a comma list) deliver the whole list as one string.
  $DonorPaths = @($Donors | ForEach-Object { $_ -split ',' } |
                  ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ })
  if ($DonorPaths.Count -lt 1) { Fail 'merge needs -Donors (one path per checkpoint, up to 4)' }
  # Remember whether the caller chose a base before the default fills it in:
  # a default base must not block the same-lineage retry below.
  $explicitBase = [bool]$Base
  $swarm = Get-SwarmRoot
  if (-not $Base) {
    if (-not $swarm) { Fail 'merge needs -Base, or a SwarmUI install to find Anima-2.9B-preview-v1' }
    $Base = Join-Path $swarm 'Models\Stable-Diffusion\Anima\Anima-2.9B-preview-v1.safetensors'
  }
  if (-not (Test-Path -LiteralPath $Base)) { Fail "base checkpoint not found: $Base" }
  foreach ($d in $DonorPaths) { if (-not (Test-Path -LiteralPath $d)) { Fail "donor checkpoint not found: $d" } }

  if (-not $Out) {
    $dir = if ($swarm) { Join-Path $swarm 'Models\Stable-Diffusion\Anima' } else { Join-Path $Root 'out' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Out = Join-Path $dir ("Anima-4way-{0}.safetensors" -f $stamp)
  }

  $python = Find-Python
  $mergeScript = Join-Path $PSScriptRoot 'merge_anima.py'
  $argv = @('--base', $Base, '--out', $Out, '--method', $Method, '--scale', $Scale)
  foreach ($d in $DonorPaths) { $argv += @('--donor', $d) }
  if ($Weights) { $argv += @('--weights', $Weights) }
  if ($Force) { $argv += '--force' }

  Note ("merging {0} donor(s) onto {1}" -f $DonorPaths.Count, (Split-Path -Leaf $Base))
  & $python $mergeScript @argv
  $exit = $LASTEXITCODE

  # Exit 3 means the base is a different Anima lineage than the donors (e.g. the
  # 40-block 2.9B base against 28-block fine-tunes). Those cannot be blended, so
  # retry with the first donor as the anchor, which is what a same-lineage merge
  # actually means. An explicit -Base is never overridden.
  if ($exit -eq 3 -and -not $explicitBase) {
    $rest = @($DonorPaths | Select-Object -Skip 1)
    if ($rest.Count -lt 1) { Fail 'the donors do not share a lineage with the base, and there is no second donor to anchor on' }
    $w = $null
    if ($Weights) {
      $vals = @($Weights -split ',' | ForEach-Object { $_.Trim() })
      if ($vals.Count -eq ($rest.Count + 1)) { $w = ($vals[1..($vals.Count - 1)] -join ',') }
      elseif ($vals.Count -eq $rest.Count) { $w = ($vals -join ',') }
    }
    $Base = $DonorPaths[0]
    Say ''
    Note ("anchoring on {0} instead (same lineage as the other donors)" -f (Split-Path -Leaf $Base))
    $argv = @('--base', $Base, '--out', $Out, '--method', $Method, '--scale', $Scale)
    foreach ($d in $rest) { $argv += @('--donor', $d) }
    if ($w) { $argv += @('--weights', $w) }
    if ($Force) { $argv += '--force' }
    & $python $mergeScript @argv
    $exit = $LASTEXITCODE
  }
  if ($exit -ne 0) { Fail "merge failed (python exit $exit)" }

  # Make the merge visible in ComfyUI too. A hard link costs no disk space and
  # keeps both trees in sync; a cross-volume fallback copies the file.
  if (-not $NoMirror) {
    $comfyDir = Get-ComfyModelDir
    if ($comfyDir) {
      $mirror = Join-Path $comfyDir (Split-Path -Leaf $Out)
      try {
        if (Test-Path -LiteralPath $mirror) { Remove-Item -LiteralPath $mirror -Force }
        # cmd's mklink is used rather than New-Item -ItemType HardLink: the PS
        # cmdlet has been seen to report success while producing a separate copy.
        $null = & cmd.exe /c "mklink /H `"$mirror`" `"$Out`"" 2>&1
        # Verify instead of trusting the exit code: fsutil lists every path that
        # shares the file ID, so both names must appear.
        $links = (& cmd.exe /c "fsutil hardlink list `"$mirror`"" 2>$null) -join ' '
        if (-not ($links -match [regex]::Escape((Split-Path -Leaf $Out)))) {
          throw 'hard link did not take'
        }
        Ok "linked into ComfyUI: $mirror"
      }
      catch {
        Copy-Item -LiteralPath $Out -Destination $mirror -Force
        Ok "copied into ComfyUI: $mirror"
      }
    }
  }

  if ($swarm -and $Out.StartsWith($swarm, [StringComparison]::OrdinalIgnoreCase)) {
    Note 'refreshing the SwarmUI model list so the merge appears in the UI'
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$(Get-SwarmPort)/API/TriggerRefresh" -Method Post `
            -ContentType 'application/json' -Body (@{ session_id = (New-SwarmSession (Get-SwarmPort)) } | ConvertTo-Json) `
            -TimeoutSec 30 -UseBasicParsing | Out-Null } catch { Note 'SwarmUI not running; refresh happens on next start' }
    Ok ("model appears as: " + [IO.Path]::GetFileNameWithoutExtension($Out))
  }
}

function Show-Mcp {
  $cp = if ($Port) { $Port } else { Get-ComfyUIPort }
  if (-not $cp) { $cp = 8188 }

  # Compact mode registers 3 meta-tools instead of ~200 schemas: the right
  # choice for a chat agent, and the only sane one for a local model.
  $config = [ordered]@{
    mcpServers = [ordered]@{
      'comfyui' = [ordered]@{
        command = 'npx'
        args    = @('-y', 'comfyui-mcp@0.52.206')
        env     = [ordered]@{
          COMFYUI_HOST       = '127.0.0.1'
          COMFYUI_PORT       = "$cp"
          COMFYUI_MCP_TOOL_MODE = 'compact'
        }
      }
    }
  }
  $json = $config | ConvertTo-Json -Depth 8
  if (-not $Write) {
    Say '# ComfyUI MCP client config (comfyui-mcp 0.52.206, compact mode)'
    Say ("# target: http://127.0.0.1:{0}/" -f $cp)
    Say $json
    Say ''
    Say '# write it with:  image-stack.ps1 mcp -Write -Agent hermes'
    return
  }
  $target = switch ($Agent) {
    'hermes' { Join-Path $env:LOCALAPPDATA 'hermes\config.yaml' }
    'claude' { Join-Path $HOME '.claude.json' }
    'codex'  { Join-Path $HOME '.codex\config.toml' }
    default  { $Agent }
  }
  if ($Agent -in @('hermes', 'codex')) {
    Note "Hermes and Codex do not read a JSON mcpServers block; add this to $target by hand:"
    Say $json
    return
  }
  if (-not (Test-Path -LiteralPath $target)) {
    $config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $target -Encoding UTF8
    Ok "created $target with the comfyui server (port $cp)"
    return
  }
  # Existing file: add or replace only the "comfyui" entry, keep everything else.
  $existing = Get-Content -LiteralPath $target -Raw | ConvertFrom-Json
  if (-not $existing.PSObject.Properties['mcpServers']) {
    $existing | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
  }
  $existing.mcpServers | Add-Member -NotePropertyName comfyui -NotePropertyValue $config.mcpServers.comfyui -Force
  $existing | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $target -Encoding UTF8
  Ok "wrote comfyui server into $target (port $cp)"
}

switch ($Verb) {
  'status'   { Show-Status }
  'install'  { Invoke-Install }
  'presets'  { Show-Presets }
  'generate' { Invoke-Generate }
  'merge'    { Invoke-Merge }
  'mcp'      { Show-Mcp }
  'help'     { Get-Help $PSCommandPath -Detailed }
}
