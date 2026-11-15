# Parse-only syntax gate for every PowerShell file in TOOLS and TESTS. No execution,
# no side effects. A fixed list missed new files (the installers and libraries were
# never parsed here), so the list is the folders themselves.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$files = @(Get-ChildItem -LiteralPath (Join-Path $repo 'TOOLS'), (Join-Path $repo 'TESTS') -Filter '*.ps1' -File)
if ($files.Count -lt 10) { throw "only $($files.Count) PowerShell files found; the gate is looking in the wrong place" }
$failed = 0
foreach ($f in $files) {
  $tokens = $null; $errors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors)
  $rel = $f.FullName.Substring($repo.Length + 1)
  if ($errors -and $errors.Count) {
    Write-Host "FAIL $rel" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host ("   line {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message) }
    $script:failed++
  } else { Write-Host "ok   $rel" }
}
if ($failed) { throw "$failed file(s) did not parse" }
Write-Host "POWERSHELL PARSE: PASS ($($files.Count) files)"
