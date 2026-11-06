# Fault-injection tests for the model installation transaction.
# Loads TOOLS/model-install-lib.ps1 - the production code - so a missing helper,
# a wrong staging path or a broken rollback fails here instead of in a model dir.
#
# The HTTP cases start TESTS/fixtures/http_range_server.py and let real curl talk
# to it: a mocked downloader cannot prove that Range headers are honoured.
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

# --- resume: a kept staged file is continued, not restarted ---------------
$r = Install (Make-Downloader -Bytes $payload -Truncate)
Check ($r.State -eq 'failed') 'a truncated download is refused'
$stageDirs = @(Get-ChildItem -LiteralPath $tmp -Directory -Filter 'model.safetensors.stage-*')
Check ($stageDirs.Count -eq 1) 'the staged file is kept for the next attempt'
Check ((Get-Item -LiteralPath (Join-Path $stageDirs[0].FullName 'data')).Length -eq 8) 'the staged file holds the partial bytes'
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'the kept staged file is resumed to a verified file'
Check ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -eq $hash) 'the resumed file matches the manifest hash'
Check (-not (Test-Path -LiteralPath $stageDirs[0].FullName)) 'the staging directory is removed after a successful install'

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
Check (-not (Test-Path -LiteralPath ($dest + '.install-txn.json'))) 'a completed rollback clears its transaction marker'

# --- user-owned partial is never touched ---------------------------------
# The old code deleted a `.partial` whose sidecar was missing. That is exactly
# how another tool's or the user's own file disappears.
$userPartial = $dest + '.partial'
[IO.File]::WriteAllBytes($userPartial, [byte[]](1..99))
$userHash = (Get-FileHash -LiteralPath $userPartial -Algorithm SHA256).Hash
[IO.File]::WriteAllBytes($dest, [byte[]](3..9))
[void](Install (Make-Downloader -Bytes $payload))
Check ((Get-FileHash -LiteralPath $userPartial -Algorithm SHA256).Hash -eq $userHash) 'a user-owned .partial without a sidecar survives an install'

# --- a staging directory we do not own is left alone ---------------------
$foreign = Join-Path $tmp 'model.safetensors.stage-0123456789ab'
New-Item -ItemType Directory -Path $foreign | Out-Null
$foreignData = Join-Path $foreign 'data'
[IO.File]::WriteAllBytes($foreignData, [byte[]](5..70))
$foreignHash = (Get-FileHash -LiteralPath $foreignData -Algorithm SHA256).Hash
[void](Install (Make-Downloader -Bytes $payload))
Check ((Get-FileHash -LiteralPath $foreignData -Algorithm SHA256).Hash -eq $foreignHash) 'a staging directory with no id file is preserved'
Check (Test-Path -LiteralPath (Join-Path $foreign 'data')) 'an unrecognised staging directory is not deleted'

# --- corrupt identity in a staging directory we might own ------------------
$stage = Get-ModelStageRoot -Destination $dest -Identity ((Get-ModelPartialIdentity -Sha256 $hash -Bytes $payload.Length))
if (-not (Test-Path -LiteralPath $stage)) { New-Item -ItemType Directory -Path $stage | Out-Null }
Set-Content -LiteralPath (Join-Path $stage 'id') -Value 'garbage' -NoNewline -Encoding ascii
[IO.File]::WriteAllBytes((Join-Path $stage 'data'), [byte[]](9..12))
$corruptHash = (Get-FileHash -LiteralPath (Join-Path $stage 'data') -Algorithm SHA256).Hash
[IO.File]::WriteAllBytes($dest, [byte[]](4..9))
$r = Install (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a corrupt identity file does not block the install'
Check ((Get-FileHash -LiteralPath (Join-Path $stage 'data') -Algorithm SHA256).Hash -eq $corruptHash) 'the ambiguous staging data is preserved, not resumed'

# --- crash recovery needs a transaction marker ---------------------------
$crash = Join-Path $tmp 'crashed.safetensors'
[IO.File]::WriteAllBytes($crash, [byte[]](1..16))
$orphan = $crash + '.replaced-20260101-000000-abc123'
Move-Item -LiteralPath $crash -Destination $orphan
$r = Install-ModelFile -Destination $crash -Url 'http://x' -ExpectedBytes $payload.Length `
  -ExpectedSha256 $hash -Downloader (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a destination the user removed is re-installed'
Check (Test-Path -LiteralPath $orphan) 'a historical backup is not mistaken for an interrupted transaction'

$crash2 = Join-Path $tmp 'crashed2.safetensors'
[IO.File]::WriteAllBytes($crash2, [byte[]](1..16))
$backup2 = $crash2 + '.replaced-20260101-000001-def456'
Move-Item -LiteralPath $crash2 -Destination $backup2
@{ identity = (Get-ModelPartialIdentity -Sha256 $hash -Bytes $payload.Length)
   backup = $backup2; destination = $crash2 } |
  ConvertTo-Json -Compress | Set-Content -LiteralPath ($crash2 + '.install-txn.json') -NoNewline -Encoding utf8
$r = Install-ModelFile -Destination $crash2 -Url 'http://x' -ExpectedBytes $payload.Length `
  -ExpectedSha256 $hash -Downloader (Make-Downloader -Bytes $payload)
Check ($r.State -eq 'installed') 'a recorded transaction is completed on the next run'
Check ((Get-FileHash -LiteralPath $crash2 -Algorithm SHA256).Hash -eq $hash) 'the recovered destination holds the verified bytes'
Check (-not (Test-Path -LiteralPath ($crash2 + '.install-txn.json'))) 'the transaction marker is cleared'

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

# --- already verified staging publishes without the network --------------
$dl = {
  param($Url, $Path, $Resumed)
  throw 'the downloader must not be called'
}
$offline = Join-Path $tmp 'offline.safetensors'
$offStage = Get-ModelStageRoot -Destination $offline -Identity ((Get-ModelPartialIdentity -Sha256 $hash -Bytes $payload.Length))
New-Item -ItemType Directory -Path $offStage | Out-Null
Set-Content -LiteralPath (Join-Path $offStage 'id') -Value ((Get-ModelPartialIdentity -Sha256 $hash -Bytes $payload.Length)) -NoNewline -Encoding ascii
[IO.File]::WriteAllBytes((Join-Path $offStage 'data'), $payload)
$r = Install-ModelFile -Destination $offline -Url 'http://x' -ExpectedBytes $payload.Length `
  -ExpectedSha256 $hash -Downloader $dl
Check ($r.State -eq 'installed') 'a fully staged file publishes without a download'
Check ((Get-FileHash -LiteralPath $offline -Algorithm SHA256).Hash -eq $hash) 'the offline publish installs the verified bytes'

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

# --- real HTTP: Range honoured, ignored and rejected ---------------------
# These use curl and the fixture server, because a scriptblock cannot prove that
# a resume actually sent `bytes=N-` or that a 416 is handled.
$py = if ($env:UABS_PYTHON) { $env:UABS_PYTHON } else { 'python' }
$httpOk = $true
try { & $py -c 'import sys; sys.exit(0)' } catch { $httpOk = $false }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { $httpOk = $false }
$pyExe = (Get-Command $py -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $httpOk = $false }

function Start-RangeServer {
  param([string]$File, [string[]]$Extra = @())
  $port = Get-Random -Minimum 18000 -Maximum 18999
  $log = Join-Path $tmp ("range-" + $port + ".log")
  $a = @((Join-Path $repo 'TESTS\fixtures\http_range_server.py'), $File, "$port", '--log', $log) + $Extra
  # A job avoids Start-Process's argument re-joining, which mangles paths that
  # contain spaces (this repository lives under "Ai documentation").
  $job = Start-Job -ScriptBlock { param($exe, $argv) & $exe @argv } -ArgumentList $pyExe, $a
  $bound = $false
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $c = New-Object Net.Sockets.TcpClient
      $c.Connect('127.0.0.1', $port)
      $c.Close()
      $bound = $true
      break
    } catch { Start-Sleep -Milliseconds 250 }
  }
  if (-not $bound) {
    Receive-Job $job | Write-Host
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    throw "the range server never bound port $port"
  }
  return @{ Job = $job; Log = $log; Url = "http://127.0.0.1:$port/payload.bin" }
}

function Stop-RangeServer($srv) {
  Stop-Job $srv.Job -ErrorAction SilentlyContinue
  Remove-Job $srv.Job -Force -ErrorAction SilentlyContinue
}

if ($httpOk) {
  $big = New-Object byte[] 4096
  (New-Object Random 7).NextBytes($big)
  $bigPath = Join-Path $tmp 'big.bin'
  [IO.File]::WriteAllBytes($bigPath, $big)
  $bigHash = (Get-FileHash -LiteralPath $bigPath -Algorithm SHA256).Hash

  function New-ResumeState([string]$Target) {
    # 1000 bytes staged with the right identity is what a killed download leaves.
    $stage = Get-ModelStageRoot -Destination $Target -Identity ((Get-ModelPartialIdentity -Sha256 $bigHash -Bytes $big.Length))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $stage 'id') -Value ((Get-ModelPartialIdentity -Sha256 $bigHash -Bytes $big.Length)) -NoNewline -Encoding ascii
    $fs = [IO.File]::Create((Join-Path $stage 'data'))
    try { $fs.Write($big, 0, 1000) } finally { $fs.Dispose() }
    return $stage
  }

  # 1. Range honoured: curl asks for bytes=1000- and the result verifies.
  $srv = Start-RangeServer -File $bigPath
  try {
    $t1 = Join-Path $tmp 'http-range.safetensors'
    $st1 = New-ResumeState $t1
    $r = Install-ModelFile -Destination $t1 -Url $srv.Url -ExpectedBytes $big.Length `
      -ExpectedSha256 $bigHash
    if ($r.State -ne 'installed') { Write-Host "     range case: $($r.Message)" }
    Check ($r.State -eq 'installed') 'HTTP: a range-capable server resumes and verifies'
    Check ((Get-FileHash -LiteralPath $t1 -Algorithm SHA256).Hash -eq $bigHash) 'HTTP: the resumed download matches the expected hash'
    $sent = if (Test-Path -LiteralPath $srv.Log) { Get-Content -LiteralPath $srv.Log -Raw } else { '' }
    Check ($sent -match 'bytes=1000-') 'HTTP: the resume actually sent a Range header'
    Check ($sent -notmatch 'none 0') 'HTTP: the resume did not start from zero'
  } finally { Stop-RangeServer $srv }

  # 2. Server ignores Range and returns the whole body: curl appends it, the size
  #    check fails, and the retry restarts clean instead of publishing garbage.
  $srv = Start-RangeServer -File $bigPath -Extra @('--ignore-range')
  try {
    $t2 = Join-Path $tmp 'http-ignore.safetensors'
    $st2 = New-ResumeState $t2
    $r = Install-ModelFile -Destination $t2 -Url $srv.Url -ExpectedBytes $big.Length `
      -ExpectedSha256 $bigHash
    if ($r.State -ne 'installed') { Write-Host "     ignore case: $($r.Message)" }
    Check ($r.State -eq 'installed') 'HTTP: a server that ignores Range still installs'
    Check ((Get-FileHash -LiteralPath $t2 -Algorithm SHA256).Hash -eq $bigHash) 'HTTP: the restart produced the correct bytes'
    Check (-not (Test-Path -LiteralPath $st2)) 'HTTP: the poisoned staging directory was replaced'
  } finally { Stop-RangeServer $srv }

  # 3. Server rejects the range with 416: curl exits 33 and the code restarts
  #    clean instead of surfacing a range error to the user.
  $srv = Start-RangeServer -File $bigPath -Extra @('--reject-range')
  try {
    $t3 = Join-Path $tmp 'http-reject.safetensors'
    $st3 = New-ResumeState $t3
    $r = Install-ModelFile -Destination $t3 -Url $srv.Url -ExpectedBytes $big.Length `
      -ExpectedSha256 $bigHash
    if ($r.State -ne 'installed') { Write-Host "     reject case: $($r.Message)" }
    Check ($r.State -eq 'installed') 'HTTP: a 416 range rejection falls back to a clean download'
    Check ((Get-FileHash -LiteralPath $t3 -Algorithm SHA256).Hash -eq $bigHash) 'HTTP: the fallback produced the correct bytes'
  } finally { Stop-RangeServer $srv }

  # 4. Already complete and verified: no request at all. Proven by injecting a
  #    downloader that raises, not by reading a log that may not exist yet.
  $srv = Start-RangeServer -File $bigPath
  try {
    $t4 = Join-Path $tmp 'http-complete.safetensors'
    $stage4 = Get-ModelStageRoot -Destination $t4 -Identity ((Get-ModelPartialIdentity -Sha256 $bigHash -Bytes $big.Length))
    New-Item -ItemType Directory -Path $stage4 -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $stage4 'id') -Value ((Get-ModelPartialIdentity -Sha256 $bigHash -Bytes $big.Length)) -NoNewline -Encoding ascii
    [IO.File]::WriteAllBytes((Join-Path $stage4 'data'), $big)
    $noDownload = {
      param($Url, $Path, $Resumed)
      throw 'a verified staged file must not be downloaded again'
    }
    $r = Install-ModelFile -Destination $t4 -Url $srv.Url -ExpectedBytes $big.Length `
      -ExpectedSha256 $bigHash -Downloader $noDownload
    Check ($r.State -eq 'installed') 'HTTP: a complete staged file publishes offline'
    Check ((Get-FileHash -LiteralPath $t4 -Algorithm SHA256).Hash -eq $bigHash) 'HTTP: the offline publish installs the verified bytes'
  } finally { Stop-RangeServer $srv }
}
else {
  Write-Host 'WARN HTTP resume tests skipped (python or curl.exe unavailable)'
  $script:fail++
}

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fail -eq 0) { Write-Host 'MODEL INSTALL TESTS: PASS'; exit 0 }
Write-Host "MODEL INSTALL TESTS: $fail FAILED"; exit 1
