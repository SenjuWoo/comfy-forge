# Release identity check.
#
# v0.5.0 shipped a stable tag on a tree whose VERSION still said 0.5.0-rc.5, so
# the release page and the code disagreed. This makes that mismatch fail: the
# declared version, the top changelog heading, STATE.md and - when the build comes
# from a tag - the tag name itself must all agree exactly.
#
# "Exactly" matters: a prefix match let `## v0.5.1-rc.1` satisfy 0.5.1. The
# matcher is self-tested first so that regression cannot come back quietly.
#
# Runs in CI on every push and again from .github/workflows/release-verify.yml on
# a tag, where the tag name is available as GITHUB_REF_NAME.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([bool]$cond, [string]$name) {
  if ($cond) { Write-Host "ok   $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}

function Get-HeadingVersion([string]$Heading) {
  # The version token itself, not a prefix of it.
  if ($Heading -match 'v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)') { return $matches[1] }
  return $null
}

# --- matcher self-test ------------------------------------------------------
Check ((Get-HeadingVersion '## v0.5.1 - safety patch') -eq '0.5.1') 'matcher reads a plain release heading'
Check ((Get-HeadingVersion '## 0.5.1 - safety patch') -eq '0.5.1') 'matcher accepts a heading without the v'
Check ((Get-HeadingVersion '## v0.5.1-rc.1 - candidate') -ne '0.5.1') 'a release candidate does not satisfy the release'
Check ((Get-HeadingVersion '## v0.5.10 - later') -ne '0.5.1') 'a longer version is not the shorter one'
Check ($null -eq (Get-HeadingVersion '## Unreleased')) 'a heading without a version yields nothing'

$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw).Trim()
Check ($version -match '^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$') "VERSION is a version: $version"

$changelog = Get-Content -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Raw
$firstHeading = ([regex]::Match($changelog, '(?m)^##\s+(.+)$')).Groups[1].Value.Trim()
Check ((Get-HeadingVersion $firstHeading) -eq $version) `
  "the top changelog heading is exactly $version (found: $firstHeading)"

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
