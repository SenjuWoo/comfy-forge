# Turn a comfy-forge 3D GLB into a game-ready mesh.
# Asset Forge owns the Blender decimate and the texture cap. This script only
# finds that repo and copies the primary GLB back. A .spz splat is not a mesh.
# Default budget game fails above 20000 triangles. A 700000 sculpt does not ship.
#
#   powershell -File TOOLS\game-ready.ps1 -Input model.glb
#   powershell -File TOOLS\game-ready.ps1 -Input model.glb -Budget hero
#   powershell -File TOOLS\game-ready.ps1 -Input model.glb -Budget accessory

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [Alias('Input')]
  [string]$InputPath,
  [ValidateSet('game', 'hero', 'accessory')]
  [string]$Budget = 'game',
  [int]$Tris = 0,
  [int]$Texture = 0,
  [string]$Out,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Resolve-AssetForge {
  $candidates = @()
  if ($env:ASSETFORGE_ROOT) { $candidates += $env:ASSETFORGE_ROOT }
  $repoParent = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
  $candidates += (Join-Path $repoParent 'Asset-Forge')
  foreach ($candidate in $candidates) {
    if (-not $candidate) { continue }
    $marker = Join-Path $candidate 'src\assetforge\data\recipes\mesh-gameready.json'
    if (Test-Path -LiteralPath $marker) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  throw 'Asset Forge with mesh-gameready was not found. Set ASSETFORGE_ROOT to that folder.'
}

if (-not (Test-Path -LiteralPath $InputPath)) { throw "input not found: $InputPath" }
$item = Get-Item -LiteralPath $InputPath
if ($item.Extension -notmatch '^\.glb$') {
  throw "mesh-gameready takes a .glb. A .spz stays a preview; pass the Idol .glb. Got $($item.Extension)"
}
$full = $item.FullName
if (-not $Out) {
  $Out = Join-Path $item.DirectoryName ($item.BaseName + '-gameready.glb')
}

$forge = Resolve-AssetForge
$repo = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repo 'out\gameready'
New-Item -ItemType Directory -Force -Path $project | Out-Null

$py = Join-Path $forge '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }
$env:PYTHONPATH = (Join-Path $forge 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH

$capLine = & $py -c "from assetforge.gameready import resolve_budget; t, tex, cap = resolve_budget('$Budget', $Tris, $Texture); print('cap %d triangles; Blender is asked for %d; texture %d. A result over the cap fails.' % (cap, t, tex))"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host $capLine

$forgeArgs = @(
  '-m', 'assetforge', 'run', 'mesh-gameready',
  '--input', "mesh=$full",
  '--set', "budget=$Budget",
  '--set', "tris=$Tris",
  '--set', "texture=$Texture",
  '--json'
)
if ($DryRun) { $forgeArgs += '--dry-run' }

Push-Location -LiteralPath $project
try {
  if (-not (Test-Path -LiteralPath (Join-Path $project 'assetforge.json'))) {
    & $py -m assetforge init $project --name gameready
    if ($LASTEXITCODE -ne 0) { throw "assetforge init failed ($LASTEXITCODE)" }
  }
  $raw = & $py @forgeArgs
  $code = $LASTEXITCODE
} finally {
  Pop-Location
}

if ($raw -is [System.Array]) { $text = $raw -join "`n" } else { $text = [string]$raw }
if ($code -ne 0) {
  if ($text) { Write-Host $text }
  exit $code
}
$report = $text | ConvertFrom-Json
if ($DryRun) {
  Write-Host $text
  exit 0
}
$rel = [string]$report.asset.primary.mesh
if (-not $rel) { throw 'Asset Forge finished without a primary mesh' }
$produced = Join-Path $project ($rel -replace '/', '\')
if (-not (Test-Path -LiteralPath $produced)) { throw "game-ready GLB missing: $produced" }
Copy-Item -LiteralPath $produced -Destination $Out -Force
Write-Host "wrote $Out"
foreach ($step in $report.job.steps) {
  Write-Host ("{0} {1} {2}" -f $step.status, $step.name, $step.detail)
}
