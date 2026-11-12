# Model-file installation as one transaction.
#
# History worth keeping: the installer once moved an existing checkpoint aside
# and then called a logging helper that did not exist, outside the try block, so
# a logging failure could leave the user with no file at the destination. It also
# claimed resumable downloads while staging to a fresh GUID name every run, which
# makes `curl -C -` a no-op - there was never a partial to resume from.
#
# Rules this file now enforces:
#   * the lock is taken before anything is examined or mutated;
#   * staging lives in an installer-owned directory whose name IS the expected
#     artifact identity, so a file we did not create can never be mistaken for
#     our partial and is never deleted;
#   * crash recovery reads a transaction marker, never a filename timestamp, so a
#     historical backup is not restored over a destination the user removed;
#   * a verified staged file is published without another network request.
#
# Dot-sourced by TOOLS/Ensure-AnimaSwarmUI.ps1 and by TESTS/Test-ModelInstall.ps1
# so the tests exercise this code rather than a copy of it.

function Get-ModelPartialIdentity {
    param([string]$Sha256, [long]$Bytes)
    # Ties a partial file to one exact expected artifact: a changed manifest must
    # never resume onto the previous build's bytes.
    return ("$Sha256|$Bytes")
}

function Get-ModelStageRoot {
    param([string]$Destination, [string]$Identity)
    # The directory name encodes the identity, so ownership is structural rather
    # than asserted by a sidecar a user could create by accident.
    $tag = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($Identity))).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    return ($Destination + '.stage-' + $tag)
}

function Read-ModelTransaction {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { return $null }
}

function Install-ModelFile {
    <#
      Returns a hashtable: @{ State = 'unchanged'|'installed'|'failed'; Message = '...' }
      Never throws for an expected failure, so the caller can keep going.
    #>
    param(
        [Parameter(Mandatory)] [string]$Destination,
        [Parameter(Mandatory)] [string]$Url,
        [Parameter(Mandatory)] [long]$ExpectedBytes,
        [Parameter(Mandatory)] [string]$ExpectedSha256,
        # Test seams: the download and the publication step. Everything else is
        # the real implementation.
        [scriptblock]$Downloader = $null,
        [scriptblock]$Publish = $null,
        [scriptblock]$Log = $null
    )

    function Write-Log([string]$m) {
        # Logging must never break the transaction.
        if ($null -eq $Log) { return }
        try { & $Log $m } catch { }
    }

    $identity = Get-ModelPartialIdentity -Sha256 $ExpectedSha256 -Bytes $ExpectedBytes
    $stage = Get-ModelStageRoot -Destination $Destination -Identity $identity
    $data = Join-Path $stage 'data'
    $idFile = Join-Path $stage 'id'
    $txn = $Destination + '.install-txn.json'
    $lockPath = $Destination + '.install-lock'
    $backup = $null
    $movedAside = $false
    $resumed = $false
    $lock = $null

    if (Test-Path -LiteralPath $Destination -PathType Container) {
        return @{ State = 'failed'; Message = 'destination is a directory: ' + $Destination }
    }

    # Fresh ComfyUI installs often have `models/` and nothing under it. The
    # staging directory lives beside the destination, so the category folder has
    # to exist before the first download or the install fails on a missing path.
    $parentDir = Split-Path -Parent $Destination
    if ($parentDir -and -not (Test-Path -LiteralPath $parentDir)) {
        try { New-Item -ItemType Directory -Path $parentDir -Force | Out-Null }
        catch { return @{ State = 'failed'; Message = 'cannot create ' + $parentDir } }
    }

    # One installer at a time per destination, taken before any inspection so two
    # processes cannot both decide the transaction state.
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { return @{ State = 'failed'; Message = 'another install is already writing ' + $Destination } }

    try {
        # Already correct: leave it completely alone.
        if (Test-Path -LiteralPath $Destination) {
            if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $ExpectedSha256) {
                Remove-Item -LiteralPath $txn -Force -ErrorAction SilentlyContinue
                return @{ State = 'unchanged'; Message = 'already present and verified' }
            }
        }

        # Crash recovery, inside the lock and only from a recorded transaction.
        # A `.replaced-*` file with no marker is a historical backup - the user
        # may have deleted the destination on purpose, so it stays where it is.
        $pending = Read-ModelTransaction -Path $txn
        if ($pending -and -not (Test-Path -LiteralPath $Destination)) {
            if ($pending.identity -eq $identity -and $pending.backup -and
                (Test-Path -LiteralPath $pending.backup)) {
                Move-Item -LiteralPath $pending.backup -Destination $Destination -Force
                Write-Log ('recovered ' + (Split-Path -Leaf $Destination) + ' from an interrupted install')
            }
            Remove-Item -LiteralPath $txn -Force -ErrorAction SilentlyContinue
        }

        # Resume only from our own staging directory. Anything else is left
        # untouched: a partial we did not create may be another tool's file.
        $have = 0
        if (Test-Path -LiteralPath $stage) {
            $known = if (Test-Path -LiteralPath $idFile) { (Get-Content -LiteralPath $idFile -Raw).Trim() } else { '' }
            if ($known -ne $identity) {
                Write-Log ('leaving an unrecognised staging directory alone: ' + (Split-Path -Leaf $stage))
                $stage = Join-Path $parentDir ('.comfy-forge-stage-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
                $data = Join-Path $stage 'data'
                $idFile = Join-Path $stage 'id'
            }
        }
        if (-not (Test-Path -LiteralPath $stage)) {
            New-Item -ItemType Directory -Path $stage -Force | Out-Null
            # Identity first: a directory without it is not ours to resume.
            Set-Content -LiteralPath $idFile -Value $identity -NoNewline -Encoding ascii
        }
        if (Test-Path -LiteralPath $data) { $have = (Get-Item -LiteralPath $data).Length }

        # A staged file that already verifies is published without a network call.
        $stagedOk = $have -eq $ExpectedBytes -and
                    (Get-FileHash -LiteralPath $data -Algorithm SHA256).Hash -eq $ExpectedSha256
        if ($stagedOk) {
            Write-Log 'staged file already verifies; publishing without downloading'
        }
        else {
            $resumed = $have -gt 0
            if ($resumed) { Write-Log ('resuming at ' + $have + ' bytes') }

            for ($attempt = 1; $attempt -le 2; $attempt++) {
                if ($Downloader) { & $Downloader $Url $data $resumed }
                elseif (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                    # -C - asks only for the missing range. -sS keeps curl's progress
                    # meter off stderr, where it becomes a terminating error under
                    # EAP=Stop. stderr goes to a file so $LASTEXITCODE stays curl's.
                    $curlArgs = @('-sS', '-L', '--fail')
                    if ($resumed) { $curlArgs += @('-C', '-') }
                    $curlArgs += @('--output', $data, $Url)
                    $errLog = Join-Path ([IO.Path]::GetTempPath()) ('comfy-forge-curl-' + [guid]::NewGuid().ToString('N') + '.log')
                    try {
                        # PS 5.1 turns redirected native stderr into a terminating
                        # error under EAP=Stop, which would bypass the exit-code
                        # handling below (curl 33 = server cannot resume).
                        $prevEap = $ErrorActionPreference
                        $ErrorActionPreference = 'Continue'
                        try { & curl.exe @curlArgs 2> $errLog }
                        finally { $ErrorActionPreference = $prevEap }
                        $code = [int]$LASTEXITCODE
                        if ($code -ne 0) {
                            $tail = if (Test-Path -LiteralPath $errLog) { (Get-Content -LiteralPath $errLog -Tail 3) -join ' | ' } else { '' }
                            # 33 is curl's "server does not support byte ranges".
                            # Restart clean once; any other failure is real.
                            if ($resumed -and $code -eq 33) {
                                Write-Log 'server rejected the range; restarting the download from zero'
                                Remove-Item -LiteralPath $data -Force -ErrorAction SilentlyContinue
                                $resumed = $false
                                continue
                            }
                            throw ('curl download failed (' + $code + '): ' + $tail)
                        }
                    }
                    finally { Remove-Item -LiteralPath $errLog -Force -ErrorAction SilentlyContinue }
                }
                else {
                    # No curl means no range support: this is a full restart, not a
                    # resume, and the code says so rather than pretending.
                    $headers = @{ 'User-Agent' = 'comfy-forge' }
                    Invoke-WebRequest -Uri $Url -OutFile $data -Headers $headers -TimeoutSec 3600 -UseBasicParsing
                }

                $size = (Get-Item -LiteralPath $data).Length
                if ($size -eq $ExpectedBytes -and
                    (Get-FileHash -LiteralPath $data -Algorithm SHA256).Hash -eq $ExpectedSha256) { break }

                if ($attempt -eq 1) {
                    # A server that ignored the range appends duplicate bytes; start
                    # clean once instead of publishing a longer, wrong file.
                    Write-Log 'staged file did not verify after resume; downloading from scratch'
                    Remove-Item -LiteralPath $data -Force -ErrorAction SilentlyContinue
                    $resumed = $false
                    continue
                }
                throw ('download verification failed: got ' + $size + ' bytes')
            }
        }

        if (Test-Path -LiteralPath $Destination) {
            $backup = $Destination + '.replaced-' + (Get-Date -Format 'yyyyMMdd-HHmmss') +
                      '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
            # Marker before the move: recovery needs proof of which transaction
            # this backup belongs to, not a timestamp guess.
            @{ identity = $identity; backup = $backup; destination = $Destination } |
                ConvertTo-Json -Compress | Set-Content -LiteralPath $txn -NoNewline -Encoding utf8
            Move-Item -LiteralPath $Destination -Destination $backup -Force
            $movedAside = $true
            Write-Log ('previous file kept as ' + (Split-Path -Leaf $backup))
        }

        # Publication seam: tests inject a failure here, after the backup exists
        # and before the destination is replaced.
        if ($Publish) { & $Publish $data $Destination }
        else { Move-Item -LiteralPath $data -Destination $Destination -Force }

        Remove-Item -LiteralPath $txn -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        Write-Log ('installed ' + (Split-Path -Leaf $Destination))
        return @{ State = 'installed'; Message = 'verified and installed' }
    }
    catch {
        # Keep the staged bytes as a resume point unless they are provably wrong.
        if ((Test-Path -LiteralPath $data) -and
            (Get-Item -LiteralPath $data).Length -gt $ExpectedBytes) {
            Remove-Item -LiteralPath $data -Force -ErrorAction SilentlyContinue
        }
        if ($movedAside) {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            }
            if ($backup -and (Test-Path -LiteralPath $backup)) {
                try {
                    Move-Item -LiteralPath $backup -Destination $Destination -Force
                    Remove-Item -LiteralPath $txn -Force -ErrorAction SilentlyContinue
                }
                catch {
                    # Never lose the old file silently: name where it is.
                    return @{ State = 'failed'
                              Message = ('publication failed and the previous file could not be restored; it is at ' + $backup) }
                }
            }
        }
        return @{ State = 'failed'; Message = $_.Exception.Message }
    }
    finally {
        if ($lock) { $lock.Dispose() }
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}
