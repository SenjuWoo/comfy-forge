<#
.SYNOPSIS
  One entry point for the Anima image stack: check it, install it, generate
  from a preset, merge checkpoints, and wire an agent to ComfyUI.

.DESCRIPTION
  comfy-forge keeps the Anima path on rails: `install` fetches everything that is
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
    python                        print the interpreter this stack uses (the one
                                 that has torch); exit 1 when none is found

  Dataset preparation is its own tool, TOOLS\dataset.ps1, because its arguments
  (--out, --mode, --force) would collide with the parameters of this script.

.EXAMPLE
  powershell -File TOOLS\image-stack.ps1 status
  powershell -File TOOLS\image-stack.ps1 generate -Prompt "neon rain city" -Preset "Anima - Author Daily (Euler + SGM)"
  powershell -File TOOLS\image-stack.ps1 merge -Donors "a.safetensors","b.safetensors" -Weights 0.85,0.75
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('status', 'install', 'presets', 'generate', 'merge', 'mcp', 'python', 'help')]
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
  [switch]$AcceptFallbackAnchor,
  [switch]$Write,
  [switch]$NoMirror,
  [string]$Agent = 'hermes',
  [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot          # repo root (this file lives in TOOLS\)
. (Join-Path $PSScriptRoot 'mirror-lib.ps1')      # hard-link/copy primitives
. (Join-Path $PSScriptRoot 'node-pack-lib.ps1')   # node-pack state + clone guard
# Standalone by default; a record left by the old bundle path is still honoured.
$StatePath = Join-Path $env:LOCALAPPDATA 'comfy-kit\anima-swarmui.json'
if (-not (Test-Path -LiteralPath $StatePath)) {
  $legacyState = Join-Path $env:LOCALAPPDATA 'Ultimate-AI-Starter-Bundle\anima-swarmui.json'
  if (Test-Path -LiteralPath $legacyState) { $StatePath = $legacyState }
}

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
  # Standalone installs (portable, venv or plain checkout) are not in the state
  # file, so look in the usual places before giving up.
  $candidates = @(
    (Join-Path $HOME 'ComfyUI'),
    (Join-Path $HOME 'Documents\ComfyUI'),
    'C:\ComfyUI',
    'C:\ComfyUI_windows_portable\ComfyUI',
    'D:\ComfyUI'
  )
  foreach ($c in $candidates) {
    if (Test-Frontend -Kind 'ComfyUI' -Root $c) { return $c }
  }
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
  # Returns a verified executable path, or $null. A bare name like 'python' is
  # not a path, so Test-Path on it is always false and every caller that used
  # one silently skipped its work; resolve through Get-Command instead.
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

  # No frontend interpreter. A ComfyUI root that exists without a python is a
  # broken install, not an invitation to use the user's global interpreter.
  if ($comfy) {
    Write-Host "warning    $comfy has no python interpreter; not falling back to the global python" -ForegroundColor Yellow
    return $null
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
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
  # Model category matters: ComfyUI's checkpoint loaders read `models/checkpoints`
  # and diffusion-only loaders read `models/diffusion_models`. Putting a full
  # checkpoint in diffusion_models leaves CheckpointLoaderSimple unable to see it.
  param([ValidateSet('checkpoint', 'diffusion')] [string]$Kind = 'diffusion')
  $leaf = if ($Kind -eq 'checkpoint') { 'checkpoints' } else { 'diffusion_models' }
  $roots = @()
  $swarm = Get-SwarmRoot
  if ($swarm) { $roots += (Join-Path $swarm 'dlbackend\comfy\ComfyUI') }
  $comfy = Get-ComfyRoot
  if ($comfy) { $roots += $comfy }
  foreach ($r in $roots) {
    $p = Join-Path $r (Join-Path 'models' $leaf)
    if (Test-Path -LiteralPath $p) { return $p }
    # Create the registered category folder when the tree exists but the folder
    # does not yet: ComfyUI reads whatever is under models/.
    $models = Join-Path $r 'models'
    if (Test-Path -LiteralPath $models) {
      try { New-Item -ItemType Directory -Path $p -Force | Out-Null; return $p } catch { }
    }
  }
  return $null
}

function Get-ComfyNodeDir {
  $swarm = Get-SwarmRoot
  if ($swarm) {
    # SwarmUI's Extensions folder is NOT ComfyUI's custom_nodes; a ComfyUI node
    # pack installed there registers nothing.
    foreach ($p in @((Join-Path $swarm 'dlbackend\comfy\ComfyUI\custom_nodes'))) {
      if (Test-Path -LiteralPath $p) { return $p }
    }
  }
  # Standalone ComfyUI: custom_nodes sits directly under its root.
  $comfy = Get-ComfyRoot
  if ($comfy) {
    $nodes = Join-Path $comfy 'custom_nodes'
    if (Test-Path -LiteralPath $nodes) { return $nodes }
  }
  return $null
}

# Custom node packs this kit relies on. RES4LYF is what puts beta57 / res_2s /
# rk_beta into ComfyUI's scheduler list, which SwarmUI then merges into its own
# Scheduler dropdown on startup (ComfyUIBackendExtension.AssignValuesFromRaw).
function Get-NodePacks {
  # The lock is the only list. A second hardcoded copy drifts silently, and a
  # missing lock must not degrade into "install whatever main has today".
  $lock = Get-DependencyLock
  if (-not $lock) { return @() }
  return @($lock.node_packs | ForEach-Object {
    @{ Name = $_.name; Url = $_.url; Sha = $_.sha
       Why = (@($_.provides) -join ', ') }
  })
}

function Get-PackDependencyReport([string]$Python, [string]$NodeDir) {
  # One resolver reads requirements.txt AND pyproject.toml (HaDeS ships only the
  # latter, which the old requirements.txt-only parser never saw). Native stderr
  # is captured without EAP=Stop turning an expected pip message into a crash.
  $script = Join-Path $Root 'TOOLS\check_deps.py'
  $packs = @(@(Get-NodePacks) | ForEach-Object { Join-Path $NodeDir $_.Name } |
             Where-Object { Test-Path -LiteralPath $_ })
  if ($packs.Count -eq 0) { return @() }
  # --report-only: the installer installs what is missing, so a nonzero
  # "requirements unmet" exit would be misread as a resolver failure.
  $argv = @($script, '--report-only')
  foreach ($p in $packs) { $argv += @('--pack', $p) }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $raw = & $Python @argv 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($code -ne 0) {
    Write-Host ("error      dependency resolution failed for {0}" -f $NodeDir) -ForegroundColor Red
    return $null
  }
  return ($raw | ConvertFrom-Json)
}

function Install-NodePackDeps([string]$Python) {
  # Installs only what the resolver reports missing, as argv (never a shell
  # string), into the frontend's own interpreter. torch is never replaced.
  $nodeDir = Get-ComfyNodeDir
  if (-not $nodeDir) { return 0 }
  $report = Get-PackDependencyReport $Python $nodeDir
  if ($null -eq $report) { return 1 }

  $failures = 0
  foreach ($pack in $report) {
    if (-not $pack.present) { continue }
    if ($pack.status -eq 'failed') {
      # A resolver that cannot check is not a resolver that passed.
      Write-Host ("error      {0}: {1}" -f $pack.pack, $pack.error) -ForegroundColor Red
      $failures++
      continue
    }
    foreach ($u in @($pack.unsupported)) {
      # Reported requirements that pip cannot replay are unresolved by
      # definition; saying "installed" here would be a false claim.
      Write-Host ("blocked    {0}: '{1}' cannot be installed automatically - install it yourself" -f $pack.pack, $u) -ForegroundColor Red
      $failures++
    }
    $missing = @($pack.missing)
    if ($missing.Count -eq 0) {
      if ($pack.requirements.Count -gt 0) { Ok ("deps verified -> {0}" -f $pack.pack) }
      continue
    }

    $safe = @()
    foreach ($r in $missing) {
      $name = (($r -split '[<>=!~;\[ ]')[0]).Trim().ToLower()
      if ($name -in @('torch', 'torchvision', 'torchaudio', 'xformers')) {
        # A node pack that wants a different torch build is a compatibility
        # question, not something the installer may decide alone.
        Write-Host ("blocked    {0} requires {1}; the installed CUDA torch is kept. Upgrade it yourself if the node needs it." -f $pack.pack, $r) -ForegroundColor Red
        $failures++
        continue
      }
      $safe += $r
    }
    if ($safe.Count -eq 0) { continue }

    Note ("installing for {0}: {1}" -f $pack.pack, ($safe -join ', '))
    & $Python @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check') @safe
    if ($LASTEXITCODE -ne 0) {
      Write-Host ("error      pip install failed for {0}" -f $pack.pack) -ForegroundColor Red
      $failures++
      continue
    }
    $after = Get-PackDependencyReport $Python $nodeDir
    if ($null -eq $after) {
      Write-Host ("error      {0}: post-install verification could not run" -f $pack.pack) -ForegroundColor Red
      $failures++
      continue
    }
    $entry = @($after | Where-Object { $_.pack -eq $pack.pack })[0]
    if ($entry.status -eq 'failed') {
      Write-Host ("error      {0}: {1}" -f $pack.pack, $entry.error) -ForegroundColor Red
      $failures++
      continue
    }
    $still = @($entry.missing)
    if ($still.Count -gt 0) {
      Write-Host ("error      still unsatisfied after install: {0}" -f ($still -join ', ')) -ForegroundColor Red
      $failures++
    } else {
      Ok ("deps installed -> {0}" -f $pack.pack)
    }
  }
  return $failures
}

# ---------------------------------------------------------------------------
# Discovery - read what the installer already recorded instead of re-scanning
# every drive. The installer owns detection; this only reads its result.
# ---------------------------------------------------------------------------
function Get-DependencyLock {
  # The tested revision of each external pack. Absent file means "unpinned", and
  # callers must say so rather than pretending the install is reproducible.
  $path = Join-Path $Root 'config\dependencies.lock.json'
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  try { return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json) }
  catch { Write-Host 'warning    config\dependencies.lock.json is not valid JSON' -ForegroundColor Yellow; return $null }
}

function Get-NodePackDrift {
  # Read-only. Never resets, cleans or checks out anything.
  $lock = Get-DependencyLock
  $dir = Get-ComfyNodeDir
  if (-not $lock -or -not $dir) { return @() }
  return @($lock.node_packs | ForEach-Object { Get-NodePackState $dir $_ })
}

function Test-NodePack([string]$Name) {
  # A directory is not an installation. Ask the one state evaluator: only a
  # checkout at the pinned commit, in the pinned repository, counts. `dirty`
  # still counts - it is the user's own working copy, not a broken install.
  $lock = Get-DependencyLock
  $dir = Get-ComfyNodeDir
  if (-not $lock -or -not $dir) { return $false }
  $pin = @($lock.node_packs | Where-Object { $_.name -eq $Name })[0]
  if (-not $pin) { return $false }
  $state = Get-NodePackState $dir $pin
  return ($state.State -eq 'locked' -or $state.State -eq 'dirty')
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

  # Find-Python returns $null when no interpreter exists, and Test-Path on
  # $null throws, so the check has to name the condition explicitly.
  foreach ($p in @(Get-NodePackDrift)) {
    $short = if ($p.Sha) { $p.Sha.Substring(0, 7) } else { '?' }
    $line = 'packs    {0} {1}' -f $p.Name, $p.State
    if ($p.State -eq 'locked') { Ok $line }
    elseif ($p.State -eq 'drift') { Note ($line + " ($short, lock pins " + $p.Pin.Substring(0, 7) + ')') }
    elseif ($p.Detail) { Note ($line + ' (' + $p.Detail + ')') }
    else { Note $line }
  }
  $rows += @{ Name = 'python'; Ok = [bool]($py -and (Test-Path -LiteralPath $py));
              Detail = $(if ($py) { $py } else { 'not found' });
              Fix = 'install ComfyUI or put python on PATH' }
  $rows += @{ Name = 'merge deps'; Ok = (Test-PythonDeps $py); Detail = 'torch, safetensors, numpy';
              Fix = 'image-stack.ps1 install' }
  $rows += @{ Name = 'node/npx'; Ok = ((Test-CommandExists 'node') -and (Test-CommandExists 'npx')); Detail = 'for the ComfyUI MCP server';
              Fix = 'install Node.js 18+' }
  foreach ($pack in @(Get-NodePacks)) {
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
  if ($py -and (Test-Path -LiteralPath $py)) {
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
  $cloneFailures = 0
  if (-not (Get-DependencyLock)) {
    # Without pins this install is not reproducible; refuse instead of cloning
    # whatever upstream main happens to be today.
    Write-Host 'error      config\dependencies.lock.json is missing or invalid; refusing to install unpinned node packs' -ForegroundColor Red
    $cloneFailures++
  }
  foreach ($pack in @($(if (Get-DependencyLock) { Get-NodePacks } else { @() }))) {
    if (Test-NodePack $pack.Name) { continue }
    if ($dir -and (Test-CommandExists 'git')) {
      $dest = Join-Path $dir $pack.Name
      Note ("installing node pack {0} ({1})" -f $pack.Name, $pack.Why)
      # A folder with no .git is a failed clone, not a working copy: move it
      # aside so the clone can proceed. A real checkout is never touched.
      $occupied = Clear-OccupiedPackDir $dest
      if ($occupied -eq 'checkout') {
        Write-Host ("error      {0}: a checkout exists but is not at the pinned commit; inspect or remove it yourself" -f $pack.Name) -ForegroundColor Red
        $cloneFailures++
        continue
      }
      if ($occupied -like 'aside:*') {
        Note ("moved an incomplete {0} to {1}" -f $pack.Name, (Split-Path -Leaf $occupied.Substring(6)))
      }
      # Clone then check out the locked commit: tracking upstream main means two
      # users installing the same release get different node behaviour.
      & git clone --quiet $pack.Url $dest
      if ($LASTEXITCODE -ne 0) {
        # A failed clone is a failed install, not a warning that vanishes from
        # the exit code.
        Write-Host ("error      {0}: clone failed" -f $pack.Name) -ForegroundColor Red
        $cloneFailures++
        continue
      }
      if ($LASTEXITCODE -eq 0) {
        $lock = Get-DependencyLock
        $pin = $null
        if ($lock) { $pin = @($lock.node_packs | Where-Object { $_.name -eq $pack.Name })[0] }
        if ($pin -and $pin.sha) {
          & git -C $dest checkout --quiet $pin.sha 2>$null
          $head = (& git -C $dest rev-parse HEAD 2>$null | Out-String).Trim()
          if ($LASTEXITCODE -ne 0 -or $head -ne $pin.sha) {
            # A failed checkout is a failed install; reporting success here is
            # how an unreachable pin looked fine for a whole release.
            Write-Host ("error      {0}: could not check out {1} (HEAD is {2})" -f $pack.Name, $pin.sha, $head) -ForegroundColor Red
            $cloneFailures++
            continue
          }
          # Verify by state, not by the commands having returned 0: a stray
          # .git or a foreign origin must not read as installed.
          $state = Get-NodePackState $dir $pin
          if ($state.State -eq 'locked' -or $state.State -eq 'dirty') {
            Ok ("node pack -> {0} @ {1}" -f $dest, $pin.sha.Substring(0, 7))
          } else {
            Write-Host ("error      {0}: checkout verification says '{1}'" -f $pack.Name, $state.State) -ForegroundColor Red
            $cloneFailures++
            continue
          }
        } else {
          Ok "node pack -> $dest (unpinned: no lock entry)"
        }
        $fixed++
      }
      else { Write-Host ("warning    {0} clone failed" -f $pack.Name) -ForegroundColor Yellow }
    } else {
      Write-Host 'warning    cannot install node packs: needs a ComfyUI custom_nodes folder and git' -ForegroundColor Yellow
      break
    }
  }
  $pyForDeps = Find-Python
  $depFailures = $cloneFailures
  if ($pyForDeps) { $depFailures += Install-NodePackDeps $pyForDeps }
  $packsAdded = @(@(Get-NodePacks) | Where-Object { -not (Test-NodePack $_.Name) }).Count -eq 0
  if ($packsAdded -and $fixed -gt 0) {
    Note 'restart SwarmUI so it re-reads the sampler/scheduler list from ComfyUI'
  }

  if (-not (Test-CommandExists 'node')) {
    Write-Host 'warning    Node.js is missing; the ComfyUI MCP server needs it (https://nodejs.org/)' -ForegroundColor Yellow
  }

  if ($fixed -eq 0) { Ok 'nothing to install' } else { Ok "$fixed component(s) installed" }
  if ($depFailures -gt 0) {
    # A node pack that cannot import is not an installed node pack.
    Write-Host ("error      {0} installation failure(s): node pack checkout or dependency set" -f $depFailures) -ForegroundColor Red
    exit 1
  }
}

function Invoke-Install {
  # With no SwarmUI there is nothing to install into. Saying "done" and exiting 0
  # hides that, so refuse and name the launcher the README asks for.
  if (-not (Get-SwarmRoot)) {
    Fail 'no SwarmUI install found - run launch-windows.bat in your SwarmUI folder first'
  }
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
  $reportPath = Join-Path ([IO.Path]::GetTempPath()) ("comfy-forge-merge-" + [guid]::NewGuid().ToString('N').Substring(0,8) + '.json')
  # One builder for every execution: the fallback retry used to rebuild this list
  # by hand and forget --report, so a full checkpoint was routed as UNET-only.
  $buildArgs = {
    param($Anchor)
    $a = @('--base', $Anchor, '--out', $Out, '--method', $Method, '--scale', $Scale,
           '--report', $reportPath)
    foreach ($d in $DonorPaths) { $a += @('--donor', $d) }
    if ($Weights) { $a += @('--weights', $Weights) }
    if ($Force) { $a += '--force' }
    return ,$a
  }
  $argv = & $buildArgs $Base

  Note ("merging {0} donor(s) onto {1}" -f $DonorPaths.Count, (Split-Path -Leaf $Base))
  & $python $mergeScript @argv
  $exit = $LASTEXITCODE

  # Exit 3 means the base is a different Anima lineage than the donors. Those
  # cannot be blended, and there is no verified ancestor to use instead.
  #
  # The only honest fallback is to treat one donor as the computational anchor
  # and KEEP IT IN THE DONOR LIST with its original weight: its task vector is
  # then zero, but its weight still counts in the denominator, so at scale 1 the
  # blend is exactly sum(w_i*D_i)/sum(w_i) - the recipe the caller asked for.
  # Dropping the anchor and renormalising over the rest silently rewrites the
  # recipe (31.5/27.8/22.2/18.5 becomes 0/40.5/32.4/27.0), so it is opt-in.
  if ($exit -eq 3 -and -not $explicitBase) {
    if (-not $AcceptFallbackAnchor) {
      $msg = ("the base {0} is a different Anima lineage from the donors, so they cannot be blended. " +
              "Pass -Base with a checkpoint from the donors' lineage, or -AcceptFallbackAnchor to anchor " +
              "on {1} while keeping all {2} donors and their weights.") -f `
              (Split-Path -Leaf $Base), (Split-Path -Leaf $DonorPaths[0]), $DonorPaths.Count
      Fail $msg
    }
    $Base = $DonorPaths[0]
    Say ''
    Note ("anchoring on {0}: a different lineage from the requested base, so it becomes both the anchor and donor #1" -f (Split-Path -Leaf $Base))
    Note 'all donors keep their original weights; the anchor contributes a zero task vector rather than being removed'
    $argv = & $buildArgs $Base
    & $python $mergeScript @argv
    $exit = $LASTEXITCODE
  }
  if ($exit -ne 0) { Fail "merge failed (python exit $exit)" }

  # Make the merge visible in ComfyUI too. A hard link costs no disk space and
  # keeps both trees in sync; a cross-volume fallback copies the file.
  $script:MergeWasFullCheckpoint = $false
  $report = $null
  if (Test-Path -LiteralPath $reportPath) {
    try { $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json } catch { $report = $null }
    Remove-Item -LiteralPath $reportPath -Force -ErrorAction SilentlyContinue
  }
  if ($null -eq $report -or -not $report.out -or -not $report.tensors) {
    # Guessing here is how a full checkpoint ends up in diffusion_models and
    # becomes invisible to CheckpointLoaderSimple. Keep the file, refuse to route.
    Write-Host ("error      the merge wrote {0}, but its report is missing or unreadable; not mirroring it anywhere. Re-run with the same arguments." -f $Out) -ForegroundColor Red
    exit 1
  }
  if ((Resolve-Path -LiteralPath $report.out -ErrorAction SilentlyContinue).Path -ne (Resolve-Path -LiteralPath $Out -ErrorAction SilentlyContinue).Path) {
    Write-Host ("error      merge report describes {0}, not {1}; not mirroring." -f $report.out, $Out) -ForegroundColor Red
    exit 1
  }
  # CheckpointLoaderSimple needs model + clip + vae. One of the two is a partial
  # checkpoint: usable by a diffusion loader, not by "load checkpoint".
  $hasEncoder = [bool]$report.roles.encoder
  $hasVae = [bool]$report.roles.vae
  if ($report.full_checkpoint -and -not ($hasEncoder -and $hasVae)) {
    Note ("checkpoint has {0}; treating it as diffusion-only" -f $(if ($hasEncoder) { 'an encoder but no VAE' } else { 'a VAE but no encoder' }))
  }
  $script:MergeWasFullCheckpoint = ($hasEncoder -and $hasVae)
  Note ("output is a {0} ({1} tensors)" -f $(if ($script:MergeWasFullCheckpoint) { 'full checkpoint' } else { 'diffusion-only model' }), $report.tensors)

  if (-not $NoMirror) {
    # A merge made from a full checkpoint carries encoder + VAE and is loadable
    # by CheckpointLoaderSimple; a UNET-only merge is not.
    $kind = if ($script:MergeWasFullCheckpoint) { 'checkpoint' } else { 'diffusion' }
    $comfyDir = Get-ComfyModelDir -Kind $kind
    if ($comfyDir) {
      $r = Set-ModelMirror -Source $Out -DestinationDirectory $comfyDir
      switch ($r.Kind) {
        'linked'    { Ok ("ComfyUI: " + $r.Message) }
        'in-place'  { Note ("ComfyUI: " + $r.Message) }
        'copied'    { Ok ("ComfyUI: " + $r.Message) }
        default     { Write-Host ("warning    ComfyUI: " + $r.Message) -ForegroundColor Yellow }
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
        # --comfyui-url is what the server actually honours; COMFYUI_HOST/PORT
        # are ignored and it falls back to 8188, which is rarely where SwarmUI's
        # backend lives. COMFYUI_URL alone was also ignored on 0.52.206.
        args    = @('-y', 'comfyui-mcp@0.52.206', '--comfyui-url', "http://127.0.0.1:$cp")
        env     = [ordered]@{
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
  'python'   {
    # Machine-readable on purpose: one line, the path, nothing else.
    $found = Find-Python
    if (-not $found) { exit 1 }
    Write-Output $found
  }
  'help'     { Get-Help $PSCommandPath -Detailed }
}
