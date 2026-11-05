<#
.SYNOPSIS
  Install the Anima max-generation stack into a detected SwarmUI and/or ComfyUI.
.DESCRIPTION
  Locates a SwarmUI install (a folder holding SwarmUI.sln plus Data\Backends.fds)
  and/or a ComfyUI install (a folder holding main.py plus comfy\samplers.py), then
  downloads the pinned Anima model files from anima-manifest.json into each
  frontend's model tree.

  Files already present with a matching SHA-256 are never re-downloaded, whatever
  their filename or subfolder, so a re-run costs nothing. The manifest's aliases
  (for example qwen_3_600m.safetensors for qwen_3_06b_base.safetensors) count as
  the same bytes.

  Also copies the SwarmUI presets next to the install for import, and writes an
  installed-state record under LOCALAPPDATA\Ultimate-AI-Starter-Bundle.

  A quiet no-op when neither frontend is found, so it is safe to run from
  Ensure-Tools on machines that do not use a image front end.
#>
[CmdletBinding()]
param(
  [switch]$CheckOnly,
  [switch]$IncludeOptional,
  [switch]$Offline,
  [switch]$Force,
  [string]$SwarmUIRoot,
  [string]$ComfyUIRoot,
  [string[]]$SearchRoot,
  [switch]$Refresh,
  [switch]$NoKnownRoots
)

$ErrorActionPreference = 'Stop'

# --- helpers ---------------------------------------------------------------
# Prefer the pack's shared helpers when present; define minimal fallbacks so
# this script also works when copied out on its own.
$common = Join-Path $PSScriptRoot 'UABS-Common.ps1'
if (Test-Path -LiteralPath $common) {
  . $common
} else {
  function Write-UabsStep([string]$m)  { Write-Host ("==> " + $m) -ForegroundColor Cyan }
  function Write-UabsOk([string]$m)    { Write-Host ("  OK  " + $m) -ForegroundColor Green }
  function Write-UabsInfo([string]$m)  { Write-Host ("  ..  " + $m) -ForegroundColor DarkGray }
  function Write-UabsWarn([string]$m)  { Write-Host ("  !!  " + $m) -ForegroundColor Yellow }
  function Write-UabsBad([string]$m)   { Write-Host ("  XX  " + $m) -ForegroundColor Red }
  function Get-UabsStateRoot {
    $base = $env:LOCALAPPDATA; if (-not $base) { $base = $env:TEMP }
    return (Join-Path $base 'Ultimate-AI-Starter-Bundle')
  }
  function Get-UabsPackRoot {
    $here = $PSScriptRoot
    $p = $here
    for ($i = 0; $i -lt 4 -and $p; $i++) {
      if (Test-Path -LiteralPath (Join-Path $p 'BUNDLED-TOOLS\CATALOG.json')) { return $p }
      $p = Split-Path -Parent $p
    }
    return $null
  }
  function Test-UabsPathWithin {
    param([string]$Path, [string]$Root)
    if (-not $Path -or -not $Root) { return $false }
    try {
      $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
      $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
      return $fullPath.Equals($fullRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.StartsWith($fullRoot + '\', [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
  }
}

function Test-UabsSwarmUIRoot([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  return (Test-Path -LiteralPath (Join-Path $Path 'SwarmUI.sln') -PathType Leaf) -and
         (Test-Path -LiteralPath (Join-Path $Path 'Data\Backends.fds') -PathType Leaf) -and
         (Test-Path -LiteralPath (Join-Path $Path 'Models') -PathType Container)
}

function Test-UabsComfyUIRoot([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  return (Test-Path -LiteralPath (Join-Path $Path 'main.py') -PathType Leaf) -and
         (Test-Path -LiteralPath (Join-Path $Path 'comfy\samplers.py') -PathType Leaf) -and
         (Test-Path -LiteralPath (Join-Path $Path 'models') -PathType Container)
}

function Get-UabsSearchRoots {
  # Bounded list. The installer must never walk a whole drive; the last good
  # root is also cached in state, so discovery normally costs one Test-Path.
  $candidates = @($SearchRoot)
  if (-not $NoKnownRoots) { $candidates += @(
    (Join-Path $env:USERPROFILE 'SwarmUI'),
    (Join-Path $env:USERPROFILE 'ComfyUI'),
    (Join-Path $env:USERPROFILE 'Desktop\SwarmUI'),
    (Join-Path $env:USERPROFILE 'Desktop\ComfyUI'),
    (Join-Path $env:USERPROFILE 'Downloads\SwarmUI'),
    (Join-Path $env:USERPROFILE 'Downloads\ComfyUI'),
    (Join-Path $env:LOCALAPPDATA 'Programs\SwarmUI'),
    'C:\SwarmUI', 'C:\ComfyUI', 'C:\AI\SwarmUI', 'C:\AI\ComfyUI',
    'S:\Apps\SwarmUI', 'S:\Apps\ComfyUI', 'S:\Misc\SwarmUI', 'S:\Misc\ComfyUI'
  ) }
  $roots = @()
  foreach ($p in $candidates) {
    if ([string]::IsNullOrWhiteSpace($p)) { continue }
    if (-not (Test-Path -LiteralPath $p -PathType Container)) { continue }
    $full = [IO.Path]::GetFullPath($p)
    if ($roots -notcontains $full) { $roots += $full }
  }
  return @($roots)
}

function Get-UabsCachedFrontends {
  # The last successful run records where the frontends live; re-verify the
  # markers and skip discovery entirely when they still hold.
  $path = Join-Path (Get-UabsStateRoot) 'anima-swarmui.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
  try { $state = [IO.File]::ReadAllText($path) | ConvertFrom-Json } catch { return $null }
  $swarm = @(); $comfy = @()
  foreach ($f in @($state.frontends)) {
    if (-not $f.root) { continue }
    if ($f.kind -eq 'SwarmUI' -and (Test-UabsSwarmUIRoot $f.root)) { $swarm += $f.root }
    if ($f.kind -eq 'ComfyUI' -and (Test-UabsComfyUIRoot $f.root)) { $comfy += $f.root }
  }
  if (-not $swarm.Count -and -not $comfy.Count) { return $null }

  return [pscustomobject]@{ SwarmUI = @($swarm); ComfyUI = @($comfy) }
}

function Find-UabsFrontendRoots {
  $swarm = @(); $comfy = @()

  # 1. Explicit overrides win.
  foreach ($scope in @('Process', 'User')) {
    $s = [Environment]::GetEnvironmentVariable('SWARMUI_ROOT', $scope)
    if ($s -and (Test-UabsSwarmUIRoot $s) -and $swarm -notcontains $s) { $swarm += $s }
    $c = [Environment]::GetEnvironmentVariable('COMFYUI_ROOT', $scope)
    if ($c -and (Test-UabsComfyUIRoot $c) -and $comfy -notcontains $c) { $comfy += $c }
  }

  # 2. Check each bounded root itself, then its depth-2 children by name.
  foreach ($root in @(Get-UabsSearchRoots)) {
    if ((Test-UabsSwarmUIRoot $root) -and $swarm -notcontains $root) { $swarm += $root }
    if ((Test-UabsComfyUIRoot $root) -and $comfy -notcontains $root) { $comfy += $root }
    try {
      $dirs = @(Get-ChildItem -LiteralPath $root -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq 'SwarmUI' -or $_.Name -ieq 'ComfyUI' })
      foreach ($d in $dirs) {
        if ($d.Name -ieq 'SwarmUI') { if ((Test-UabsSwarmUIRoot $d.FullName) -and $swarm -notcontains $d.FullName) { $swarm += $d.FullName } }
        else { if ((Test-UabsComfyUIRoot $d.FullName) -and $comfy -notcontains $d.FullName) { $comfy += $d.FullName } }
      }
    } catch { }
  }

  # 3. A SwarmUI install owns an internal ComfyUI backend.
  foreach ($sroot in @($swarm)) {
    try {
      $inner = @(Get-ChildItem -LiteralPath $sroot -Directory -Recurse -Depth 4 -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq 'ComfyUI' })
      foreach ($d in $inner) { if ((Test-UabsComfyUIRoot $d.FullName) -and $comfy -notcontains $d.FullName) { $comfy += $d.FullName } }
    } catch { }
  }

  # SwarmUI's own backend is not a second frontend: its model tree is shared,
  # so installing into both would duplicate multi-GB files.
  $standalone = @()
  foreach ($c in @($comfy)) {
    $nested = $false
    foreach ($s in @($swarm)) { if ($c.StartsWith($s, [StringComparison]::OrdinalIgnoreCase)) { $nested = $true; break } }
    if (-not $nested) { $standalone += $c }
  }
  $comfy = @($standalone)
  return [pscustomobject]@{ SwarmUI = @($swarm); ComfyUI = @($comfy) }
}

function Get-UabsSwarmUIModelRoot([string]$SwarmRoot) {
  # SwarmUI's own layout comes from Data\Settings.fds; never guess it.
  $root = 'Models'; $sd = 'Stable-Diffusion'; $vae = 'VAE'; $clip = 'text_encoders'
  $settings = Join-Path $SwarmRoot 'Data\Settings.fds'
  if (Test-Path -LiteralPath $settings -PathType Leaf) {
    foreach ($line in [IO.File]::ReadAllLines($settings)) {
      $t = $line.Trim()
      if ($t.StartsWith('ModelRoot:'))       { $root = $t.Substring(10).Trim() }
      elseif ($t.StartsWith('SDModelFolder:'))   { $sd = $t.Substring(14).Trim() }
      elseif ($t.StartsWith('SDVAEFolder:'))     { $vae = $t.Substring(12).Trim() }
      elseif ($t.StartsWith('SDClipFolder:'))    { $clip = $t.Substring(13).Trim() }
    }
  }
  $root = ($root -split ';')[0].Trim()
  $sd = ($sd -split ';')[0].Trim()
  $vae = ($vae -split ';')[0].Trim()
  $clip = ($clip -split ';')[0].Trim()
  if (-not [IO.Path]::IsPathRooted($root)) { $root = Join-Path $SwarmRoot $root }
  return [pscustomobject]@{
    Diffusion = (Join-Path (Join-Path $root $sd) 'Anima')
    TextEncoder = (Join-Path $root $clip)
    VAE = (Join-Path $root $vae)
  }
}

function Get-UabsComfyUIModelRoot([string]$ComfyRoot) {
  $models = Join-Path $ComfyRoot 'models'
  return [pscustomobject]@{
    Diffusion = (Join-Path $models 'diffusion_models')
    TextEncoder = (Join-Path $models 'text_encoders')
    VAE = (Join-Path $models 'vae')
  }
}

function Find-UabsExistingModelFile {
  param([string]$Dir, [string[]]$Names, [long]$Bytes)
  # Any file under the target directory, at any depth, with the same byte length.
  if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return $null }
  $hits = @(Get-ChildItem -LiteralPath $Dir -File -Recurse -Depth 3 -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -eq $Bytes })
  foreach ($h in $hits) {
    if ($Names -contains $h.Name) { return $h.FullName }
  }
  return $null
}

function Test-UabsFileHash {
  param([string]$Path, [string]$Sha256)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
  $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
  return ($actual -ieq ([string]$Sha256).ToLowerInvariant())
}

function Save-UabsModelFile {
  param([string]$Url, [string]$Destination, [long]$ExpectedBytes, [string]$ExpectedSha256, [bool]$Offline)
  $dir = Split-Path -Parent $Destination
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $part = $Destination + '.part'
  if ($Offline) { throw ('offline mode: cannot download ' + (Split-Path -Leaf $Destination)) }

  $curl = $null
  foreach ($cand in @(Get-Command curl.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object Source)) {
    if ($cand) { $curl = $cand; break }
  }
  Write-UabsStep ('Download ' + (Split-Path -Leaf $Destination))
  if ($curl) {
    # -C - resumes a partial file; -L follows HF's redirect to the CDN.
    # Stderr goes to a FILE, never the pipeline: under
    # $ErrorActionPreference='Stop' a 2>&1 merge turns curl's progress lines
    # into terminating NativeCommandError records and aborts the download.
    # Keeping the call un-piped also means $LASTEXITCODE is curl's own.
    #
    # -sS matters just as much: without -s curl writes its progress meter
    # ("% Total % Received ...") to stderr, and under 'Stop' that first line
    # alone throws a RemoteException that kills a perfectly good download.
    # -sS keeps the meter off while still surfacing genuine errors.
    $errLog = Join-Path ([IO.Path]::GetTempPath()) ('uabs-curl-' + [guid]::NewGuid().ToString('N') + '.log')
    try {
      & $curl -sS -L --fail -C - --output $part $Url 2> $errLog
      $code = [int]$LASTEXITCODE
      if ($code -ne 0) {
        $tail = ''
        if (Test-Path -LiteralPath $errLog) { $tail = ((Get-Content -LiteralPath $errLog -Tail 3) -join ' | ') }
        throw ('curl failed (exit ' + $code + ') downloading ' + $Url + ' ' + $tail)
      }
      if (Test-Path -LiteralPath $errLog) {
        $lastLine = Get-Content -LiteralPath $errLog -Tail 1
        if ($lastLine) { Write-UabsInfo ([string]$lastLine) }
      }
    } finally {
      Remove-Item -LiteralPath $errLog -Force -ErrorAction SilentlyContinue
    }
  } else {
    $headers = @{ 'User-Agent' = 'Ultimate-AI-Starter-Bundle-Anima' }
    Invoke-WebRequest -Uri $Url -OutFile $part -Headers $headers -TimeoutSec 3600 -UseBasicParsing
  }

  $got = (Get-Item -LiteralPath $part).Length
  if ($got -ne $ExpectedBytes) {
    Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    throw ('size mismatch for ' + (Split-Path -Leaf $Destination) + ': expected ' + $ExpectedBytes + ' bytes, got ' + $got)
  }
  if (-not (Test-UabsFileHash -Path $part -Sha256 $ExpectedSha256)) {
    Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    throw ('SHA-256 mismatch for ' + (Split-Path -Leaf $Destination) + '; refused to install.')
  }
  Move-Item -LiteralPath $part -Destination $Destination -Force
  Write-UabsOk ('verified and installed ' + $Destination)
  return $Destination
}

function Get-UabsPreviousFrontends {
  $path = Join-Path (Get-UabsStateRoot) 'anima-swarmui.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return @() }
  try {
    $prev = [IO.File]::ReadAllText($path) | ConvertFrom-Json
    return @($prev.frontends | Where-Object { $_.root })
  } catch { return @() }
}

function Get-UabsVerifiedCache {
  # Hashing a 5.8 GB file on every Ensure-Tools pass is wasteful. A file whose
  # size and mtime still match a recorded verification is trusted; anything
  # else is re-hashed before use.
  $map = @{}
  $path = Join-Path (Get-UabsStateRoot) 'anima-swarmui.json'
  if (Test-Path -LiteralPath $path -PathType Leaf) {
    try {
      $state = [IO.File]::ReadAllText($path) | ConvertFrom-Json
      foreach ($p in $state.verified.PSObject.Properties) { $map[$p.Name] = $p.Value }
    } catch { }
  }
  return $map
}

function Test-UabsCachedHash {
  param([string]$Path, [string]$Sha256, [hashtable]$Cache)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
  $item = Get-Item -LiteralPath $Path
  $key = $item.FullName.ToLowerInvariant()
  if ($Cache.ContainsKey($key)) {
    $rec = $Cache[$key]
    if ([long]$rec.bytes -eq $item.Length -and ([string]$rec.sha256) -ieq $Sha256 -and
        ([string]$rec.mtime) -eq $item.LastWriteTimeUtc.ToString('o')) { return $true }
  }
  return $false
}

function Set-UabsCachedHash {
  param([string]$Path, [string]$Sha256, [hashtable]$Cache)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
  $item = Get-Item -LiteralPath $Path
  $Cache[$item.FullName.ToLowerInvariant()] = [pscustomobject]@{
    bytes = $item.Length; mtime = $item.LastWriteTimeUtc.ToString('o'); sha256 = $Sha256
  }
}

function Install-UabsAnimaIntoFrontend {
  param([string]$Frontend, [string]$Root, [object]$ModelDirs, $Files, [hashtable]$Cache)
  $report = New-Object System.Collections.Generic.List[object]
  $failures = 0

  foreach ($entry in $Files) {
    $destDir = switch ($entry.role) {
      'diffusion_model' { $ModelDirs.Diffusion }
      'text_encoder'    { $ModelDirs.TextEncoder }
      'vae'             { $ModelDirs.VAE }
      default           { $null }
    }
    if (-not $destDir) { continue }
    $dest = Join-Path $destDir $entry.filename
    $names = @($entry.filename) + @($entry.aliases)
    $row = [ordered]@{ role = $entry.role; file = $entry.filename; status = ''; path = $dest }

    $existing = $null
    if (-not $Force) {
      $existing = Find-UabsExistingModelFile -Dir $destDir -Names $names -Bytes ([long]$entry.bytes)
      $hashOk = $false
      if ($existing) {
        $hashOk = (Test-UabsCachedHash -Path $existing -Sha256 ([string]$entry.sha256) -Cache $Cache) -or
                  (Test-UabsFileHash -Path $existing -Sha256 ([string]$entry.sha256))
        if ($hashOk -and -not $Cache.ContainsKey($existing.ToLowerInvariant())) {
          Set-UabsCachedHash -Path $existing -Sha256 ([string]$entry.sha256) -Cache $Cache
        }
      }
      if ($hashOk) {
        $row.status = 'already present (verified)'
        $row.path = $existing
        $report.Add([pscustomobject]$row)
        Write-UabsInfo ($entry.filename + ' - already present, verified')
        continue
      }
      if ($existing) { Write-UabsWarn ($entry.filename + ' present but hash differs; replacing.') }
    }

    try {
      if ($CheckOnly) {
        $row.status = 'would download'
        Write-UabsInfo ($entry.filename + ' - would download (missing)')
      } elseif ($Offline) {
        $row.status = 'not downloaded (offline)'
        Write-UabsInfo ($entry.filename + ' - missing; offline mode, not downloaded')
      } else {
        [void](Save-UabsModelFile -Url ([string]$entry.url) -Destination $dest `
          -ExpectedBytes ([long]$entry.bytes) -ExpectedSha256 ([string]$entry.sha256) -Offline:$Offline)
        $row.status = 'installed and verified'
        Set-UabsCachedHash -Path $dest -Sha256 ([string]$entry.sha256) -Cache $Cache
      }
    } catch {
      $row.status = 'FAILED: ' + $_.Exception.Message
      $failures++
      Write-UabsBad ($entry.filename + ' - ' + $_.Exception.Message)
    }
    $report.Add([pscustomobject]$row)
  }
  return [pscustomobject]@{ Files = @($report.ToArray()); Failures = $failures }
}

# --- main ------------------------------------------------------------------
$packRoot = $null
try { $packRoot = Split-Path -Parent $PSScriptRoot } catch { $packRoot = $null }
if (-not $packRoot) {
  # Walk up from this script's folder looking for the pack's marker.
  $p = $PSScriptRoot
  for ($i = 0; $i -lt 4 -and $p; $i++) {
    if (Test-Path -LiteralPath (Join-Path $p 'BUNDLED-TOOLS\CATALOG.json')) { $packRoot = $p; break }
    $p = Split-Path -Parent $p
  }
}
if (-not $packRoot -or -not (Test-Path -LiteralPath $packRoot -PathType Container)) {
  Write-UabsInfo 'Repository root not found; nothing to do.'
  exit 0
}

$presetDir = Join-Path $packRoot 'presets'
$manifestPath = Join-Path $presetDir 'anima-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  Write-UabsWarn ('Anima manifest missing; optional stack skipped: ' + $manifestPath)
  exit 0
}
$manifest = ([IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json)
$files = @($manifest.files | Where-Object { $IncludeOptional -or -not $_.optional })
if (-not $files.Count) { $files = @($manifest.files) }

Write-UabsStep 'Anima generation stack'
Write-UabsInfo ('manifest: ' + $manifestPath + ' (' + $files.Count + ' files)')

# 1. Locate frontends: explicit > cached > bounded scan (never a drive walk).
$found = $null
if (-not $Refresh) { $found = Get-UabsCachedFrontends }
if (-not $found) { $found = Find-UabsFrontendRoots }
if ($SwarmUIRoot -and (Test-UabsSwarmUIRoot $SwarmUIRoot)) { $found.SwarmUI = @($SwarmUIRoot) }
if ($ComfyUIRoot -and (Test-UabsComfyUIRoot $ComfyUIRoot)) { $found.ComfyUI = @($ComfyUIRoot) }

if (-not $found.SwarmUI.Count -and -not $found.ComfyUI.Count) {
  Write-UabsInfo 'No SwarmUI or ComfyUI install found on this machine; nothing to do.'
  if (-not $CheckOnly) {
    $state = [ordered]@{ schema = 1; ran_at = (Get-Date).ToString('o'); frontends = @(Get-UabsPreviousFrontends) }
    $statePath = Join-Path (Get-UabsStateRoot) 'anima-swarmui.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statePath) | Out-Null
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
  }
  exit 0
}
foreach ($s in $found.SwarmUI) { Write-UabsOk ('SwarmUI: ' + $s) }
foreach ($c in $found.ComfyUI) { Write-UabsOk ('ComfyUI: ' + $c) }

$state = [ordered]@{
  schema = 1
  ran_at = (Get-Date).ToString('o')
  check_only = [bool]$CheckOnly
  include_optional = [bool]$IncludeOptional
  frontends = @(Get-UabsPreviousFrontends)
}
$totalFailures = 0
$verified = Get-UabsVerifiedCache

# 2. SwarmUI (its own Models tree; its internal ComfyUI backend shares it).
foreach ($sroot in @($found.SwarmUI)) {
  $modelDirs = Get-UabsSwarmUIModelRoot -SwarmRoot $sroot
  $res = Install-UabsAnimaIntoFrontend -Frontend 'SwarmUI' -Root $sroot -ModelDirs $modelDirs -Files $files -Cache $verified
  $totalFailures += $res.Failures

  # Copy the presets alongside the install so they are easy to import.
  if (-not $CheckOnly) {
    $srcPresets = Join-Path $presetDir 'presets.json'
    $dstPresets = Join-Path $sroot 'Data\presets-uabs-anima.json'
    if (Test-Path -LiteralPath $srcPresets -PathType Leaf) {
      Copy-Item -LiteralPath $srcPresets -Destination $dstPresets -Force
      Write-UabsOk ('presets copied to ' + $dstPresets)
    }
    $note = @(
      '# Anima presets for this SwarmUI',
      '',
      'Generated by TOOLS\Ensure-AnimaSwarmUI.ps1 from the bundle.',
      '',
      'To load them: open SwarmUI, go to the Presets tab, use Import and choose',
      'presets-uabs-anima.json in this folder.',
      '',
      'The Anima presets use only samplers and schedulers SwarmUI exposes natively.',
      'RES4LYF names (rk_beta, beta57, res_2s, bong_tangent) are intentionally not used.'
    ) -join "`r`n"
    [IO.File]::WriteAllText((Join-Path $sroot 'Data\PRESETS-IMPORT.md'), ($note + "`r`n"), (New-Object Text.UTF8Encoding $false))
  }

  $state.frontends += [pscustomobject]@{
    kind = 'SwarmUI'; root = $sroot
    model_root = $modelDirs.Diffusion
    files = $res.Files
  }
}

# 3. Standalone ComfyUI (not a backend nested inside a SwarmUI root).
#    Comfy Desktop layouts are skipped on purpose: that app keeps its models in
#    a separate ComfyUI-Shared tree and manages them itself, so dropping files
#    into the instance's models folder would strand them.
foreach ($croot in @($found.ComfyUI)) {
  $nested = $false
  foreach ($sroot in @($found.SwarmUI)) {
    if (Test-UabsPathWithin -Path $croot -Root $sroot) { $nested = $true; break }
  }
  if ($nested) {
    Write-UabsInfo ('ComfyUI backend inside SwarmUI, its Models tree is shared: ' + $croot)
    continue
  }
  $grandparent = Split-Path -Parent (Split-Path -Parent $croot)
  if ((Split-Path -Leaf $grandparent) -ieq 'ComfyUI-Installs' -or (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $grandparent) 'ComfyUI-Shared') -PathType Container)) {
    Write-UabsInfo ('Comfy Desktop install; it manages its own shared models, skipped: ' + $croot)
    continue
  }
  $modelDirs = Get-UabsComfyUIModelRoot -ComfyRoot $croot
  $res = Install-UabsAnimaIntoFrontend -Frontend 'ComfyUI' -Root $croot -ModelDirs $modelDirs -Files $files -Cache $verified
  $totalFailures += $res.Failures
  $state.frontends += [pscustomobject]@{
    kind = 'ComfyUI'; root = $croot
    model_root = $modelDirs.Diffusion
    files = $res.Files
  }
}

# 4. Record the run.
if (-not $CheckOnly) {
  $statePath = Join-Path (Get-UabsStateRoot) 'anima-swarmui.json'
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statePath) | Out-Null
  $live = @()
  foreach ($sroot in @($found.SwarmUI)) { $live += [pscustomobject]@{ kind = 'SwarmUI'; root = $sroot } }
  foreach ($croot in @($found.ComfyUI)) { $live += [pscustomobject]@{ kind = 'ComfyUI'; root = $croot } }
  # A transient discovery miss must not erase a working install: keep the last
  # good frontend list when this run found none.
  if ($live.Count) { $state.frontends = $live }
  $state.verified = $verified
  [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
  Write-UabsOk ('state: ' + $statePath)
}

if ($totalFailures) {
  Write-UabsWarn ('Anima setup finished with ' + $totalFailures + ' failed download(s).')
  exit 1
}
Write-UabsOk 'Anima generation stack ready.'
exit 0
