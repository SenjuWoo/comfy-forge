# Fault-injection tests for the model installation transaction.
# Loads TOOLS/model-install-lib.ps1 - the production code - so a missing helper,
# a wrong staging path or a broken rollback fails here instead of in a model dir.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([bool]$cond, [string]$name) {
  if ($cond) { Write-Host "ok   $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}

. (Join-Path $repo 'TOOLS\model-install-lib.ps1')

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("mk-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$dest = Join-Path $tmp 'model.safetensors'

$payload = [byte[]](1..32)
$payloadPath = Join-Path $tmp 'payload.bin'
[IO.File]::WriteAllBytes($payloadPath, $payload)
$hash = (Get-FileHash -LiteralPath $payloadPath -Algorithm SHA256).Hash

function Make-Downloader {
  param([byte[]]$Bytes, [switch]$Fail, [switch]$Truncate)
  {
    param($Url, $Path, $Resumed)
    if ($Fail) { throw 'simulated download failure' }
    if ($Truncate) { [IO.File]::WriteAllBytes($Path, $Bytes[0..7]); return }
    if ($Resumed -and (Test-Path -LiteralPath $Path)) {
      # Append only the missing tail, the way a server honouring Range would.
      $have = (Get-Item -LiteralPath $Path).Length
      $fs = [IO.File]::Open($Path, 'Append')
      try { $fs.Write($Bytes, [int]$have, $Bytes.Length - [int]$have) } finally { $fs.Dispose() }
      return
    }
    [IO.File]::WriteAllBytes($Path, $Bytes)
  }.GetNewClosure()
}

function Install([scriptblock]$dl, [scriptblock]$log = $null, [scriptblock]$publish = $null) {
  Install-ModelFile -Destination $dest -Url 'http://example.invalid/m' `
    -ExpectedBytes $payload.Length -ExpectedSha256 $hash `
    -Downloader $dl -Log $log -Publish $publish
}

# --- missing destination -------------------------------------------------
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a missing destination is installed'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the installed bytes are the verified ones'

# --- existing verified destination ---------------------------------------
$before = (Get-Item -LiteralPath $dest).LastWriteTimeUtc
Start-Sleep -Milliseconds 20
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'unchanged') 'a verified destination reports unchanged'
Check ((Get-Item -LiteralPath $dest).LastWriteTimeUtc -eq $before) 'a verified destination is not rewritten'

# --- different bytes, same length ---------------------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](9..40))
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'same-length different bytes is replaced'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*').Count -eq 1) 'the replaced file is backed up'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the replacement is the verified file'

# --- different length ----------------------------------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](1..5))
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a different-length collision is replaced'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*').Count -eq 2) 'each replacement keeps its own backup'

# --- download failure keeps the previous file ----------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](7..18))
$original = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
$r = Install (Make-Downloader -Bytes $payload -Fail)
Check ($r.State -eq 'failed') 'a download failure reports failed'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a download failure leaves the previous file intact'

# --- resume: a kept partial is continued, not restarted -------------------
$r = Install (Make-Downloader -Bytes $payload -Truncate)
Check ($r.State -eq 'failed') 'a truncated download is refused'
Check (Test-Path -LiteralPath ($dest + '.partial')) 'the partial is kept for the next attempt'
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'the kept partial is resumed to a verified file'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the resumed file matches the manifest hash'

# --- invalid checksum ----------------------------------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](7..18))
$original = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
$r = Install (Make-Downloader -Bytes ([byte[]](2..33)))
Check ($r.State -eq 'failed') 'a checksum mismatch is refused'
Check ($r.Message -match 'verification failed|SHA-256') 'a checksum mismatch is refused for the right reason'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a checksum mismatch leaves the previous file intact'

# --- logging failure cannot break the transaction ------------------------
$r = Install (Make-Downloader -Bytes $payload) { throw 'logger exploded' }
Check ($r.State -eq 'installed') 'a throwing logger does not fail the install'

# --- publication failure, after the backup exists ------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](7..18))
$original = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
$r = Install (Make-Downloader -Bytes $payload) $null { throw 'disk full' }
Check ($r.State -eq 'failed') 'a publication failure reports failed'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a publication failure restores the previous file'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*').Count -ge 3) 'the backup existed at the moment publication failed'

# --- crash recovery: destination gone, backup present --------------------
$crash = Join-Path $tmp 'crashed.safetensors'
[IO.File]::WriteAllBytes($crash, $payload)
Move-Item -LiteralPath $crash -Destination ($crash + '.replaced-20260101-000000-abc123')
$r = Install-ModelFile -Destination $crash -Url 'http://x' -ExpectedBytes $payload.Length `
  -ExpectedSha256 $hash -Downloader (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'unchanged') 'an interrupted install is recovered on the next run'
Check ((Get-FileHash -LiteralPath $crash -Algorithm SHA256).Hash -eq $hash) 'the recovered file is the original bytes'

# --- a fresh tree with only a parent folder ------------------------------
$fresh = Join-Path $tmp 'fresh\models\diffusion_models\deep'
$r = Install-ModelFile -Destination (Join-Path $fresh 'new.safetensors') -Url 'http://x' `
  -ExpectedBytes $payload.Length -ExpectedSha256 $hash -Downloader (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a missing model category folder is created on first install'
Check (Test-Path -LiteralPath (Join-Path $fresh 'new.safetensors')) 'the file lands in the created category'

# --- concurrent install is refused --------------------------------------
$held = [IO.File]::Open(($dest + '.install-lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try { $r = Install (Make-Downloader -Bytes $payload) } finally { $held.Dispose() }
Check ($r.State -eq 'failed' -and $r.Message -match 'another install') 'a concurrent install is refused'

# --- unrelated .part file is never touched -------------------------------
$stray = Join-Path $tmp 'model.safetensors.part'
[IO.File]::WriteAllBytes($stray, [byte[]](3..6))
$strayHash = (Get-FileHash -LiteralPath $stray -Algorithm SHA256).Hash
[void](Install (Make-Downloader -Bytes $payload -Fail))
Check ((Get-FileHash -LiteralPath $stray -Algorithm SHA256).Hash -eq $strayHash) 'an unrelated .part file is left byte-for-byte alone'

# --- idempotence ---------------------------------------------------------
# Bring the destination to the expected bytes first; earlier cases deliberately
# leave different content behind.
[void](Install (Make-Downloader -Bytes $payload))
$r1 = Install (Make-Downloader -Bytes $payload)
$r2 = Install (Make-Downloader -Bytes $payload)
Check ($r1.State -eq 'unchanged' -and $r2.State -eq 'unchanged') 'repeated installs are idempotent'
Check (-not (Test-Path -LiteralPath ($dest + '.install-lock'))) 'the lock is released'

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fail -eq 0) { Write-Host 'MODEL INSTALL TESTS: PASS'; exit 0 }
Write-Host "MODEL INSTALL TESTS: $fail FAILED"; exit 1
