<#
.SYNOPSIS
  Tagged image folder in, verified training archive out.

.DESCRIPTION
  A launcher for TOOLS\anima_dataset.py that finds Python and passes every
  argument on untouched. It declares no parameters on purpose: a PowerShell
  script with a param block binds `--out` to -Out and `--mode` to -Model, which
  would silently swallow the dataset tool's own flags.

  Commands:
    inspect  --images <folder>            read-only report and the next command
    build    --images <folder> [...]      write the dataset (default command)
    validate <folder-or-zip>              check a finished dataset or archive

  Exit codes: 0 clean, 1 archive does not match, 2 something was left out or failed.

.EXAMPLE
  powershell -File TOOLS\dataset.ps1 inspect --images "D:\shots"
  powershell -File TOOLS\dataset.ps1 build --images "D:\shots" --mode convert --trigger mychar --zip mychar.zip
  powershell -File TOOLS\dataset.ps1 validate mychar.zip
#>
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'anima_dataset.py'
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
