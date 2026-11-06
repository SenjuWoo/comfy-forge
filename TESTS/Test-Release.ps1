# Release identity check.
#
# v0.5.0 shipped a stable tag on a tree whose VERSION still said 0.5.0-rc.5, so
# the release page and the code disagreed. This makes that mismatch fail: the
# declared version, the top changelog entry, STATE.md and - when the build comes
# from a tag - the tag name itself must all agree.
#
# Runs in CI on every push and again from .github/workflows/release-verify.yml on
# a tag, where the tag name is available as GITHUB_REF_NAME.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([bool]$cond, [string]$name) {
  if ($cond) { Write-Host "ok   $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}

$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw).Trim()
Check ($version -match '^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$') "VERSION is a version: $version"

$changelog = Get-Content -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Raw
$firstHeading = ([regex]::Match($changelog, '(?m)^##\s+(.+)$')).Groups[1].Value.Trim()
Check ($firstHeading -match ([regex]::Escape($version) + '\b')) `
  "the top changelog entry names $version (found: $firstHeading)"

$state = Get-Content -LiteralPath (Join-Path $repo 'STATE.md') -Raw
Check ($state -match [regex]::Escape($version)) "STATE.md names $version"

# A tag build must not carry an RC version, and the tag must equal VERSION.
$ref = $env:GITHUB_REF_NAME
if ($ref -and $ref -match '^v') {
  Check ($ref -eq ('v' + $version)) "the tag $ref matches VERSION ($version)"
  Check ($version -notmatch '-rc\.') "a released tag cannot declare an RC version ($version)"
}

if ($fail -eq 0) { Write-Host "RELEASE VERSION: PASS ($version)"; exit 0 }
Write-Host "RELEASE VERSION: $fail FAILED"; exit 1
