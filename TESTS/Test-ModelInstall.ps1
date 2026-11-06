# Fault-injection tests for the model installation transaction.
# Loads TOOLS/model-install-lib.ps1 - the production code - so a missing helper
# or a wrong staging path fails here instead of in a user's model folder.
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

# A downloader that copies known bytes, or fails, or truncates.
function Make-Downloader {
  param([byte[]]$Bytes, [switch]$Fail, [switch]$Truncate)
  {
    param($Url, $Path)
    if ($Fail) { throw 'simulated download failure' }
    if ($Truncate) { [IO.File]::WriteAllBytes($Path, $Bytes[0..7]); return }
    [IO.File]::WriteAllBytes($Path, $Bytes)
  }.GetNewClosure()
}

function Install([scriptblock]$dl, [scriptblock]$log = $null) {
  Install-ModelFile -Destination $dest -Url 'http://example.invalid/m' `
    -ExpectedBytes $payload.Length -ExpectedSha256 $hash -Downloader $dl -Log $log
}

# --- 1: missing destination --------------------------------------------
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a missing destination is installed'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the installed bytes are the verified ones'

# --- 2: existing verified destination is untouched ----------------------
$before = (Get-Item -LiteralPath $dest).LastWriteTimeUtc
Start-Sleep -Milliseconds 20
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'unchanged') 'a verified destination reports unchanged'
Check ((Get-Item -LiteralPath $dest).LastWriteTimeUtc -eq $before) 'a verified destination is not rewritten'

# --- 3: different bytes, same length ------------------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](9..40))
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'same-length different bytes is replaced'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*').Count -eq 1) 'the replaced file is backed up'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the replacement is the verified file'

# --- 4: different length -------------------------------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](1..5))
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a different-length collision is replaced'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'model.safetensors.replaced-*').Count -eq 2) 'each replacement keeps its own backup'

# --- 5: download failure keeps the previous file -------------------------
[IO.File]::WriteAllBytes($dest, [byte[]](7..18))
$original = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
$r = Install (Make-Downloader -Bytes $payload -Fail)
Check ($r.State -eq 'failed') 'a download failure reports failed'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a download failure leaves the previous file intact'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter '*.download-*').Count -eq 0) 'a failed download leaves no staging file'

# --- 6: truncated download ----------------------------------------------
$r = Install (Make-Downloader -Bytes $payload -Truncate)
Check ($r.State -eq 'failed') 'a truncated download is refused'
Check ($r.Message -match 'size mismatch') 'a truncated download is refused for the right reason'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a truncated download leaves the previous file intact'

# --- 7: invalid checksum -------------------------------------------------
$r = Install (Make-Downloader -Bytes ([byte[]](2..33)))
Check ($r.State -eq 'failed') 'a checksum mismatch is refused'
Check ($r.Message -match 'SHA-256') 'a checksum mismatch is refused for the right reason'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $original) 'a checksum mismatch leaves the previous file intact'

# --- 8: logging failure cannot break the transaction ---------------------
$r = Install (Make-Downloader -Bytes $payload) { throw 'logger exploded' }
Check ($r.State -eq 'installed') 'a throwing logger does not fail the install'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the file is installed despite the logger'

# --- 9: publication failure rolls back ----------------------------------
# A directory at the destination path makes the final rename fail, after the
# backup has already been taken.
$dest2 = Join-Path $tmp 'blocked.safetensors'
New-Item -ItemType Directory -Path $dest2 | Out-Null
$r = Install-ModelFile -Destination $dest2 -Url 'http://x' -ExpectedBytes $payload.Length `
  -ExpectedSha256 $hash -Downloader (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'failed') 'a failed publication reports failed'
Check ((Test-Path -LiteralPath $dest2)) 'a failed publication does not delete the destination'
Remove-Item -LiteralPath $dest2 -Recurse -Force

# --- 10: an unrelated .part file is never touched ------------------------
$stray = Join-Path $tmp 'model.safetensors.part'
[IO.File]::WriteAllBytes($stray, [byte[]](3..6))
$strayHash = (Get-FileHash -LiteralPath $stray -Algorithm SHA256).Hash
[void](Install (Make-Downloader -Bytes $payload -Fail))
Check ((Get-FileHash -LiteralPath $stray -Algorithm SHA256).Hash -eq $strayHash) 'an unrelated .part file is left byte-for-byte alone'

# --- 11: idempotence -----------------------------------------------------
$r1 = Install (Make-Downloader -Bytes $payload)
$r2 = Install (Make-Downloader -Bytes $payload)
Check ($r1.State -eq 'unchanged' -and $r2.State -eq 'unchanged') 'repeated installs are idempotent'
Check (@(Get-ChildItem -LiteralPath $tmp -Filter '*.download-*').Count -eq 0) 'no staging files remain'

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fail -eq 0) { Write-Host 'MODEL INSTALL TESTS: PASS'; exit 0 }
Write-Host "MODEL INSTALL TESTS: $fail FAILED"; exit 1
