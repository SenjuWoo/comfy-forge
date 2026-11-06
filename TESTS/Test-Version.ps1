# Version consistency: VERSION is the single source of truth.
# Fails if CHANGELOG's newest heading disagrees with VERSION, or if the release
# notes for that version are missing.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw).Trim()
if (-not $version) { Write-Host 'FAIL VERSION is empty'; exit 1 }

$changelog = Get-Content -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Raw
$match = [regex]::Match($changelog, '(?m)^##\s+v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)')
if (-not $match.Success) { Write-Host 'FAIL CHANGELOG has no version heading'; exit 1 }
$top = $match.Groups[1].Value

$fail = 0
if ($top -ne $version) {
  Write-Host "FAIL VERSION ($version) does not match the newest CHANGELOG entry ($top)"
  $fail++
} else {
  Write-Host "ok   VERSION matches CHANGELOG ($version)"
}

$state = Get-Content -LiteralPath (Join-Path $repo 'STATE.md') -Raw
if ($state -notmatch [regex]::Escape($version)) {
  Write-Host "FAIL STATE.md does not mention $version"
  $fail++
} else {
  Write-Host 'ok   STATE.md references the current version'
}

if ($changelog -match '(?m)^##\s+v?0\.2\.0' -and $version -notmatch '^0\.2\.0') {
  Write-Host 'ok   historical entries retained'
}

if ($fail -eq 0) { Write-Host 'VERSION: PASS' } else { Write-Host "VERSION: $fail FAILED" }
exit $(if ($fail -eq 0) { 0 } else { 1 })
