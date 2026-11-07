<#
.SYNOPSIS
  Regression coverage for the CLI surface of TOOLS/image-stack.ps1.

.DESCRIPTION
  0.5.1-0.5.3 shipped a status verb that threw CommandNotFoundException because a
  refactor deleted Get-NodePackDrift and nothing ran the verb. This test closes
  both holes:

    1. every read-only verb runs and exits 0 (help, presets, status, mcp)
    2. the call graph is scanned: any function the script calls that is not
       defined and does not resolve as a command fails the test - that is the
       exact shape of the deleted-helper bug, caught statically
    3. merge/generate/install error paths are exercised with bad arguments and
       must refuse with a clear message, before any model, GPU or disk work
    4. merge never writes when it refuses

  Nothing here downloads a model, touches a user installation or renders.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $repo 'TOOLS/image-stack.ps1'
$fail = 0
$skip = 0

function Pass($m) { Write-Host "ok   $m" }
function Fail($m) { Write-Host "FAIL $m"; $script:fail++ }
function Skip($m) { Write-Host "skip $m"; $script:skip++ }

function Invoke-Cli {
  param([string[]]$CliArgs)
  # Native stderr becomes an ErrorRecord; with Stop that would abort the suite on
  # the first bad verb instead of reporting every check.
  $ErrorActionPreference = 'Continue'
  try {
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $cli @CliArgs 2>&1
    $code = $LASTEXITCODE
  } catch {
    $out = $_.Exception.Message
    $code = 1
  } finally {
    $ErrorActionPreference = 'Stop'
  }
  [pscustomobject]@{ Exit = $code; Text = ($out | Out-String) }
}

# --- 1. read-only verbs -----------------------------------------------------
$checks = @(
  @{ Name = 'help';    Args = @('help');    Match = 'image-stack' },
  @{ Name = 'presets'; Args = @('presets'); Match = 'Anima' },
  @{ Name = 'status';  Args = @('status');  Match = 'image stack status' },
  @{ Name = 'mcp';     Args = @('mcp');     Match = 'mcpServers' }
)
foreach ($c in $checks) {
  $r = Invoke-Cli $c.Args
  if ($r.Exit -eq 0 -and $r.Text -match [regex]::Escape($c.Match)) {
    Pass "verb $($c.Name) exits 0"
  } else {
    Fail "verb $($c.Name) exit=$($r.Exit) expected text '$($c.Match)'"
  }
}

# --- 2. call graph: no call to a missing helper ------------------------------
$files = @($cli) + @(Get-ChildItem (Join-Path $repo 'TOOLS') -Filter '*-lib.ps1' | ForEach-Object FullName)
$defined = @{}
$calls = @{}
foreach ($f in $files) {
  $errs = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$errs)
  if ($errs -and $errs.Count) { Fail "parse $([IO.Path]::GetFileName($f)): $($errs[0].Message)" }
  foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    $defined[$fn.Name] = $true
  }
  foreach ($cmd in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    $head = $cmd.GetCommandName()
    if (-not $head) { continue }                       # $var | & { }
    if ($head -match '^(foreach|if|while|switch|return|throw)$') { continue }
    $calls[$head] = $true
  }
}
$missing = @()
foreach ($name in $calls.Keys) {
  if ($defined.ContainsKey($name)) { continue }
  if (Get-Command $name -ErrorAction SilentlyContinue) { continue }
  $missing += $name
}
if ($missing.Count -eq 0) {
  Pass "call graph resolves ($($calls.Count) commands, $($defined.Count) local functions)"
} else {
  Fail "calls to undefined helpers: $($missing -join ', ')"
}

# The specific regression this test exists for.
if ($defined.ContainsKey('Get-NodePackDrift')) {
  Pass 'Get-NodePackDrift is defined'
} else {
  Fail 'Get-NodePackDrift missing - status will throw'
}

# --- 3. error paths refuse cleanly, before side effects ---------------------
$r = Invoke-Cli @('merge')
if ($r.Exit -ne 0 -and $r.Text -match 'donor') {
  Pass 'merge without donors refuses'
} else {
  Fail "merge without donors: exit=$($r.Exit)"
}

# Weight-count validation sits behind the base check, and reaching it honestly
# needs a real checkpoint - that path is covered by TESTS/test_merge.py.

$r = Invoke-Cli @('merge', '-Donors', 'no-such-file.safetensors', '-Weights', '0.5', '-Out', (Join-Path $env:TEMP 'should-not-exist.safetensors'))
$written = Test-Path -LiteralPath (Join-Path $env:TEMP 'should-not-exist.safetensors')
if ($r.Exit -ne 0 -and -not $written) {
  Pass 'merge with a missing donor refuses and writes nothing'
} else {
  Fail "merge missing donor: exit=$($r.Exit) wrote=$written"
}

$r = Invoke-Cli @('generate', '-Preset', 'no such preset', '-Count', '1')
if ($r.Exit -ne 0 -and $r.Text -match 'preset') {
  Pass 'generate with an unknown preset refuses'
} else {
  Fail "generate unknown preset: exit=$($r.Exit)"
}

# --- 4. install: only safe where no real SwarmUI exists ---------------------
$swarm = @('S:\Misc\SwarmUI', 'C:\SwarmUI') | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($swarm) {
  Skip "install not run: a real SwarmUI exists at $swarm (would install for real)"
} else {
  $r = Invoke-Cli @('install')
  if ($r.Exit -ne 0 -and $r.Text -match 'SwarmUI|not found') {
    Pass 'install refuses without a SwarmUI install'
  } else {
    Fail "install without SwarmUI: exit=$($r.Exit)"
  }
}

Write-Host ''
if ($fail -gt 0) {
  Write-Host "CLI SURFACE: FAIL ($fail failed, $skip skipped)"
  exit 1
}
Write-Host "CLI SURFACE: PASS ($skip skipped)"
exit 0
