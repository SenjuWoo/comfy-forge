<#
.SYNOPSIS
  Image or text to a 3D model: launcher for TOOLS\model3d.py.

.DESCRIPTION
  Finds Python and passes every argument on untouched. It declares no parameters
  on purpose: a PowerShell script with a param block would bind --out and --seed
  to its own parameters and swallow them (the same reason music.ps1 exists).

  Commands:
    variants                            list the variants and the files each needs
    check    [--variant V|all]          is the backend ready
    build    --image f [--out g.json]   write the ComfyUI API graph, no backend
    generate --image f [...]            one picture to a .glb
    generate --prompt "..." [...]       text to a picture to a .glb
    generate --variant pixal3d-multi --views front.png,left.png,back.png,right.png

  --game-ready runs Asset Forge's mesh-gameready on the result (20,000 triangles).
  The backend is started headless when none answers; --no-start refuses instead.

  Exit codes: 0 done, 1 failed, 2 bad arguments, 3 no backend reachable.

.EXAMPLE
  powershell -File TOOLS\model3d.ps1 check --variant all
  powershell -File TOOLS\model3d.ps1 generate --image sword.png --name sword --game-ready
  powershell -File TOOLS\model3d.ps1 generate --prompt "a viking axe with a wolf-head pommel" --name axe --game-ready
#>
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'model3d.py'
$python = $null
foreach ($name in @($env:COMFY_FORGE_PYTHON, 'python', 'python3', 'py')) {
  if (-not $name) { continue }
  $found = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { $python = $found.Source; break }
}
if (-not $python) {
  Write-Host 'error  Python 3.10 or newer was not found. Install it, or set COMFY_FORGE_PYTHON to python.exe.' -ForegroundColor Red
  exit 3
}
$forward = @($args)
if ($forward.Count -eq 0) { $forward = @('--help') }
& $python $script @forward
exit $LASTEXITCODE
