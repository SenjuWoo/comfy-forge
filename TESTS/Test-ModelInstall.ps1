# Fault-injection tests for the model installer's safe-publish wrapper.
# The function under test is lifted verbatim from Ensure-AnimaSwarmUI.ps1 so the
# test cannot drift from the shipped code; the download and hash helpers are
# stubbed so no network is needed and failures can be injected.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([bool]$cond, [string]$name) {
  if ($cond) { Write-Host "ok   $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}

$src = Get-Content -LiteralPath (Join-Path $repo 'TOOLS\Ensure-AnimaSwarmUI.ps1') -Raw
$m = [regex]::Match($src, '(?s)function Save-UabsModelFileSafely \{.*?\r?\n\}\r?\n')
if (-not $m.Success) { Write-Host 'FAIL could not extract Save-UabsModelFileSafely'; exit 1 }

# --- stubs ---------------------------------------------------------------
function Write-UabsOk([string]$m) { }
function Write-UabsNote([string]$m) { }
function Write-UabsWarn([string]$m) { }
function Test-UabsFileHash {
  param([string]$Path, [string]$Sha256)
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  return ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256)
}
$script:Payload = [byte[]](1..32)
$script:PayloadHash = ''
$script:FailDownload = $false
function Save-UabsModelFile {
  param([string]$Destination, [string]$Url, [long]$ExpectedBytes, [string]$ExpectedSha256)
  if ($script:FailDownload) { throw 'simulated download failure' }
  [IO.File]::WriteAllBytes($Destination, $script:Payload)
  return $Destination
}
Invoke-Expression $m.Value
$script:PayloadHash = (Get-FileHash -LiteralPath (New-TemporaryFile) -Algorithm SHA256).Hash
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("mk-install-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$probe = Join-Path $tmp 'probe.bin'
[IO.File]::WriteAllBytes($probe, $script:Payload)
$script:PayloadHash = (Get-FileHash -LiteralPath $probe -Algorithm SHA256).Hash
Remove-Item -LiteralPath $probe

$dest = Join-Path $tmp 'model.safetensors'

# --- 1: a matching file is left alone ------------------------------------
[IO.File]::WriteAllBytes($dest, $script:Payload)
$before = (Get-Item -LiteralPath $dest).LastWriteTimeUtc
Start-Sleep -Milliseconds 20
[void](Save-UabsModelFileSafely -Destination $dest -Url 'http://x' -ExpectedBytes $script:Payload.Length -ExpectedSha256 $script:PayloadHash)
Check ((Get-Item -LiteralPath $dest).LastWriteTimeUtc -eq $before) 'a verified file is not rewritten'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter '*.replaced-*').Count -eq 0) 'no backup is made when the file already matches'

# --- 2: a mismatched file is backed up, not destroyed --------------------
[IO.File]::WriteAllBytes($dest, [byte[]](9..20))   # same length, different bytes
[void](Save-UabsModelFileSafely -Destination $dest -Url 'http://x' -ExpectedBytes $script:Payload.Length -ExpectedSha256 $script:PayloadHash)
$backups = @(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*')
Check ($backups.Count -eq 1) 'a mismatched file is kept as a backup'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $script:PayloadHash) 'the verified download is published'
Check ($backups[0].Length -eq 12) 'the backup holds the original bytes'

# --- 3: a different-length collision is backed up too --------------------
[IO.File]::WriteAllBytes($dest, [byte[]](1..5))
[void](Save-UabsModelFileSafely -Destination $dest -Url 'http://x' -ExpectedBytes $script:Payload.Length -ExpectedSha256 $script:PayloadHash)
$backups = @(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*')
Check ($backups.Count -eq 2) 'a different-sized collision also produces a backup'

# --- 4: a failed download restores the previous file ---------------------
[IO.File]::WriteAllBytes($dest, [byte[]](7..18))
$original = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
$script:FailDownload = $true
$threw = $false
try {
  [void](Save-UabsModelFileSafely -Destination $dest -Url 'http://x' -ExpectedBytes $script:Payload.Length -ExpectedSha256 $script:PayloadHash)
} catch { $threw = $true }
$script:FailDownload = $false
Check $threw 'a failed download throws'
Check (Test-Path -LiteralPath $dest) 'the previous file is back at the original path'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'the restored file has the original bytes'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter '*.part').Count -eq 0) 'no .part file is left behind'

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fail -eq 0) { Write-Host 'MODEL INSTALL TESTS: PASS'; exit 0 }
Write-Host "MODEL INSTALL TESTS: $fail FAILED"; exit 1
