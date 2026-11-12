# Release preflight: run every local check and say plainly whether this tree is
# releasable. It never fakes a pass: a check that cannot run is UNVERIFIED and
# blocks promotion, it does not silently count as green.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS/preflight.ps1
#
# Exit 0 = every check PASSED. Exit 1 = something failed or is unverified.

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
$results = @()

function Add-Result([string]$Name, [string]$State, [string]$Detail) {
    $script:results += [pscustomobject]@{ Check = $Name; State = $State; Detail = $Detail }
}

function Run-Check([string]$Name, [string]$Exe, [string[]]$Arguments, [int]$UnverifiedCode = -1) {
    # A command that never launched must not be judged by the previous command's
    # $LASTEXITCODE, which is how a missing interpreter can look like success.
    $global:LASTEXITCODE = $null
    try {
        $out = & $Exe @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        $launched = $true
    } catch {
        $out = $_.Exception.Message
        $code = $null
        $launched = $false
    }
    $tail = (($out -split "`n" | Where-Object { $_ -match '\S' } | Select-Object -Last 1) -join '').Trim()
    if (-not $launched -or $null -eq $code) { Add-Result $Name 'FAIL' ("could not run: " + $tail); return }
    if ($code -eq 0) { Add-Result $Name 'PASS' $tail }
    elseif ($UnverifiedCode -ge 0 -and $code -eq $UnverifiedCode) { Add-Result $Name 'UNVERIFIED' $tail }
    else { Add-Result $Name 'FAIL' $tail }
}

$py = if ($env:COMFY_PYTHON) { $env:COMFY_PYTHON } else { 'python' }
$jsonOut = if ($env:COMFY_PREFLIGHT_JSON) { $env:COMFY_PREFLIGHT_JSON } else { $null }

Write-Host '== comfy-forge preflight =='
Run-Check 'powershell syntax' 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-PsSyntax.ps1')
Run-Check 'installer gate'    'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-AnimaInstall.ps1')
Run-Check 'cli surface'       'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-CliSurface.ps1')
Run-Check 'mirror'            'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-Mirror.ps1')
Run-Check 'model install'     'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-ModelInstall.ps1')
Run-Check 'node pack state'   'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-NodePackState.ps1')
Run-Check 'release identity'  'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-Release.ps1')
Run-Check 'version'           'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'TESTS\Test-Version.ps1')
Run-Check 'merge math'        $py @('TESTS\test_merge.py')
Run-Check 'merge artifacts'   $py @('TESTS\test_merge_artifacts.py')
Run-Check 'deps resolver'     $py @('TOOLS\check_deps.py', '--self-test')
Run-Check 'workflow structure' $py @('TESTS\validate_workflows.py') 3
Run-Check 'workflow convert'  $py @('TESTS\test_workflow_convert.py')
Run-Check 'workflow lineage gate' $py @('TESTS\validate_workflows.py', '--self-test')
Run-Check 'dependency lock'   $py @('TOOLS\validate-lock.py') 3
Run-Check 'MCP surface'       $py @('TESTS\test_mcp_handshake.py') 3

$head = (& git rev-parse --short HEAD 2>$null | Out-String).Trim()
$dirty = (& git status --porcelain 2>$null | Out-String).Trim()
$version = (Get-Content VERSION -Raw).Trim()

Write-Host ''
foreach ($r in $results) {
    $mark = switch ($r.State) { 'PASS' { 'ok  ' } 'UNVERIFIED' { '??  ' } default { 'XX  ' } }
    Write-Host ("{0} {1,-22} {2}" -f $mark, $r.Check, $r.Detail)
}

$failed = @($results | Where-Object { $_.State -eq 'FAIL' })
$unverified = @($results | Where-Object { $_.State -eq 'UNVERIFIED' })

Write-Host ''
Write-Host ("version  {0}" -f $version)
Write-Host ("commit   {0}{1}" -f $head, $(if ($dirty) { ' (working tree dirty)' } else { '' }))
Write-Host ("checks   {0} passed, {1} failed, {2} unverified" -f @($results | Where-Object { $_.State -eq 'PASS' }).Count, $failed.Count, $unverified.Count)
$machine = [pscustomobject]@{
    tool       = 'comfy-forge preflight'
    version    = $version
    commit     = $head
    dirty      = [bool]$dirty
    local_pass = ($failed.Count -eq 0 -and -not $dirty)
    release_ready = ($failed.Count -eq 0 -and -not $dirty -and $unverified.Count -eq 0)
    checks     = @($results)
}
if ($jsonOut) {
    $machine | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $jsonOut -Encoding utf8
    Write-Host ("evidence {0}" -f $jsonOut)
}
Pop-Location

if ($failed.Count -gt 0) { Write-Host 'RELEASE: BLOCKED (failures)'; exit 1 }
if ($dirty) { Write-Host 'RELEASE: BLOCKED (uncommitted changes)'; exit 1 }
if ($unverified.Count -gt 0) {
    Write-Host 'RELEASE: BLOCKED (unverified checks - run the live integration test where a backend exists)'
    exit 1
}
Write-Host 'RELEASE: ELIGIBLE (all local checks passed; live GPU test is separate)'
exit 0
