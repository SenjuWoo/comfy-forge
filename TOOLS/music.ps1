<#
.SYNOPSIS
  ACE-Step 1.5 music generation: launcher for TOOLS\music.py.

.DESCRIPTION
  Finds Python and passes every argument on untouched. It declares no parameters
  on purpose: a PowerShell script with a param block would bind --out, --format
  and --seed to its own parameters and swallow them (the same reason dataset.ps1
  exists).

  Commands:
    variants                          list the model variants and their files
    check    [--variant V|all]        is the backend ready (it finds SwarmUI's or ComfyUI's port)
    build    --tags "..." [--out f]   write the ComfyUI API graph, no backend
    generate --tags "..." [...]       render takes; each file gets a .json sidecar
    generate --from take.json [...]   render a take again, or vary it (--reseed, --duration ...)

  Exit codes: 0 done, 1 failed, 2 bad arguments, 3 no backend reachable.

.EXAMPLE
  powershell -File TOOLS\music.ps1 check --variant all
  powershell -File TOOLS\music.ps1 generate --tags "lo-fi hip hop, mellow rhodes, vinyl crackle" --instrumental --duration 90 --bpm 82 --key "A minor" --count 3
  powershell -File TOOLS\music.ps1 generate --variant xl-sft --tags "synthwave, female vocals" --lyrics-file song.txt --duration 180
#>
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'music.py'
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
