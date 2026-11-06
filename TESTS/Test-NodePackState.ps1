# Node-pack state and clone-guard tests, against real temporary git repositories.
# Loads TOOLS/node-pack-lib.ps1 - the production evaluator - so a state that
# could be mistaken for "installed" fails here.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([bool]$cond, [string]$name) {
  if ($cond) { Write-Host "ok   $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}
. (Join-Path $repo 'TOOLS\node-pack-lib.ps1')

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host 'WARN git is not installed; node pack tests cannot run'
  exit 1
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("np-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$dir = Join-Path $tmp 'custom_nodes'
New-Item -ItemType Directory -Path $dir -Force | Out-Null

$url = 'https://github.com/example/pack.git'
function New-Repo([string]$Name, [string]$Origin) {
  $p = Join-Path $dir $Name
  New-Item -ItemType Directory -Path $p -Force | Out-Null
  & git -C $p init -q 2>$null
  & git -C $p config user.email 'test@example.com'
  & git -C $p config user.name 'test'
  Set-Content -LiteralPath (Join-Path $p 'a.txt') -Value 'one'
  & git -C $p add -A 2>$null
  & git -C $p commit -q -m init 2>$null
  if ($Origin) { & git -C $p remote add origin $Origin }
  return $p
}

function Pin([string]$Name, [string]$Sha) {
  return [pscustomobject]@{ name = $Name; url = $url; sha = $Sha; why = 'test' }
}

# --- locked -----------------------------------------------------------------
$a = New-Repo 'pack-a' $url
$shaA = (& git -C $a rev-parse HEAD | Out-String).Trim()
$st = Get-NodePackState $dir (Pin 'pack-a' $shaA)
Check ($st.State -eq 'locked') 'a clean checkout at the pinned commit is locked'

# --- dirty ------------------------------------------------------------------
Set-Content -LiteralPath (Join-Path $a 'a.txt') -Value 'edited'
$st = Get-NodePackState $dir (Pin 'pack-a' $shaA)
Check ($st.State -eq 'dirty') 'a checkout with local edits is dirty, not locked'

# --- drift ------------------------------------------------------------------
$b = New-Repo 'pack-b' $url
$shaB = (& git -C $b rev-parse HEAD | Out-String).Trim()
$st = Get-NodePackState $dir (Pin 'pack-b' ('0' * 40))
Check ($st.State -eq 'drift') 'a different commit is drift'

# --- foreign repository -----------------------------------------------------
$foreign = [pscustomobject]@{ name = 'pack-b'; url = 'https://github.com/other/pack.git'; sha = $shaB }
$st = Get-NodePackState $dir $foreign
Check ($st.State -eq 'foreign-repo') 'a checkout of another repository is foreign-repo'

# --- no origin --------------------------------------------------------------
$c = New-Repo 'pack-c' $null
$shaC = (& git -C $c rev-parse HEAD | Out-String).Trim()
$st = Get-NodePackState $dir (Pin 'pack-c' $shaC)
Check ($st.State -eq 'unverifiable' -and $st.Detail -eq 'no origin remote') 'a checkout with no origin is unverifiable'

# --- not a git checkout -----------------------------------------------------
$plain = Join-Path $dir 'pack-plain'
New-Item -ItemType Directory -Path $plain | Out-Null
Set-Content -LiteralPath (Join-Path $plain 'file.py') -Value 'x'
$st = Get-NodePackState $dir (Pin 'pack-plain' $shaA)
Check ($st.State -eq 'unverifiable' -and $st.Detail -eq 'not a git checkout') 'a copied folder is unverifiable, not installed'

# --- missing ----------------------------------------------------------------
$st = Get-NodePackState $dir (Pin 'pack-missing' $shaA)
Check ($st.State -eq 'missing') 'an absent pack is missing'

# --- git status itself fails ------------------------------------------------
$d = New-Repo 'pack-d' $url
$shaD = (& git -C $d rev-parse HEAD | Out-String).Trim()
Set-Content -LiteralPath (Join-Path $d '.git\index') -Value 'not an index' -Encoding ascii
$st = Get-NodePackState $dir (Pin 'pack-d' $shaD)
Check ($st.State -eq 'unverifiable') 'a failing git status is unverifiable, never locked'
Check ($st.Detail -eq 'git status failed') 'the failure is named'

# --- clone guard ------------------------------------------------------------
$free = Join-Path $dir 'pack-free'
Check ((Clear-OccupiedPackDir $free) -eq 'free') 'an absent directory is free to clone into'

$checkout = Join-Path $dir 'pack-checkout'
New-Item -ItemType Directory -Path $checkout | Out-Null
New-Item -ItemType Directory -Path (Join-Path $checkout '.git') | Out-Null
Check ((Clear-OccupiedPackDir $checkout) -eq 'checkout') 'an existing checkout is reported, not moved'

$partial = Join-Path $dir 'pack-partial'
New-Item -ItemType Directory -Path $partial | Out-Null
Set-Content -LiteralPath (Join-Path $partial 'README.md') -Value 'half cloned'
$res = Clear-OccupiedPackDir $partial
Check ($res -like 'aside:*') 'a folder with no .git is moved aside'
Check (-not (Test-Path -LiteralPath $partial)) 'the occupied path is now free for the clone'
Check (Test-Path -LiteralPath ($res.Substring(6)) -PathType Container) 'the moved folder still exists'
Check ((Get-Content -LiteralPath (Join-Path $res.Substring(6) 'README.md') -Raw).Trim() -eq 'half cloned') 'its contents are preserved, not deleted'

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fail -eq 0) { Write-Host 'NODE PACK STATE: PASS'; exit 0 }
Write-Host "NODE PACK STATE: $fail FAILED"; exit 1
