# Mirror helpers: expose a finished checkpoint in a second model tree.
# Dot-sourced by image-stack.ps1 and by TESTS\Test-Mirror.ps1, so the logic can
# be exercised without running the CLI.

function Get-FileIdentity([string]$Path) {
  # (volume serial, file id) is the only reliable Windows answer to "same file?".
  # Length and name say nothing: a copy and a hard link look identical to both.
  $full = [IO.Path]::GetFullPath($Path)
  if (-not (Test-Path -LiteralPath $full)) { return $null }
  $drive = [IO.Path]::GetPathRoot($full).TrimEnd('').TrimEnd(':')
  $idRaw = (& cmd.exe /c "fsutil file queryfileid `"$full`" 2>nul") -join ' '
  if ($idRaw -notmatch 'File ID is 0x([0-9a-fA-F]+)') { return $null }
  $id = $Matches[1]
  $volRaw = (& cmd.exe /c ("fsutil fsinfo volumeinfo {0}: 2>nul" -f $drive)) -join ' '
  $serial = if ($volRaw -match 'Serial Number is 0x([0-9a-fA-F]+)') { $Matches[1] } else { $drive }
  return ("{0}:{1}" -f $serial, $id)
}

function Test-SameFile([string]$A, [string]$B) {
  if (-not (Test-Path -LiteralPath $A) -or -not (Test-Path -LiteralPath $B)) { return $false }
  $ia = Get-FileIdentity $A; $ib = Get-FileIdentity $B
  if (-not $ia -or -not $ib) { return $false }
  return ($ia -eq $ib)
}

function Get-FileHash256([string]$Path) {
  (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Copy-FileVerified([string]$Source, [string]$Destination) {
  # Stage then move, so a failed copy never leaves a truncated file in place.
  # Verified by SHA-256, not length: equal sizes say nothing about equal bytes.
  $staging = "$Destination.staging-" + [guid]::NewGuid().ToString('N').Substring(0, 8)
  Copy-Item -LiteralPath $Source -Destination $staging -Force
  if ((Get-FileHash256 $staging) -ne (Get-FileHash256 $Source)) {
    Remove-Item -LiteralPath $staging -Force -ErrorAction SilentlyContinue
    throw 'copy checksum mismatch'
  }
  Move-Item -LiteralPath $staging -Destination $Destination -Force
}

function Set-ModelMirror {
  param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$DestinationDirectory,
    [string]$Enabled = $true
  )
  $src = [IO.Path]::GetFullPath($Source)
  $dst = [IO.Path]::GetFullPath((Join-Path $DestinationDirectory (Split-Path -Leaf $Source)))

  if (-not (Test-Path -LiteralPath $src)) { return @{ Kind = 'error'; Message = "source not found: $src" } }
  if ($src -ieq $dst) {
    # -Out already lives in the destination tree: mirroring would delete it.
    return @{ Kind = 'in-place'; Message = 'already in the destination tree; nothing to mirror' }
  }
  if ((Test-Path -LiteralPath $dst) -and (Test-SameFile $src $dst)) {
    return @{ Kind = 'linked'; Message = 'already the same file; nothing to do' }
  }
  $aside = $null
  $asideName = $null
  if (Test-Path -LiteralPath $dst) {
    # Never destroy a checkpoint we did not create: set it aside, then continue.
    # GUID suffix so two replacements in one second cannot overwrite each other.
    $aside = "$dst.replaced-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    Move-Item -LiteralPath $dst -Destination $aside -Force
    $asideName = Split-Path -Leaf $aside
  }
  $null = & cmd.exe /c "mklink /H `"$dst`" `"$src`" 2>nul"
  if ((Test-Path -LiteralPath $dst) -and (Test-SameFile $src $dst)) {
    $msg = if ($asideName) { "hard link verified; earlier file kept as $asideName" } else { 'hard link verified' }
    return @{ Kind = 'linked'; Message = $msg }
  }
  # Not the same file id: different volume, or the link did not take.
  if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
  try {
    Copy-FileVerified $src $dst
  }
  catch {
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue }
    # Put back the file we displaced: a failed mirror must not cost the user a
    # checkpoint that was already there.
    if ($aside -and (Test-Path -LiteralPath $aside)) {
      Move-Item -LiteralPath $aside -Destination $dst -Force
      return @{ Kind = 'error'; Message = 'copy failed; previous file restored' }
    }
    return @{ Kind = 'error'; Message = 'copy failed; destination removed' }
  }
  return @{ Kind = 'copied'; Message = 'copied and verified by SHA-256 (different volume)' }
}
