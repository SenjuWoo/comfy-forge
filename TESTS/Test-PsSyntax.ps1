# Parse-only syntax gate for the PowerShell tools. No execution, no side effects.
$ErrorActionPreference = 'Stop'
$files = @('TOOLS\image-stack.ps1', 'TOOLS\Ensure-AnimaSwarmUI.ps1', 'TESTS\Test-AnimaInstall.ps1')
$failed = 0
foreach ($f in $files) {
  $tokens = $null; $errors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot "..\$f"), [ref]$tokens, [ref]$errors)
  if ($errors -and $errors.Count) {
    Write-Host "FAIL $f" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host ("   line {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message) }
    $script:failed++
  } else { Write-Host "ok   $f" }
}
if ($failed) { throw "$failed file(s) did not parse" }
Write-Host 'POWERSHELL PARSE: PASS'
