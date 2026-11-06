# Model-file installation as one transaction.
#
# The installer used to move an existing checkpoint out of the way and then call
# a logging helper that did not exist, so a logging failure could leave the user
# with no file at the destination. This module stages first, verifies the bytes,
# and only then touches the destination - and it never depends on a helper that
# might be missing.
#
# Dot-sourced by TOOLS/Ensure-AnimaSwarmUI.ps1 and by TESTS/Test-ModelInstall.ps1
# so the tests exercise this exact code rather than a copy of it.

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
        # Test seam: replaces the download only. Everything else is real.
        [scriptblock]$Downloader = $null,
        [scriptblock]$Log = $null
    )

    function Write-Log([string]$m) {
        # Logging must never break the transaction.
        if ($null -eq $Log) { return }
        try { & $Log $m } catch { }
    }

    $staging = $Destination + '.download-' + [guid]::NewGuid().ToString('N').Substring(0, 10)
    $backup = $null
    $movedAside = $false

    # 0. Anything at the destination that is not a file is a layout error, not
    #    something to move aside.
    if (Test-Path -LiteralPath $Destination -PathType Container) {
        return @{ State = 'failed'; Message = 'destination is a directory: ' + $Destination }
    }

    # 1. A verified destination is left completely alone.
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $ExpectedSha256) {
            return @{ State = 'unchanged'; Message = 'already present and verified' }
        }
    }

    try {
        # 2. Stage the download next to the destination; the real file is not
        #    touched until verified bytes exist.
        Write-Log ('downloading ' + (Split-Path -Leaf $Destination))
        if ($Downloader) { & $Downloader $Url $staging }
        else {
            # curl is preferred: -C - resumes a partial download and -sS keeps a
            # progress meter from becoming a terminating error under
            # $ErrorActionPreference='Stop'. stderr goes to a file, never the
            # pipeline, so $LASTEXITCODE stays curl's own.
            $curl = $null
            foreach ($c in @(Get-Command curl.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
                if ($c.Source) { $curl = $c.Source; break }
            }
            if ($curl) {
                $errLog = Join-Path ([IO.Path]::GetTempPath()) ('comfy-kit-curl-' + [guid]::NewGuid().ToString('N') + '.log')
                try {
                    & $curl -sS -L --fail -C - --output $staging $Url 2> $errLog
                    if ([int]$LASTEXITCODE -ne 0) {
                        $tail = if (Test-Path -LiteralPath $errLog) { (Get-Content -LiteralPath $errLog -Tail 3) -join ' | ' } else { '' }
                        throw ('curl download failed: ' + $tail)
                    }
                }
                finally { Remove-Item -LiteralPath $errLog -Force -ErrorAction SilentlyContinue }
            } else {
                $headers = @{ 'User-Agent' = 'Ultimate-AI-Starter-Bundle-Anima' }
                Invoke-WebRequest -Uri $Url -OutFile $staging -Headers $headers -TimeoutSec 3600 -UseBasicParsing
            }
        }

        # 3. Verify size and hash before publication.
        $size = (Get-Item -LiteralPath $staging).Length
        if ($size -ne $ExpectedBytes) {
            throw ('size mismatch: expected ' + $ExpectedBytes + ' bytes, got ' + $size)
        }
        if ((Get-FileHash -LiteralPath $staging -Algorithm SHA256).Hash -ne $ExpectedSha256) {
            throw 'SHA-256 mismatch for the downloaded file'
        }

        # 4. Keep the previous file, but only now that a good replacement exists.
        if (Test-Path -LiteralPath $Destination) {
            $backup = $Destination + '.replaced-' + (Get-Date -Format 'yyyyMMdd-HHmmss') +
                      '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
            Move-Item -LiteralPath $Destination -Destination $backup -Force
            $movedAside = $true
            Write-Log ('previous file kept as ' + (Split-Path -Leaf $backup))
        }

        # 5. Publish.
        Move-Item -LiteralPath $staging -Destination $Destination -Force
        Write-Log ('installed ' + (Split-Path -Leaf $Destination))
        return @{ State = 'installed'; Message = 'verified and installed' }
    }
    catch {
        # 6. Undo anything this operation did; restore what it displaced.
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Force -ErrorAction SilentlyContinue
        }
        if ($movedAside) {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            }
            if ($backup -and (Test-Path -LiteralPath $backup)) {
                try { Move-Item -LiteralPath $backup -Destination $Destination -Force }
                catch { Write-Log 'could not restore the previous file' }
            }
        }
        return @{ State = 'failed'; Message = $_.Exception.Message }
    }
}
