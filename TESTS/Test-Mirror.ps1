# Mirror regression tests: prove a merge output can never be deleted by the
# ComfyUI mirror step, that links are verified by file identity, and that
# collisions are set aside rather than overwritten.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File TESTS\Test-Mirror.ps1

$ErrorActionPreference = 'Stop'
$lib = Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\mirror-lib.ps1'
. $lib

$script:FAIL = 0
function Check([bool]$Condition, [string]$Name) {
  if ($Condition) { Write-Host "ok   $Name" } else { Write-Host "FAIL $Name"; $script:FAIL++ }
}

$root = Join-Path $env:TEMP ("mirror-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$srcDir = Join-Path $root 'models with spaces'
$dstDir = Join-Path $root 'other tree'
New-Item -ItemType Directory -Path $srcDir, $dstDir -Force | Out-Null

$model = Join-Path $srcDir 'My Model v1.safetensors'
[IO.File]::WriteAllBytes($model, (1..4096 | ForEach-Object { $_ % 251 }))

# 1. Hard link on the same volume.
$r = Set-ModelMirror -Source $model -DestinationDirectory $dstDir
$mirror = Join-Path $dstDir 'My Model v1.safetensors'
Check ($r.Kind -eq 'linked') 'same-volume mirror reports a link'
Check (Test-SameFile $model $mirror) 'mirror shares the source file identity'
Check ((Get-Item $mirror).Length -eq (Get-Item $model).Length) 'mirror length matches'

# 2. Same directory as the source: the old bug deleted the output here.
$r2 = Set-ModelMirror -Source $model -DestinationDirectory $srcDir
Check ($r2.Kind -eq 'in-place') 'source inside destination tree is detected'
Check (Test-Path -LiteralPath $model) 'source survives an in-place mirror'
Check ((Get-Item $model).Length -eq 4096) 'source is untouched by the in-place mirror'

# 3. Collision with an unrelated file of the same name.
$otherDir = Join-Path $root 'third tree'
New-Item -ItemType Directory -Path $otherDir -Force | Out-Null
$unrelated = Join-Path $otherDir 'My Model v1.safetensors'
[IO.File]::WriteAllBytes($unrelated, [byte[]](1..64))
$r3 = Set-ModelMirror -Source $model -DestinationDirectory $otherDir
$aside = Get-ChildItem -LiteralPath $otherDir -Filter '*.replaced-*' | Select-Object -First 1
Check (($r3.Kind -eq 'linked') -and ($r3.Message -match 'earlier file kept')) 'existing unrelated checkpoint is reported'
Check ($null -ne $aside -and (Get-Item $aside.FullName).Length -eq 64) 'unrelated file is preserved, not destroyed'
Check (Test-SameFile $model (Join-Path $otherDir 'My Model v1.safetensors')) 'link replaced the earlier file after setting it aside'

# 4. Cross-volume: linking is impossible, so it must copy and say so.
$crossSrc = Join-Path $env:TEMP ("cross-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $crossSrc -Force | Out-Null
$crossFile = Join-Path $crossSrc 'cross model.safetensors'
[IO.File]::WriteAllBytes($crossFile, (1..4096 | ForEach-Object { $_ % 251 }))
# Pick a destination on a different drive than the source, when one exists.
$otherDrive = @('S:','D:','E:') | Where-Object { (Test-Path "$_\") -and ($_ -ne ([IO.Path]::GetPathRoot($crossFile).TrimEnd('\'))) } | Select-Object -First 1
if ($otherDrive) {
  $crossDst = Join-Path "$otherDrive\" ("crossdst-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Path $crossDst -Force | Out-Null
  $r4 = Set-ModelMirror -Source $crossFile -DestinationDirectory $crossDst
  Check ($r4.Kind -eq 'copied') 'cross-volume falls back to a copy and says so'
  Check (-not (Test-SameFile $crossFile (Join-Path $crossDst 'cross model.safetensors'))) 'copy is a separate file, not a false link'
  Check ((Get-Item (Join-Path $crossDst 'cross model.safetensors')).Length -eq (Get-Item $crossFile).Length) 'copy length matches'
  Remove-Item -LiteralPath $crossDst -Recurse -Force -ErrorAction SilentlyContinue
} else {
  Write-Host 'skip cross-volume case (no second writable volume)'
}

# 5. Missing source must not create anything.
$r5 = Set-ModelMirror -Source (Join-Path $root 'nope.safetensors') -DestinationDirectory $dstDir
Check ($r5.Kind -eq 'error') 'missing source is reported as an error'
Check (-not (Test-Path (Join-Path $dstDir 'nope.safetensors'))) 'missing source creates no destination'

Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $crossSrc -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ("MIRROR TESTS: " + $(if ($script:FAIL -eq 0) { 'PASS' } else { "$script:FAIL FAILED" }))
exit $(if ($script:FAIL -eq 0) { 0 } else { 1 })
