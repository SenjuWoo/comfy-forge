# Model-file installation as one transaction.
#
# History worth keeping: the installer once moved an existing checkpoint aside
# and then called a logging helper that did not exist, outside the try block, so
# a logging failure could leave the user with no file at the destination. It also
# claimed resumable downloads while staging to a fresh GUID name every run, which
# makes `curl -C -` a no-op - there was never a partial to resume from.
#
# This module stages to a stable partial tied to the expected artifact identity,
# resumes only when that partial is provably the same file, publishes through a
# seam the tests can fail, and never deletes anything it did not create.
#
# Dot-sourced by TOOLS/Ensure-AnimaSwarmUI.ps1 and by TESTS/Test-ModelInstall.ps1
# so the tests exercise this code rather than a copy of it.

function Get-ModelPartialIdentity {
    param([string]$Sha256, [long]$Bytes)
    # Ties a partial file to one exact expected artifact: a changed manifest must
    # never resume onto the previous build's bytes.
    return ("$Sha256|$Bytes")
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
    $partial = $Destination + '.partial'
    $sidecar = $partial + '.id'
    $lockPath = $Destination + '.install-lock'
    $backup = $null
    $movedAside = $false
    $resumed = $false
    $lock = $null

    if (Test-Path -LiteralPath $Destination -PathType Container) {
        return @{ State = 'failed'; Message = 'destination is a directory: ' + $Destination }
    }

    # Fresh ComfyUI installs often have `models/` and nothing under it. The
    # staging file lives beside the destination, so the category folder has to
    # exist before the first download or the install fails on a missing path.
    $parentDir = Split-Path -Parent $Destination
    if ($parentDir -and -not (Test-Path -LiteralPath $parentDir)) {
        try { New-Item -ItemType Directory -Path $parentDir -Force | Out-Null }
        catch { return @{ State = 'failed'; Message = 'cannot create ' + $parentDir } }
    }

    # Crash recovery: a process killed between "move destination aside" and
    # "move the verified partial in" leaves the destination missing and the old
    # file under .replaced-*. Put the newest one back before doing anything else.
    if (-not (Test-Path -LiteralPath $Destination)) {
        $parent = Split-Path -Parent $Destination
        $leaf = Split-Path -Leaf $Destination
        $orphans = @(Get-ChildItem -LiteralPath $parent -Filter ($leaf + '.replaced-*') -ErrorAction SilentlyContinue |
                     Sort-Object LastWriteTimeUtc -Descending)
        if ($orphans.Count -gt 0) {
            Move-Item -LiteralPath $orphans[0].FullName -Destination $Destination -Force
            Write-Log ('recovered ' + $leaf + ' from an interrupted install')
        }
    }

    # Already correct: leave it completely alone.
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $ExpectedSha256) {
            return @{ State = 'unchanged'; Message = 'already present and verified' }
        }
    }

    # A partial belonging to a different artifact must not be resumed onto.
    if (Test-Path -LiteralPath $partial) {
        $known = ''
        if (Test-Path -LiteralPath $sidecar) { $known = (Get-Content -LiteralPath $sidecar -Raw).Trim() }
        if ($known -ne $identity) {
            Remove-Item -LiteralPath $partial, $sidecar -Force -ErrorAction SilentlyContinue
            Write-Log 'discarded a partial download that belongs to a different file'
        }
    }

    # One installer at a time per destination: two would corrupt the partial and
    # both would fail slowly.
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { return @{ State = 'failed'; Message = 'another install is already writing ' + $Destination } }

    try {
        if (-not (Test-Path -LiteralPath $sidecar)) {
            Set-Content -LiteralPath $sidecar -Value $identity -NoNewline -Encoding ascii
        }
        $have = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0 }
        $resumed = $have -gt 0
        if ($resumed) { Write-Log ('resuming at ' + $have + ' bytes') }

        for ($attempt = 1; $attempt -le 2; $attempt++) {
            if ($Downloader) { & $Downloader $Url $partial $resumed }
            elseif (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                # -C - asks only for the missing range. -sS keeps curl's progress
                # meter off stderr, where it becomes a terminating error under
                # EAP=Stop. stderr goes to a file so $LASTEXITCODE stays curl's.
                $curlArgs = @('-sS', '-L', '--fail')
                if ($resumed) { $curlArgs += @('-C', '-') }
                $curlArgs += @('--output', $partial, $Url)
                $errLog = Join-Path ([IO.Path]::GetTempPath()) ('comfy-kit-curl-' + [guid]::NewGuid().ToString('N') + '.log')
                try {
                    & curl.exe @curlArgs 2> $errLog
                    $code = [int]$LASTEXITCODE
                    if ($code -ne 0) {
                        $tail = if (Test-Path -LiteralPath $errLog) { (Get-Content -LiteralPath $errLog -Tail 3) -join ' | ' } else { '' }
                        throw ('curl download failed: ' + $tail)
                    }
                }
                finally { Remove-Item -LiteralPath $errLog -Force -ErrorAction SilentlyContinue }
            }
            else {
                # No curl means no range support: this is a full restart, not a
                # resume, and the code says so rather than pretending.
                $headers = @{ 'User-Agent' = 'Ultimate-AI-Starter-Bundle-Anima' }
                Invoke-WebRequest -Uri $Url -OutFile $partial -Headers $headers -TimeoutSec 3600 -UseBasicParsing
            }

            $size = (Get-Item -LiteralPath $partial).Length
            if ($size -eq $ExpectedBytes -and
                (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -eq $ExpectedSha256) { break }

            if ($attempt -eq 1) {
                # A server that ignored the range appends duplicate bytes; start
                # clean once instead of publishing a longer, wrong file.
                Write-Log 'partial did not verify after resume; downloading from scratch'
                Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
                $resumed = $false
                continue
            }
            throw ('download verification failed: got ' + $size + ' bytes')
        }

        if (Test-Path -LiteralPath $Destination) {
            $backup = $Destination + '.replaced-' + (Get-Date -Format 'yyyyMMdd-HHmmss') +
                      '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
            Move-Item -LiteralPath $Destination -Destination $backup -Force
            $movedAside = $true
            Write-Log ('previous file kept as ' + (Split-Path -Leaf $backup))
        }

        # Publication seam: tests inject a failure here, after the backup exists
        # and before the destination is replaced.
        if ($Publish) { & $Publish $partial $Destination }
        else { Move-Item -LiteralPath $partial -Destination $Destination -Force }

        Remove-Item -LiteralPath $sidecar -Force -ErrorAction SilentlyContinue
        Write-Log ('installed ' + (Split-Path -Leaf $Destination))
        return @{ State = 'installed'; Message = 'verified and installed' }
    }
    catch {
        # Keep the partial as a resume point unless it is provably wrong.
        if ((Test-Path -LiteralPath $partial) -and
            (Get-Item -LiteralPath $partial).Length -gt $ExpectedBytes) {
            Remove-Item -LiteralPath $partial, $sidecar -Force -ErrorAction SilentlyContinue
        }
        if ($movedAside) {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            }
            if ($backup -and (Test-Path -LiteralPath $backup)) {
                try { Move-Item -LiteralPath $backup -Destination $Destination -Force }
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
