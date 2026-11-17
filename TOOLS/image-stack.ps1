<#
.SYNOPSIS
  One entry point for the Anima image stack: check it, install it, generate
  from a preset, merge checkpoints, and wire an agent to ComfyUI.

.DESCRIPTION
  comfy-forge keeps the Anima path on rails: `install` fetches everything that is
  missing (model files, the Anima node pack, the python merge modules), and the
  other verbs do one job each.

  This script is deliberately a thin orchestrator. It never re-implements model
  download (Ensure-AnimaSwarmUI.ps1 owns that) or merge math
  (TOOLS\merge_anima.py owns that). It finds what is installed, then calls the
  right thing and reports the real result.

  Verbs:
    status                        what is installed, running, and available
    capabilities                  the same as one JSON object: every lane (image, music,
                                 3D, dataset, merge, MCP), whether it is ready, and the
                                 command that runs it or fixes it
    start [-Wait <seconds>]       start SwarmUI headless (no window, no browser) and wait
                                 for its API and its ComfyUI backend; standalone ComfyUI
                                 when there is no SwarmUI
    stop [-Force]                 stop the backend `start` launched; one you started
                                 yourself is left alone, and one that is rendering is
                                 not stopped unless -Force is given
    install [-IncludeOptional]    fetch everything missing: models, node pack, deps
    presets [-Name <preset>]      list presets, or show one preset's settings
    generate -Prompt <text> [-Preset <name>] [-Model <name>] [-Negative <text>]
             [-Width n -Height n] [-Seed n] [-Count n] [-Steps n] [-Cfg n] [-Out <dir>]
             [-InitImage <file> [-Creativity 0..1] [-Mask <file>]]   image to image, inpaint
             [-Lora name[:weight],...] [-Transparent] [-Tileable] [-Upscale 1.5]
             [-Set key=value,...] [-Json] [-NoStart]
                                 render through SwarmUI's API. The backend is started
                                 when it is down (-NoStart refuses instead). Without
                                 -Model the preset's model is used, else one SwarmUI has
                                 for that preset's family. Every image gets a .json
                                 sidecar; -Json ends the output with one JSON line.
    merge -Donors a,b [-Weights 0.85,0.75] [-Out <file>] [-Scale 1.0]
                                 4-way Anima checkpoint merge to a real file
    mcp [-Write -Agent <claude|path>]
                                 print a ComfyUI MCP client config, or write it into
                                 a JSON agent config (the old file is kept as a backup)
    python                        print the interpreter this stack uses (the one
                                 that has torch); exit 1 when none is found

  Sibling tools (their arguments would collide with the parameters of this script):
    TOOLS\model3d.ps1     image or text to a 3D model (.glb), optionally game-ready
    TOOLS\music.ps1       songs and instrumentals (ACE-Step 1.5)
    TOOLS\dataset.ps1     tagged image folder to a training archive
    TOOLS\game-ready.ps1  a generated .glb to a mesh under the triangle budget (Asset Forge)
  Weights: TOOLS\Install-3DModels.ps1 and TOOLS\Install-MusicModels.ps1, both opt-in.

.EXAMPLE
  powershell -File TOOLS\image-stack.ps1 status
  powershell -File TOOLS\image-stack.ps1 generate -Prompt "neon rain city" -Preset "Anima - Author Daily (Euler + SGM)"
  powershell -File TOOLS\image-stack.ps1 merge -Donors "a.safetensors","b.safetensors" -Weights 0.85,0.75
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('status', 'capabilities', 'start', 'stop', 'install', 'presets', 'generate', 'merge', 'mcp', 'python', 'help')]
  [string]$Verb = 'status',

  [string]$Preset = 'Anima - Author Daily (Euler + SGM)',
  [string]$Prompt = 'a weathered stone lighthouse on a basalt cliff at dawn, sea spray, volumetric light, cinematic',
  # Empty means "choose": the preset's own model, else one SwarmUI lists for the
  # preset's family. A fixed default named a file many installs do not have.
  [string]$Model = '',
  [string]$Negative = '',
  [string]$InitImage = '',
  [double]$Creativity = -1,
  [string]$Mask = '',
  [string[]]$Lora = @(),
  [switch]$Transparent,
  [switch]$Tileable,
  [double]$Upscale = 0,
  [string[]]$Set = @(),
  [switch]$Json,
  [switch]$NoStart,
  [int]$Wait = 300,
  [int]$Width = 1024,
  [int]$Height = 1024,
  [int]$Steps = 0,
  [double]$Cfg = 0,
  [int]$Seed = -1,
  [int]$Count = 1,
  [string]$Out = '',
  [string]$Name = '',
  [string[]]$Donors = @(),
  # A list, so `-Weights 0.85,0.75` binds from a PowerShell session as well as
  # from `powershell -File`, where the same text arrives as one string.
  [string[]]$Weights = @(),
  [string]$Base = '',
  [double]$Scale = 1.0,
  [string]$Method = 'task-arithmetic',
  [switch]$IncludeOptional,
  [switch]$Force,
  [switch]$AcceptFallbackAnchor,
  [switch]$Write,
  [switch]$NoMirror,
  [string]$Agent = 'hermes',
  [int]$Port = 0,
  # `comfy-forge --help` arrives here as a parameter named "-help".
  [Alias('-help', 'h')]
  [switch]$Help
)

$ErrorActionPreference = 'Stop'
$BoundParams = $PSBoundParameters                 # inside a function that name means the function's own
$Root = Split-Path -Parent $PSScriptRoot          # repo root (this file lives in TOOLS\)
. (Join-Path $PSScriptRoot 'mirror-lib.ps1')      # hard-link/copy primitives
. (Join-Path $PSScriptRoot 'node-pack-lib.ps1')   # node-pack state + clone guard
# Standalone by default; a record left by the old bundle path is still honoured.
# COMFY_FORGE_STATE_DIR moves the whole state folder (install record, backend
# record, logs); the tests use it so they never read or write the real one.
$StateDir = if ($env:COMFY_FORGE_STATE_DIR) { $env:COMFY_FORGE_STATE_DIR } else { Join-Path $env:LOCALAPPDATA 'comfy-kit' }
$StatePath = Join-Path $StateDir 'anima-swarmui.json'
if (-not $env:COMFY_FORGE_STATE_DIR -and -not (Test-Path -LiteralPath $StatePath)) {
  $legacyState = Join-Path $env:LOCALAPPDATA 'Ultimate-AI-Starter-Bundle\anima-swarmui.json'
  if (Test-Path -LiteralPath $legacyState) { $StatePath = $legacyState }
}
$BackendStatePath = Join-Path $StateDir 'backend.json'

function Invoke-Native([string]$Exe, [object[]]$Arguments) {
  # Run a program whose stderr is expected and return @{ Code; Text }. Under
  # ErrorActionPreference 'Stop', Windows PowerShell 5.1 turns a redirected stderr
  # line into a terminating error, so `& python -c "import torch" 2>$null` ended
  # `status` with a traceback on any Python without torch instead of reporting it.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $text = (& $Exe @Arguments 2>&1 | Out-String)
    return @{ Code = [int]$LASTEXITCODE; Text = $text.Trim() }
  }
  catch { return @{ Code = 1; Text = "$_" } }
  finally { $ErrorActionPreference = $prev }
}

function Say($Message) { Write-Host $Message }
function Fail($Message) { Write-Host "error  $Message" -ForegroundColor Red; exit 1 }
function Ok($Message) { Write-Host "ok     $Message" -ForegroundColor Green }
function Note($Message) { Write-Host "..     $Message" }

# ---------------------------------------------------------------------------
# Discovery - read what the installer already recorded instead of re-scanning
# every drive. The installer owns detection; this only reads its result.
# ---------------------------------------------------------------------------
function Test-Frontend {
  # Same markers the installer uses, so a recorded path that has since been
  # moved or deleted is not trusted just because it is written in the state file.
  param([string]$Kind, [string]$Root)
  if (-not $Root -or -not (Test-Path -LiteralPath $Root)) { return $false }
  if ($Kind -eq 'SwarmUI') {
    return (Test-Path -LiteralPath (Join-Path $Root 'SwarmUI.sln')) -and
           (Test-Path -LiteralPath (Join-Path $Root 'Data\Backends.fds')) -and
           (Test-Path -LiteralPath (Join-Path $Root 'Models'))
  }
  return (Test-Path -LiteralPath (Join-Path $Root 'main.py')) -and
         (Test-Path -LiteralPath (Join-Path $Root 'comfy\samplers.py')) -and
         (Test-Path -LiteralPath (Join-Path $Root 'models'))
}

function Get-Frontends {
  if (-not (Test-Path -LiteralPath $StatePath)) { return @() }
  $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
  return @($state.frontends | Where-Object { Test-Frontend -Kind $_.kind -Root $_.root })
}

function Get-SwarmRoot {
  $swarm = Get-Frontends | Where-Object { $_.kind -eq 'SwarmUI' } | Select-Object -First 1
  if ($swarm) { return $swarm.root }
  if ($env:SWARMUI_ROOT -and (Test-Path -LiteralPath $env:SWARMUI_ROOT)) { return $env:SWARMUI_ROOT }
  return $null
}

function Get-ComfyRoot {
  $comfy = Get-Frontends | Where-Object { $_.kind -eq 'ComfyUI' } | Select-Object -First 1
  if ($comfy) { return $comfy.root }
  if ($env:COMFYUI_ROOT -and (Test-Path -LiteralPath $env:COMFYUI_ROOT)) { return $env:COMFYUI_ROOT }
  # Standalone installs (portable, venv or plain checkout) are not in the state
  # file, so look in the usual places before giving up.
  $candidates = @(
    (Join-Path $HOME 'ComfyUI'),
    (Join-Path $HOME 'Documents\ComfyUI'),
    'C:\ComfyUI',
    'C:\ComfyUI_windows_portable\ComfyUI',
    'D:\ComfyUI'
  )
  foreach ($c in $candidates) {
    if (Test-Frontend -Kind 'ComfyUI' -Root $c) { return $c }
  }
  return $null
}

function Read-BackendRecord {
  # The backend `start` launched, or $null. A record whose process is gone, or
  # whose pid now belongs to a different process, is stale and is removed, so
  # `stop` can never end something this tool did not start.
  if (-not (Test-Path -LiteralPath $BackendStatePath)) { return $null }
  $rec = $null
  try { $rec = Get-Content -LiteralPath $BackendStatePath -Raw | ConvertFrom-Json } catch { }
  if ($rec -and $rec.pid -and $rec.started) {
    try {
      $proc = Get-Process -Id ([int]$rec.pid) -ErrorAction Stop
      if ($proc.StartTime.ToUniversalTime().Ticks -eq [int64]$rec.started) { return $rec }
    }
    catch { }
  }
  Remove-Item -LiteralPath $BackendStatePath -Force -ErrorAction SilentlyContinue
  return $null
}

# SwarmUI's web port comes from its own settings file, not a guess. A SwarmUI this
# tool started may have moved to the next free port; its record says which.
function Get-SwarmPort {
  $rec = Read-BackendRecord
  if ($rec -and $rec.kind -eq 'SwarmUI' -and $rec.port) { return [int]$rec.port }
  $swarm = Get-SwarmRoot
  if (-not $swarm) { return 7801 }
  $settings = Join-Path $swarm 'Data\Settings.fds'
  if (Test-Path -LiteralPath $settings) {
    $line = Select-String -LiteralPath $settings -Pattern '^\s*Port\s*:\s*(\d+)' | Select-Object -First 1
    if ($line) { return [int]$line.Matches[0].Groups[1].Value }
  }
  return 7801
}

function Get-OpenLocalPorts([int[]]$Ports, [int]$TimeoutMs = 500) {
  # Windows takes about two seconds to report a refused connection to 127.0.0.1,
  # so asking 14 ports one after another cost `status` half a minute whenever no
  # backend was up. Every port is asked at once and the wait is shared.
  $pending = @()
  foreach ($p in $Ports) {
    $client = New-Object Net.Sockets.TcpClient
    try { $pending += @{ Port = $p; Client = $client; Async = $client.BeginConnect('127.0.0.1', $p, $null, $null) } }
    catch { $client.Close() }
  }
  $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
  $open = @()
  foreach ($item in $pending) {
    $left = [int][Math]::Max(0, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
    try { if ($item.Async.AsyncWaitHandle.WaitOne($left) -and $item.Client.Connected) { $open += $item.Port } }
    catch { }
    finally { $item.Client.Close() }
  }
  return , @($open)
}

function Test-SwarmUp([int]$P) {
  if (-not (Get-OpenLocalPorts @($P)).Count) { return $false }
  try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$P/API/GetNewSession" -Method Post `
      -ContentType 'application/json' -Body '{}' -TimeoutSec 4 -UseBasicParsing
    return $true
  }
  catch { return $false }
}

function New-SwarmSession([int]$P) {
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:$P/API/GetNewSession" -Method Post `
    -ContentType 'application/json' -Body '{}' -TimeoutSec 10 -UseBasicParsing
  return $r.session_id
}

# ComfyUI can be a standalone install (8188) or SwarmUI's own backend, which
# starts at BackendStartingPort (7820). Probe for /system_stats instead of
# assuming a port number.
function Get-ComfyUIPort {
  # Asked more than once per run (status prints it twice); the answer does not change.
  if ($null -ne $script:ComfyUIPortCache) { return $script:ComfyUIPortCache }
  $found = 0
  # An open socket is not a ComfyUI: only a port that answers /system_stats counts.
  foreach ($p in (Get-OpenLocalPorts (Get-ProbePorts))) {
    try {
      $r = Invoke-RestMethod -Uri "http://127.0.0.1:$p/system_stats" -TimeoutSec 5 -UseBasicParsing
      if ($r.system) { $found = $p; break }
    }
    catch { }
  }
  $script:ComfyUIPortCache = $found
  return $found
}

function Get-ProbePorts {
  # 8188 is a standalone ComfyUI, 7820-7832 SwarmUI's backend. The tests narrow
  # the search with the variable TOOLS\music.py and TOOLS\model3d.py also read.
  if ($env:COMFY_FORGE_PROBE_PORTS) {
    return , @($env:COMFY_FORGE_PROBE_PORTS -split ',' | Where-Object { $_.Trim() } | ForEach-Object { [int]$_ })
  }
  return , (@(8188) + (7820..7832))
}

# ---------------------------------------------------------------------------
# SwarmUI API. Requests and replies are UTF-8 on the wire: Windows PowerShell 5.1
# sends a string body as ISO-8859-1 and decodes a reply the same way, which turned
# every non-Latin character of a prompt into "?" and broke the path of any image
# SwarmUI named after that prompt.
# ---------------------------------------------------------------------------
function Invoke-SwarmApi([int]$P, [string]$Route, [hashtable]$Body, [int]$TimeoutSec = 60) {
  $bytes = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 8 -Compress))
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$P/API/$Route" -Method Post `
    -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec $TimeoutSec -UseBasicParsing
  return ([Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray()) | ConvertFrom-Json)
}

function Get-SwarmBackendState([int]$P) {
  # 'running' once SwarmUI's ComfyUI backend has loaded; 'loading' before that.
  try {
    $r = Invoke-SwarmApi $P 'GetCurrentStatus' @{ session_id = (New-SwarmSession $P) } 10
    return [string]$r.backend_status.status
  }
  catch { return '' }
}

# ---------------------------------------------------------------------------
# Backend lifecycle. An agent cannot click launch-windows.bat, and SwarmUI's
# normal launch opens a browser. `start` runs the same launcher with
# --launch_mode none, hidden, and waits until a render would actually work.
# ---------------------------------------------------------------------------
function New-BackendLog {
  $dir = Join-Path $StateDir 'logs'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  # Keep the last few runs; a log per start would otherwise grow without bound.
  @(Get-ChildItem -LiteralPath $dir -Filter 'backend-*.log*' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 10) |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
  return (Join-Path $dir ("backend-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID))
}

function Get-LogTail([string]$Path, [int]$Lines = 12) {
  if (-not (Test-Path -LiteralPath $Path)) { return '' }
  return (@(Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue) -join "`n")
}

function Start-HiddenProcess([string]$CommandLine, [string]$WorkDir, [string]$Log) {
  # The backend outlives this command, so it must not hold this command's output.
  # Start-Process with -Redirect* lets the child inherit every open handle, the
  # caller's stdout pipe included: a program that captures `image-stack.ps1 start`
  # (an agent, Asset Forge, a test) then waits for that pipe until the backend
  # exits. Without -Redirect* the process is created through the shell and inherits
  # nothing; cmd.exe does the redirection itself.
  # stdin is NUL: the launcher ends in `pause` when SwarmUI fails, and a hidden
  # window waiting for a key would never report that failure.
  $line = '/d /s /c "{0} > "{1}" 2>&1 < NUL"' -f $CommandLine, $Log
  return (Start-Process -FilePath $env:ComSpec -ArgumentList $line -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru)
}

function Save-BackendRecord([string]$Kind, $Proc, [int]$BackendPort, [string]$BackendRoot, [string]$Log) {
  $rec = [ordered]@{
    kind = $Kind; pid = $Proc.Id; started = $Proc.StartTime.ToUniversalTime().Ticks
    port = $BackendPort; root = $BackendRoot; log = $Log
    since = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  }
  New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
  [IO.File]::WriteAllText($BackendStatePath, ($rec | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
}

function Start-Backend {
  # Idempotent: a backend that already answers is used as it is, whoever started it.
  $swarm = Get-SwarmRoot
  if ($swarm) {
    $port = if ($Port) { $Port } else { Get-SwarmPort }
    if (Test-SwarmUp $port) {
      if ((Get-SwarmBackendState $port) -ne 'running') { Wait-SwarmBackend $port $null '' }
      Ok "SwarmUI is already answering on http://127.0.0.1:$port"
      return
    }
    $launcher = Join-Path $swarm 'launch-windows.bat'
    if (-not (Test-Path -LiteralPath $launcher)) { Fail "missing $launcher" }
    $log = New-BackendLog
    Note "starting SwarmUI headless from $swarm (log: $log)"
    # The launcher is named by its full path: with NoDefaultCurrentDirectoryInExePath
    # set, cmd.exe does not look in the working directory for a bare name.
    $proc = Start-HiddenProcess ('"{0}" --launch_mode none' -f $launcher) $swarm $log
    $deadline = (Get-Date).AddSeconds($Wait)
    $webPort = 0
    while ((Get-Date) -lt $deadline) {
      if ($proc.HasExited) {
        Fail ("SwarmUI exited during startup (code {0}). Log: {1}`n{2}" -f $proc.ExitCode, $log, (Get-LogTail $log))
      }
      # SwarmUI takes the next port when its own is busy, and says so in the log.
      $candidate = $port
      $line = Select-String -LiteralPath $log -Pattern 'Starting webserver on https?://[^:\s]+:(\d+)' -ErrorAction SilentlyContinue | Select-Object -Last 1
      if ($line) { $candidate = [int]$line.Matches[0].Groups[1].Value }
      if (Test-SwarmUp $candidate) { $webPort = $candidate; break }
      Start-Sleep -Milliseconds 700
    }
    if (-not $webPort) {
      $null = Invoke-Native 'taskkill' @('/PID', $proc.Id, '/T', '/F')
      Fail ("SwarmUI did not answer within {0}s; it was stopped again. Log: {1}`n{2}" -f $Wait, $log, (Get-LogTail $log))
    }
    Save-BackendRecord 'SwarmUI' $proc $webPort $swarm $log
    Wait-SwarmBackend $webPort $proc $log $deadline
    Ok "SwarmUI is up on http://127.0.0.1:$webPort (headless; stop it with: image-stack.ps1 stop)"
    return
  }

  $script:ComfyUIPortCache = $null
  $cp = Get-ComfyUIPort
  if ($cp) { Ok "ComfyUI is already answering on http://127.0.0.1:$cp"; return }
  $comfy = Get-ComfyRoot
  if (-not $comfy) { Fail 'no SwarmUI or ComfyUI install found, so there is nothing to start; see: image-stack.ps1 install' }
  $py = Find-Python
  if (-not $py) { Fail "no python interpreter was found for $comfy" }
  $busy = Get-OpenLocalPorts (Get-ProbePorts)
  $free = 0
  foreach ($candidate in (Get-ProbePorts)) { if ($busy -notcontains $candidate) { $free = [int]$candidate; break } }
  if (-not $free) { Fail 'every port ComfyUI is looked for on (8188, 7820-7832) is taken by something else' }
  $log = New-BackendLog
  Note "starting ComfyUI headless from $comfy on port $free (log: $log)"
  $proc = Start-HiddenProcess ('"{0}" -s main.py --listen 127.0.0.1 --port {1} --disable-auto-launch' -f $py, $free) $comfy $log
  Save-BackendRecord 'ComfyUI' $proc $free $comfy $log
  $deadline = (Get-Date).AddSeconds($Wait)
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
      Remove-Item -LiteralPath $BackendStatePath -Force -ErrorAction SilentlyContinue
      Fail ("ComfyUI exited during startup (code {0}). Log: {1}`n{2}" -f $proc.ExitCode, $log, (Get-LogTail $log))
    }
    $script:ComfyUIPortCache = $null
    if ((Get-ComfyUIPort) -eq $free) {
      Ok "ComfyUI is up on http://127.0.0.1:$free (headless; stop it with: image-stack.ps1 stop)"
      return
    }
    Start-Sleep -Milliseconds 700
  }
  $null = Invoke-Native 'taskkill' @('/PID', $proc.Id, '/T', '/F')
  Remove-Item -LiteralPath $BackendStatePath -Force -ErrorAction SilentlyContinue
  Fail ("ComfyUI did not answer within {0}s; it was stopped again. Log: {1}`n{2}" -f $Wait, $log, (Get-LogTail $log))
}

function Wait-SwarmBackend([int]$P, $Proc, [string]$Log, $Deadline) {
  # The web server answers within seconds; the ComfyUI behind it takes most of a
  # minute, and a render sent before then is refused.
  if (-not $Deadline) { $Deadline = (Get-Date).AddSeconds($Wait) }
  $said = $false
  $badSince = $null
  while ((Get-Date) -lt $Deadline) {
    $state = Get-SwarmBackendState $P
    if ($state -eq 'running') { $script:ComfyUIPortCache = $null; return }
    if ($state -in @('errored', 'disabled', 'empty')) {
      # SwarmUI restarts a backend that failed to come up, so one bad reading is
      # not a verdict; fifteen seconds of it is.
      if (-not $badSince) { $badSince = Get-Date }
      if (((Get-Date) - $badSince).TotalSeconds -ge 15) {
        Fail ("SwarmUI is up on port {0} but its backend is '{1}'. {2}" -f $P, $state, $(if ($Log) { "Log: $Log`n" + (Get-LogTail $Log) } else { 'Open its Server > Backends tab.' }))
      }
    }
    else { $badSince = $null }
    if ($Proc -and $Proc.HasExited) { Fail ("SwarmUI exited while its backend was loading (code {0}). Log: {1}`n{2}" -f $Proc.ExitCode, $Log, (Get-LogTail $Log)) }
    if (-not $said) { Note 'waiting for the ComfyUI backend to load'; $said = $true }
    Start-Sleep -Milliseconds 1000
  }
  Fail ("SwarmUI's backend was still loading after {0}s{1}" -f $Wait, $(if ($Log) { ". Log: $Log" } else { '' }))
}

function Get-BackendJobCount($Rec) {
  # Jobs in flight: SwarmUI's own renders, plus whatever is queued on the ComfyUI
  # behind it (music and 3D go there directly, and SwarmUI does not count them).
  $count = 0
  if ($Rec.kind -eq 'SwarmUI') {
    try {
      $r = Invoke-SwarmApi ([int]$Rec.port) 'GetCurrentStatus' @{ session_id = (New-SwarmSession ([int]$Rec.port)) } 10
      $count += [int]$r.status.waiting_gens + [int]$r.status.live_gens
    }
    catch { }
  }
  $script:ComfyUIPortCache = $null
  $cp = Get-ComfyUIPort
  if ($cp) {
    try {
      $q = Invoke-RestMethod -Uri "http://127.0.0.1:$cp/queue" -TimeoutSec 10 -UseBasicParsing
      $count = [Math]::Max($count, @($q.queue_running).Count + @($q.queue_pending).Count)
    }
    catch { }
  }
  return $count
}

function Stop-Backend {
  $rec = Read-BackendRecord
  if (-not $rec) {
    Ok 'nothing to stop: no backend started by comfy-forge is running (one you started yourself is left alone)'
    return
  }
  if (-not $Force) {
    # Another tool may be rendering on this backend right now (Asset Forge uses a
    # running SwarmUI as it finds it); ending it would fail that job.
    $busy = Get-BackendJobCount $rec
    if ($busy -gt 0) {
      Fail "the backend is working on $busy job(s); wait for them to finish, or stop it anyway with: image-stack.ps1 stop -Force"
    }
  }
  $proc = Get-Process -Id ([int]$rec.pid) -ErrorAction SilentlyContinue
  if ($rec.kind -eq 'SwarmUI' -and (Test-SwarmUp ([int]$rec.port))) {
    # Ask first, so SwarmUI saves its state and ends its own ComfyUI.
    try { $null = Invoke-SwarmApi ([int]$rec.port) 'ShutdownServer' @{ session_id = (New-SwarmSession ([int]$rec.port)) } 10 } catch { }
    if ($proc) { $null = $proc.WaitForExit(20000) }
  }
  $proc = Get-Process -Id ([int]$rec.pid) -ErrorAction SilentlyContinue
  if ($proc) {
    $null = Invoke-Native 'taskkill' @('/PID', $rec.pid, '/T', '/F')
    $null = $proc.WaitForExit(15000)
  }
  Remove-Item -LiteralPath $BackendStatePath -Force -ErrorAction SilentlyContinue
  $script:ComfyUIPortCache = $null
  Ok ("stopped the {0} that comfy-forge started (pid {1})" -f $rec.kind, $rec.pid)
}

function Find-Python {
  # Returns a verified executable path, or $null. A bare name like 'python' is
  # not a path, so Test-Path on it is always false and every caller that used
  # one silently skipped its work; resolve through Get-Command instead.
  $swarm = Get-SwarmRoot
  $candidates = @()
  if ($swarm) {
    $candidates += (Join-Path $swarm 'dlbackend\comfy\python_embeded\python.exe')
    $candidates += (Join-Path $swarm 'dlbackend\comfy\ComfyUI\venv\Scripts\python.exe')
  }
  $comfy = Get-ComfyRoot
  if ($comfy) {
    $candidates += (Join-Path $comfy 'venv\Scripts\python.exe')
    $candidates += (Join-Path (Split-Path -Parent $comfy) 'python_embeded\python.exe')
  }
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }

  # No frontend interpreter. A ComfyUI root that exists without a python is a
  # broken install, not an invitation to use the user's global interpreter.
  if ($comfy) {
    Write-Host "warning    $comfy has no python interpreter; not falling back to the global python" -ForegroundColor Yellow
    return $null
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

function Read-Presets {
  $path = Join-Path $Root 'presets\presets.json'
  if (-not (Test-Path -LiteralPath $path)) { Fail "missing $path" }
  return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
}

# SwarmUI rejects a literal VAE named "Automatic"; that preset value means
# "let the server choose", so it is dropped before the request goes out.
function Convert-PresetParams($paramMap) {
  $body = @{}
  foreach ($prop in $paramMap.PSObject.Properties) {
    if ([string]$prop.Value -eq 'Automatic') { continue }
    $body[$prop.Name] = $prop.Value
  }
  return $body
}

# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------
function Show-Status {
  Say '== image stack status =='
  $swarm = Get-SwarmRoot
  $comfy = Get-ComfyRoot
  if ($swarm) { Ok "SwarmUI  $swarm" } else { Note 'SwarmUI  not found (run install once it exists)' }
  $cp = Get-ComfyUIPort
  if ($comfy) { Ok "ComfyUI  $comfy (standalone)" }
  elseif ($cp) { Note "ComfyUI  none standalone; SwarmUI's backend answers on $cp" }
  else { Note 'ComfyUI  not found' }

  $presets = Read-Presets
  $animaCount = @($presets.PSObject.Properties | Where-Object { $_.Name -like 'Anima*' }).Count
  Ok ("presets  {0} total, {1} Anima" -f @($presets.PSObject.Properties).Count, $animaCount)

  if ($swarm) {
    $modelDir = Join-Path $swarm 'Models\Stable-Diffusion'
    $anima = @(Get-ChildItem -LiteralPath $modelDir -Recurse -Filter '*.safetensors' -ErrorAction SilentlyContinue)
    if ($anima.Count) {
      Ok ("models   {0} checkpoint(s) under Stable-Diffusion" -f $anima.Count)
      foreach ($m in $anima | Select-Object -First 8) { Say ("         {0}  {1:N1} GB" -f $m.Name, ($m.Length / 1GB)) }
    }
    else { Note 'models   no checkpoints yet' }
  }

  $port = if ($Port) { $Port } else { Get-SwarmPort }
  $rec = Read-BackendRecord
  $owner = if ($rec) { ' (started by comfy-forge; `image-stack.ps1 stop` ends it)' } else { '' }
  if (Test-SwarmUp $port) { Ok "server   SwarmUI answering on http://127.0.0.1:$port$owner" }
  elseif ($swarm) { Note 'server   SwarmUI is not running - `image-stack.ps1 start` starts it headless (generate does that itself)' }
  else { Note 'server   SwarmUI is not running - start it with <SwarmUI>\launch-windows.bat' }

  $cp = Get-ComfyUIPort
  if ($cp) { Ok "comfyui  ComfyUI API on port $cp (MCP, music and 3D target)" } else { Note 'comfyui  no ComfyUI API port answered' }

  Show-Requirements
  Show-Music
  Show-3D
}

# Music (ACE-Step 1.5) and 3D (Pixal3D, TRELLIS.2, Idol): read-only. Which variants
# have every file on disk. The file lists come from TOOLS\music.py and
# TOOLS\model3d.py, so there is one table per lane, not two.
function Get-VariantReadiness([string]$Script) {
  # @{ Ready = names; Missing = @{ name = files } }, or $null when it cannot be told.
  $py = Find-Python
  if (-not $py) { return $null }
  $roots = @()
  $swarm = Get-SwarmRoot
  if ($swarm) { $roots += (Join-Path $swarm 'dlbackend\comfy\ComfyUI') }
  $comfy = Get-ComfyRoot
  if ($comfy) { $roots += $comfy }
  $roots = @($roots | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'models') })
  if (-not $roots.Count) { return $null }
  try { $table = (& $py (Join-Path $PSScriptRoot $Script) variants --json) -join "`n" | ConvertFrom-Json }
  catch { return $null }
  if (-not $table) { return $null }
  $ready = @(); $missing = [ordered]@{}
  foreach ($variant in $table.PSObject.Properties) {
    $absent = @()
    foreach ($f in @($variant.Value.files)) {
      $found = $false
      foreach ($r in $roots) { if (Test-Path -LiteralPath (Join-Path $r (Join-Path 'models' (Join-Path $f[0] $f[1])))) { $found = $true; break } }
      if (-not $found) { $absent += ("{0}/{1}" -f $f[0], $f[1]) }
    }
    if ($absent.Count) { $missing[$variant.Name] = $absent } else { $ready += $variant.Name }
  }
  return @{ Ready = $ready; Missing = $missing }
}

function Show-Music {
  $state = Get-VariantReadiness 'music.py'
  if (-not $state) { return }
  if ($state.Ready.Count) { Ok ("music    ACE-Step 1.5 ready: {0}  (TOOLS\music.ps1)" -f ($state.Ready -join ', ')) }
  else { Note 'music    ACE-Step 1.5 weights not installed (TOOLS\Install-MusicModels.ps1 -WhatIf shows the list)' }
}

function Show-3D {
  $state = Get-VariantReadiness 'model3d.py'
  if (-not $state) { return }
  if ($state.Ready.Count) { Ok ("3d       image to 3D ready: {0}  (TOOLS\model3d.ps1)" -f ($state.Ready -join ', ')) }
  else { Note '3d       image-to-3D weights not installed (TOOLS\Install-3DModels.ps1 -WhatIf shows the list)' }
}

function Resolve-AssetForgeRoot {
  # The same two places TOOLS\game-ready.ps1 looks; $null instead of an error.
  $candidates = @($env:ASSETFORGE_ROOT, (Join-Path (Split-Path -Parent $Root) 'Asset-Forge'))
  foreach ($c in $candidates) {
    if (-not $c) { continue }
    try {
      if (Test-Path -LiteralPath ([IO.Path]::Combine($c, 'src', 'assetforge', 'data', 'recipes', 'mesh-gameready.json'))) {
        return (Resolve-Path -LiteralPath $c).Path
      }
    }
    catch { }
  }
  return $null
}

# Everything `status` says, as one JSON object an agent can read: each lane, whether
# it is ready, the command that runs it and the command that fixes what is missing.
function Show-Capabilities {
  $swarm = Get-SwarmRoot
  $comfy = Get-ComfyRoot
  $port = if ($Port) { $Port } else { Get-SwarmPort }
  $swarmUp = [bool]($swarm -and (Test-SwarmUp $port))
  $cp = Get-ComfyUIPort
  $rec = Read-BackendRecord
  $py = Find-Python
  $ps = 'powershell -NoProfile -ExecutionPolicy Bypass -File'
  $tool = { param($name) '{0} "{1}"' -f $ps, (Join-Path $PSScriptRoot $name) }
  $cli = & $tool 'image-stack.ps1'
  $pyTool = { param($name) 'python "{0}"' -f (Join-Path $PSScriptRoot $name) }

  $checkpoints = @()
  if ($swarm) {
    $modelDir = Join-Path $swarm 'Models\Stable-Diffusion'
    $checkpoints = @(Get-ChildItem -LiteralPath $modelDir -Recurse -Filter '*.safetensors' -ErrorAction SilentlyContinue |
      ForEach-Object { $_.FullName.Substring($modelDir.Length + 1) -replace '\\', '/' })
  }
  $presets = Read-Presets
  $music = Get-VariantReadiness 'music.py'
  $model3d = Get-VariantReadiness 'model3d.py'
  # Typed, so one ready variant is still a list in the JSON and not a bare string.
  [string[]]$musicReady = @(); if ($music) { $musicReady = @($music.Ready) }
  [string[]]$model3dReady = @(); if ($model3d) { $model3dReady = @($model3d.Ready) }
  [string[]]$checkpoints = @($checkpoints)
  $forge = Resolve-AssetForgeRoot
  $node = (Test-CommandExists 'node') -and (Test-CommandExists 'npx')

  $report = [ordered]@{
    tool    = 'comfy-forge'
    version = (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim()
    root    = $Root
    backend = [ordered]@{
      swarmui                = [ordered]@{ root = $swarm; running = $swarmUp; url = $(if ($swarmUp) { "http://127.0.0.1:$port" } else { $null }) }
      comfyui                = [ordered]@{ root = $comfy; running = [bool]$cp; url = $(if ($cp) { "http://127.0.0.1:$cp" } else { $null }) }
      started_by_comfy_forge = [bool]$rec
      log                    = $(if ($rec) { $rec.log } else { $null })
      start                  = "$cli start"
      stop                   = "$cli stop"
      note                   = 'generate, model3d and music start the backend themselves when none answers; stop ends only a backend comfy-forge started'
    }
    lanes   = [ordered]@{
      image   = [ordered]@{
        ready       = [bool]($swarm -and $checkpoints.Count)
        command     = "$cli generate -Prompt ""<text>"" -Out <folder> -Json"
        does        = @('text to image', 'image to image (-InitImage, -Creativity)', 'inpaint (-Mask)', 'LoRA (-Lora name:weight)',
          'transparent background (-Transparent)', 'tileable texture (-Tileable, UNet models)', 'upscale pass (-Upscale)',
          'any other SwarmUI parameter (-Set key=value)')
        presets     = @($presets.PSObject.Properties).Count
        list_presets = "$cli presets"
        checkpoints = $checkpoints
        fix         = $(if (-not $swarm) { 'install SwarmUI and run its launch-windows.bat once, then: ' + "$cli install" } elseif (-not $checkpoints.Count) { "$cli install" } else { $null })
      }
      model3d = [ordered]@{
        ready      = [bool]($model3d -and $model3d.Ready.Count)
        command    = (& $pyTool 'model3d.py') + ' generate --image <file> --name <name> --out <folder> --game-ready'
        from_text  = (& $pyTool 'model3d.py') + ' generate --prompt "<object>" --name <name> --out <folder> --game-ready'
        does       = @('one image to a textured PBR .glb (pixal3d)', 'turnaround sheet or 1-4 views to a .glb (pixal3d-multi)',
          'text to image to .glb (--prompt)', 'game-ready mesh at or under 20,000 triangles (--game-ready, Asset Forge)',
          'Gaussian splat preview (idol)')
        variants_ready   = $model3dReady
        variants_missing = $(if ($model3d) { $model3d.Missing } else { $null })
        game_ready = [ordered]@{ available = [bool]$forge; asset_forge = $forge; fix = $(if ($forge) { $null } else { 'install Asset Forge beside this folder, or set ASSETFORGE_ROOT' }) }
        fix        = $(if ($model3d -and $model3d.Ready.Count) { $null } else { (& $tool 'Install-3DModels.ps1') + ' -WhatIf   (lists the downloads; then -Set pixal3d)' })
      }
      music   = [ordered]@{
        ready            = [bool]($music -and $music.Ready.Count)
        command          = (& $pyTool 'music.py') + ' generate --tags "<style>" --instrumental --duration 60 --out <folder>'
        does             = @('song with lyrics', 'instrumental', 'replay or vary a take (--from, --reseed)')
        variants_ready   = $musicReady
        variants_missing = $(if ($music) { $music.Missing } else { $null })
        fix              = $(if ($music -and $music.Ready.Count) { $null } else { (& $tool 'Install-MusicModels.ps1') + ' -WhatIf   (lists the downloads; then -Set aio)' })
      }
      dataset = [ordered]@{
        ready   = $true
        command = (& $pyTool 'anima_dataset.py') + ' inspect --images <folder> --json'
        does    = @('tagged image folder to a verified LoRA training archive (no training, no upload)')
      }
      merge   = [ordered]@{
        ready   = [bool](Test-PythonDeps $py)
        command = "$cli merge -Donors ""a.safetensors"",""b.safetensors"" -Weights 0.6,0.4"
        does    = @('task-arithmetic merge of up to four checkpoints into one loadable file')
        fix     = $(if (Test-PythonDeps $py) { $null } else { "$cli install" })
      }
      mcp     = [ordered]@{
        ready   = [bool]$node
        command = "$cli mcp -Write -Agent claude"
        does    = @('ComfyUI MCP server config pointed at the backend port that is answering')
        fix     = $(if ($node) { $null } else { 'install Node.js 18 or newer' })
      }
    }
    not_covered = @('training', 'uploads', 'sound effects and voice (Asset Forge: audio-sfx, audio-voice)', 'rigging and animation',
      'video generation')
  }
  $json = $report | ConvertTo-Json -Depth 8
  Write-Output ([regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value }))
}

# ---------------------------------------------------------------------------
# Requirements - everything the kit needs beyond the model files. `status`
# reports them, `install` fixes every one it can without changing an existing
# ComfyUI install's own packages.
# ---------------------------------------------------------------------------
function Get-ComfyModelDir {
  # Model category matters: ComfyUI's checkpoint loaders read `models/checkpoints`
  # and diffusion-only loaders read `models/diffusion_models`. Putting a full
  # checkpoint in diffusion_models leaves CheckpointLoaderSimple unable to see it.
  param([ValidateSet('checkpoint', 'diffusion')] [string]$Kind = 'diffusion')
  $leaf = if ($Kind -eq 'checkpoint') { 'checkpoints' } else { 'diffusion_models' }
  $roots = @()
  $swarm = Get-SwarmRoot
  if ($swarm) { $roots += (Join-Path $swarm 'dlbackend\comfy\ComfyUI') }
  $comfy = Get-ComfyRoot
  if ($comfy) { $roots += $comfy }
  foreach ($r in $roots) {
    $p = Join-Path $r (Join-Path 'models' $leaf)
    if (Test-Path -LiteralPath $p) { return $p }
    # Create the registered category folder when the tree exists but the folder
    # does not yet: ComfyUI reads whatever is under models/.
    $models = Join-Path $r 'models'
    if (Test-Path -LiteralPath $models) {
      try { New-Item -ItemType Directory -Path $p -Force | Out-Null; return $p } catch { }
    }
  }
  return $null
}

function Get-ComfyNodeDir {
  $swarm = Get-SwarmRoot
  if ($swarm) {
    # SwarmUI's Extensions folder is NOT ComfyUI's custom_nodes; a ComfyUI node
    # pack installed there registers nothing.
    foreach ($p in @((Join-Path $swarm 'dlbackend\comfy\ComfyUI\custom_nodes'))) {
      if (Test-Path -LiteralPath $p) { return $p }
    }
  }
  # Standalone ComfyUI: custom_nodes sits directly under its root.
  $comfy = Get-ComfyRoot
  if ($comfy) {
    $nodes = Join-Path $comfy 'custom_nodes'
    if (Test-Path -LiteralPath $nodes) { return $nodes }
  }
  return $null
}

# Custom node packs this kit relies on. RES4LYF is what puts beta57 / res_2s /
# rk_beta into ComfyUI's scheduler list, which SwarmUI then merges into its own
# Scheduler dropdown on startup (ComfyUIBackendExtension.AssignValuesFromRaw).
function Get-NodePacks {
  # The lock is the only list. A second hardcoded copy drifts silently, and a
  # missing lock must not degrade into "install whatever main has today".
  $lock = Get-DependencyLock
  if (-not $lock) { return @() }
  return @($lock.node_packs | ForEach-Object {
    @{ Name = $_.name; Url = $_.url; Sha = $_.sha
       Why = (@($_.provides) -join ', ') }
  })
}

function Get-PackDependencyReport([string]$Python, [string]$NodeDir) {
  # One resolver reads requirements.txt AND pyproject.toml (HaDeS ships only the
  # latter, which the old requirements.txt-only parser never saw). Native stderr
  # is captured without EAP=Stop turning an expected pip message into a crash.
  $script = Join-Path $Root 'TOOLS\check_deps.py'
  $packs = @(@(Get-NodePacks) | ForEach-Object { Join-Path $NodeDir $_.Name } |
             Where-Object { Test-Path -LiteralPath $_ })
  if ($packs.Count -eq 0) { return @() }
  # --report-only: the installer installs what is missing, so a nonzero
  # "requirements unmet" exit would be misread as a resolver failure.
  $argv = @($script, '--report-only')
  foreach ($p in $packs) { $argv += @('--pack', $p) }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $raw = & $Python @argv 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($code -ne 0) {
    Write-Host ("error      dependency resolution failed for {0}" -f $NodeDir) -ForegroundColor Red
    return $null
  }
  return ($raw | ConvertFrom-Json)
}

function Install-NodePackDeps([string]$Python) {
  # Installs only what the resolver reports missing, as argv (never a shell
  # string), into the frontend's own interpreter. torch is never replaced.
  $nodeDir = Get-ComfyNodeDir
  if (-not $nodeDir) { return 0 }
  $report = Get-PackDependencyReport $Python $nodeDir
  if ($null -eq $report) { return 1 }

  $failures = 0
  foreach ($pack in $report) {
    if (-not $pack.present) { continue }
    if ($pack.status -eq 'failed') {
      # A resolver that cannot check is not a resolver that passed.
      Write-Host ("error      {0}: {1}" -f $pack.pack, $pack.error) -ForegroundColor Red
      $failures++
      continue
    }
    foreach ($u in @($pack.unsupported)) {
      # Reported requirements that pip cannot replay are unresolved by
      # definition; saying "installed" here would be a false claim.
      Write-Host ("blocked    {0}: '{1}' cannot be installed automatically - install it yourself" -f $pack.pack, $u) -ForegroundColor Red
      $failures++
    }
    $missing = @($pack.missing)
    if ($missing.Count -eq 0) {
      if ($pack.requirements.Count -gt 0) { Ok ("deps verified -> {0}" -f $pack.pack) }
      continue
    }

    $safe = @()
    foreach ($r in $missing) {
      $name = (($r -split '[<>=!~;\[ ]')[0]).Trim().ToLower()
      if ($name -in @('torch', 'torchvision', 'torchaudio', 'xformers')) {
        # A node pack that wants a different torch build is a compatibility
        # question, not something the installer may decide alone.
        Write-Host ("blocked    {0} requires {1}; the installed CUDA torch is kept. Upgrade it yourself if the node needs it." -f $pack.pack, $r) -ForegroundColor Red
        $failures++
        continue
      }
      $safe += $r
    }
    if ($safe.Count -eq 0) { continue }

    Note ("installing for {0}: {1}" -f $pack.pack, ($safe -join ', '))
    & $Python @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check') @safe
    if ($LASTEXITCODE -ne 0) {
      Write-Host ("error      pip install failed for {0}" -f $pack.pack) -ForegroundColor Red
      $failures++
      continue
    }
    $after = Get-PackDependencyReport $Python $nodeDir
    if ($null -eq $after) {
      Write-Host ("error      {0}: post-install verification could not run" -f $pack.pack) -ForegroundColor Red
      $failures++
      continue
    }
    $entry = @($after | Where-Object { $_.pack -eq $pack.pack })[0]
    if ($entry.status -eq 'failed') {
      Write-Host ("error      {0}: {1}" -f $pack.pack, $entry.error) -ForegroundColor Red
      $failures++
      continue
    }
    $still = @($entry.missing)
    if ($still.Count -gt 0) {
      Write-Host ("error      still unsatisfied after install: {0}" -f ($still -join ', ')) -ForegroundColor Red
      $failures++
    } else {
      Ok ("deps installed -> {0}" -f $pack.pack)
    }
  }
  return $failures
}

# ---------------------------------------------------------------------------
# Node packs - pinned in config\dependencies.lock.json, judged by state.
# ---------------------------------------------------------------------------
function Get-DependencyLock {
  # The tested revision of each external pack. Absent file means "unpinned", and
  # callers must say so rather than pretending the install is reproducible.
  $path = Join-Path $Root 'config\dependencies.lock.json'
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  try { return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json) }
  catch { Write-Host 'warning    config\dependencies.lock.json is not valid JSON' -ForegroundColor Yellow; return $null }
}

function Get-NodePackDrift {
  # Read-only. Never resets, cleans or checks out anything.
  $lock = Get-DependencyLock
  $dir = Get-ComfyNodeDir
  if (-not $lock -or -not $dir) { return @() }
  return @($lock.node_packs | ForEach-Object { Get-NodePackState $dir $_ })
}

function Test-NodePack([string]$Name) {
  # A directory is not an installation. Ask the one state evaluator: only a
  # checkout at the pinned commit, in the pinned repository, counts. `dirty`
  # still counts - it is the user's own working copy, not a broken install.
  $lock = Get-DependencyLock
  $dir = Get-ComfyNodeDir
  if (-not $lock -or -not $dir) { return $false }
  $pin = @($lock.node_packs | Where-Object { $_.name -eq $Name })[0]
  if (-not $pin) { return $false }
  $state = Get-NodePackState $dir $pin
  return ($state.State -eq 'locked' -or $state.State -eq 'dirty')
}

function Test-PythonDeps([string]$Python) {
  if (-not $Python) { return $false }
  return ((Invoke-Native $Python @('-c', 'import torch, safetensors, numpy')).Code -eq 0)
}

function Test-CommandExists([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Show-Requirements {
  $py = Find-Python
  $rows = @()

  # Find-Python returns $null when no interpreter exists, and Test-Path on
  # $null throws, so the check has to name the condition explicitly.
  foreach ($p in @(Get-NodePackDrift)) {
    $short = if ($p.Sha) { $p.Sha.Substring(0, 7) } else { '?' }
    $line = 'packs    {0} {1}' -f $p.Name, $p.State
    if ($p.State -eq 'locked') { Ok $line }
    elseif ($p.State -eq 'drift') { Note ($line + " ($short, lock pins " + $p.Pin.Substring(0, 7) + ')') }
    elseif ($p.Detail) { Note ($line + ' (' + $p.Detail + ')') }
    else { Note $line }
  }
  $rows += @{ Name = 'python'; Ok = [bool]($py -and (Test-Path -LiteralPath $py));
              Detail = $(if ($py) { $py } else { 'not found' });
              Fix = 'install ComfyUI or put python on PATH' }
  $rows += @{ Name = 'merge deps'; Ok = (Test-PythonDeps $py); Detail = 'torch, safetensors, numpy';
              Fix = 'image-stack.ps1 install' }
  $rows += @{ Name = 'node/npx'; Ok = ((Test-CommandExists 'node') -and (Test-CommandExists 'npx')); Detail = 'for the ComfyUI MCP server';
              Fix = 'install Node.js 18+' }
  foreach ($pack in @(Get-NodePacks)) {
    $rows += @{ Name = 'nodes'; Ok = (Test-NodePack $pack.Name); Detail = $pack.Why;
                Fix = 'image-stack.ps1 install' }
  }
  $rows += @{ Name = 'git'; Ok = (Test-CommandExists 'git'); Detail = 'for node-pack installs';
              Fix = 'install Git' }

  foreach ($r in $rows) {
    if ($r.Ok) { Ok ("{0,-11} {1}" -f $r.Name, $r.Detail) }
    else { Write-Host ("missing    {0,-11} {1}  -> {2}" -f $r.Name, $r.Detail, $r.Fix) -ForegroundColor Yellow }
  }
}

function Install-MissingRequirements {
  $fixed = 0

  # Python merge dependencies. torch is never replaced: ComfyUI ships a CUDA
  # build that must match its own setup, so only the small modules are added.
  $py = Find-Python
  if ($py -and (Test-Path -LiteralPath $py)) {
    $missing = @()
    foreach ($mod in @('torch', 'safetensors', 'numpy')) {
      if ((Invoke-Native $py @('-c', "import $mod")).Code -ne 0) { $missing += $mod }
    }
    if ($missing -contains 'torch') {
      Write-Host 'note       torch is missing from the ComfyUI python; install ComfyUI/its requirements first' -ForegroundColor Yellow
    }
    $pip = @($missing | Where-Object { $_ -ne 'torch' })
    if ($pip.Count) {
      Note ("installing python modules: " + ($pip -join ', '))
      & $py -m pip install --quiet --disable-pip-version-check @pip
      if ($LASTEXITCODE -eq 0) { Ok 'python modules installed'; $fixed++ }
      else { Write-Host 'warning    pip install failed; merges will not run' -ForegroundColor Yellow }
    }
  }
  else { Write-Host 'warning    no python found; merges are unavailable' -ForegroundColor Yellow }

  # Node packs. The CLI merge does not need them, but the ComfyUI workflow does,
  # and RES4LYF is what makes beta57 selectable in SwarmUI.
  $dir = Get-ComfyNodeDir
  $cloneFailures = 0
  if (-not (Get-DependencyLock)) {
    # Without pins this install is not reproducible; refuse instead of cloning
    # whatever upstream main happens to be today.
    Write-Host 'error      config\dependencies.lock.json is missing or invalid; refusing to install unpinned node packs' -ForegroundColor Red
    $cloneFailures++
  }
  foreach ($pack in @($(if (Get-DependencyLock) { Get-NodePacks } else { @() }))) {
    if (Test-NodePack $pack.Name) { continue }
    if ($dir -and (Test-CommandExists 'git')) {
      $dest = Join-Path $dir $pack.Name
      Note ("installing node pack {0} ({1})" -f $pack.Name, $pack.Why)
      # A folder with no .git is a failed clone, not a working copy: move it
      # aside so the clone can proceed. A real checkout is never touched.
      $occupied = Clear-OccupiedPackDir $dest
      if ($occupied -eq 'checkout') {
        Write-Host ("error      {0}: a checkout exists but is not at the pinned commit; inspect or remove it yourself" -f $pack.Name) -ForegroundColor Red
        $cloneFailures++
        continue
      }
      if ($occupied -like 'aside:*') {
        Note ("moved an incomplete {0} to {1}" -f $pack.Name, (Split-Path -Leaf $occupied.Substring(6)))
      }
      # Clone then check out the locked commit: tracking upstream main means two
      # users installing the same release get different node behaviour.
      & git clone --quiet $pack.Url $dest
      if ($LASTEXITCODE -ne 0) {
        # A failed clone is a failed install, not a warning that vanishes from
        # the exit code.
        Write-Host ("error      {0}: clone failed" -f $pack.Name) -ForegroundColor Red
        $cloneFailures++
        continue
      }
      $lock = Get-DependencyLock
      $pin = $null
      if ($lock) { $pin = @($lock.node_packs | Where-Object { $_.name -eq $pack.Name })[0] }
      if ($pin -and $pin.sha) {
        $null = Invoke-Native 'git' @('-C', $dest, 'checkout', '--quiet', $pin.sha)
        $rev = Invoke-Native 'git' @('-C', $dest, 'rev-parse', 'HEAD')
        $head = $rev.Text
        if ($rev.Code -ne 0 -or $head -ne $pin.sha) {
          # A failed checkout is a failed install; reporting success here is
          # how an unreachable pin looked fine for a whole release.
          Write-Host ("error      {0}: could not check out {1} (HEAD is {2})" -f $pack.Name, $pin.sha, $head) -ForegroundColor Red
          $cloneFailures++
          continue
        }
        # Verify by state, not by the commands having returned 0: a stray
        # .git or a foreign origin must not read as installed.
        $state = Get-NodePackState $dir $pin
        if ($state.State -eq 'locked' -or $state.State -eq 'dirty') {
          Ok ("node pack -> {0} @ {1}" -f $dest, $pin.sha.Substring(0, 7))
        } else {
          Write-Host ("error      {0}: checkout verification says '{1}'" -f $pack.Name, $state.State) -ForegroundColor Red
          $cloneFailures++
          continue
        }
      } else {
        Ok "node pack -> $dest (unpinned: no lock entry)"
      }
      $fixed++
    } else {
      Write-Host 'warning    cannot install node packs: needs a ComfyUI custom_nodes folder and git' -ForegroundColor Yellow
      break
    }
  }
  $pyForDeps = Find-Python
  $depFailures = $cloneFailures
  if ($pyForDeps) { $depFailures += Install-NodePackDeps $pyForDeps }
  $packsAdded = @(@(Get-NodePacks) | Where-Object { -not (Test-NodePack $_.Name) }).Count -eq 0
  if ($packsAdded -and $fixed -gt 0) {
    Note 'restart SwarmUI so it re-reads the sampler/scheduler list from ComfyUI'
  }

  if (-not (Test-CommandExists 'node')) {
    Write-Host 'warning    Node.js is missing; the ComfyUI MCP server needs it (https://nodejs.org/)' -ForegroundColor Yellow
  }

  if ($fixed -eq 0) { Ok 'nothing to install' } else { Ok "$fixed component(s) installed" }
  if ($depFailures -gt 0) {
    # A node pack that cannot import is not an installed node pack.
    Write-Host ("error      {0} installation failure(s): node pack checkout or dependency set" -f $depFailures) -ForegroundColor Red
    exit 1
  }
}

function Invoke-Install {
  # With no SwarmUI there is nothing to install into. Saying "done" and exiting 0
  # hides that, so refuse and name the launcher the README asks for.
  if (-not (Get-SwarmRoot)) {
    Fail 'no SwarmUI install found - run launch-windows.bat in your SwarmUI folder first'
  }
  $script = Join-Path $PSScriptRoot 'Ensure-AnimaSwarmUI.ps1'
  if (-not (Test-Path -LiteralPath $script)) { Fail "missing $script" }
  $cliArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script)
  if ($IncludeOptional) { $cliArgs += '-IncludeOptional' }
  & powershell @cliArgs
  if ($LASTEXITCODE -ne 0) { Fail "installer exited $LASTEXITCODE" }
  Write-Host ''
  Say '== remaining requirements =='
  Install-MissingRequirements
}

function Show-Presets {
  $presets = Read-Presets
  if ($Name) {
    $entry = $presets.PSObject.Properties | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if (-not $entry) { Fail "no preset named '$Name' (run: image-stack.ps1 presets)" }
    Say $entry.Name
    Say ("  " + $entry.Value.description)
    foreach ($p in $entry.Value.param_map.PSObject.Properties) { Say ("  {0,-18} {1}" -f $p.Name, $p.Value) }
    return
  }
  Say ("{0} presets:" -f @($presets.PSObject.Properties).Count)
  foreach ($p in $presets.PSObject.Properties) { Say ("  " + $p.Name) }
}

# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
function Get-ModelKey([string]$Name) {
  return (($Name -replace '\\', '/') -replace '\.(safetensors|sft|ckpt|gguf)$', '').ToLowerInvariant()
}

function Get-SwarmModels([int]$P, [string]$Session, [string]$Subtype) {
  $r = Invoke-SwarmApi $P 'ListModels' @{ session_id = $Session; path = ''; depth = 10; subtype = $Subtype; sortBy = 'Name'; allowRemote = $true }
  # Shallow before deep, then by name: a model at the top of its folder is the
  # stable pick, and the order is the same on every run.
  return @(@($r.files) | Sort-Object @{ Expression = { ($_.name -split '/').Count } }, @{ Expression = { $_.name } })
}

function Resolve-SwarmModelName($Files, [string]$Wanted, [string]$What) {
  # The exact name, or a bare file name that matches one entry. Two matches are
  # an error, never a guess.
  $want = Get-ModelKey $Wanted
  $exact = @($Files | Where-Object { (Get-ModelKey $_.name) -eq $want })
  if ($exact.Count -eq 1) { return [string]$exact[0].name }
  $leaf = ($want -split '/')[-1]
  $hits = @($Files | Where-Object { ((Get-ModelKey $_.name) -split '/')[-1] -eq $leaf })
  if ($hits.Count -eq 1) { return [string]$hits[0].name }
  if ($hits.Count -gt 1) {
    Fail ("$What '$Wanted' matches $($hits.Count) entries; pass the full name: " + (($hits | ForEach-Object { $_.name }) -join '; '))
  }
  $have = if (@($Files).Count) { (@($Files | ForEach-Object { $_.name }) -join '; ') } else { 'nothing' }
  Fail "$What '$Wanted' is not in SwarmUI's list. It has: $have"
}

# Preset name prefix -> text found in the name, architecture or compatibility
# class of a model of that family (compared with punctuation removed).
$script:PresetFamilies = [ordered]@{
  'Anima'   = @('anima')
  'FLUX2'   = @('flux2')
  'FLUX1'   = @('flux1')
  'Z-Image' = @('zimage')
  'Krea'    = @('krea')
  'SDXL'    = @('sdxl', 'stablediffusionxl', 'illustrious')
}

function Select-PresetModel($Files, [string]$PresetName) {
  # No -Model and the preset names none: take a model of the preset's family.
  # A render with a model of another family is noise, so no match is a refusal.
  if (@($Files).Count -eq 0) { Fail 'SwarmUI lists no checkpoints; run: image-stack.ps1 install' }
  $keys = @()
  foreach ($family in $script:PresetFamilies.Keys) {
    if ($PresetName -like "$family*") { $keys = $script:PresetFamilies[$family]; break }
  }
  $hits = @()
  if ($keys.Count) {
    $hits = @($Files | Where-Object {
        $text = ("{0} {1} {2}" -f $_.name, $_.architecture, $_.compat_class).ToLowerInvariant() -replace '[^a-z0-9]', ''
        $found = $false
        foreach ($k in $keys) { if ($text.Contains($k)) { $found = $true } }
        $found
      })
  }
  elseif (@($Files).Count -eq 1) { $hits = @($Files) }
  if (-not $hits.Count) {
    Fail ("no installed model fits the preset '$PresetName'; pass -Model. SwarmUI has: " + (@($Files | ForEach-Object { $_.name }) -join '; '))
  }
  if ($hits.Count -gt 1) {
    Note ("model   {0} (first of {1} that fit this preset; choose with -Model)" -f $hits[0].name, $hits.Count)
  }
  return [string]$hits[0].name
}

function ConvertTo-DataUrl([string]$Path, [string]$What) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$What not found: $Path" }
  $mime = switch ([IO.Path]::GetExtension($Path).ToLowerInvariant()) {
    '.png' { 'image/png' } '.jpg' { 'image/jpeg' } '.jpeg' { 'image/jpeg' } '.webp' { 'image/webp' }
    default { $null }
  }
  if (-not $mime) { Fail "$What must be a .png, .jpg or .webp file: $Path" }
  return ("data:$mime;base64," + [Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path).Path)))
}

function Get-ImageSize([string]$Path) {
  # Width and height, or $null when the format is not one GDI+ reads (WebP).
  try {
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile((Resolve-Path -LiteralPath $Path).Path)
    try { return @($img.Width, $img.Height) } finally { $img.Dispose() }
  }
  catch { return $null }
}

function Split-List([string[]]$Values) {
  # `-Lora a,b` typed in a session is an array; through `powershell -File` the same
  # text is one string.
  return @($Values | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function ConvertTo-AsciiJson($Object) {
  # One line any console code page can carry: non-ASCII becomes \uXXXX.
  $json = $Object | ConvertTo-Json -Depth 8 -Compress
  return [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
}

function Invoke-Generate {
  $swarm = Get-SwarmRoot
  if (-not $swarm) { Fail 'no SwarmUI install found; see: image-stack.ps1 install' }

  # Everything that can be refused without a backend is refused first.
  $presets = Read-Presets
  $entry = $presets.PSObject.Properties | Where-Object { $_.Name -eq $Preset } | Select-Object -First 1
  if (-not $entry) { Fail "no preset named '$Preset' (run: image-stack.ps1 presets)" }
  if ($Count -lt 1) { Fail '-Count must be 1 or more' }
  if ($Creativity -ne -1 -and ($Creativity -lt 0 -or $Creativity -gt 1)) { Fail '-Creativity is a number from 0 (keep the image) to 1 (ignore it)' }
  if ($Mask -and -not $InitImage) { Fail '-Mask needs -InitImage: the mask says which part of that image to repaint' }
  if ($Upscale -ne 0 -and ($Upscale -lt 1 -or $Upscale -gt 4)) { Fail '-Upscale is a factor from 1 to 4, for example 1.5' }
  $extra = [ordered]@{}
  # A comma separates two pairs only where another key= follows it, so a value
  # may hold commas of its own.
  foreach ($pair in @($Set | ForEach-Object { "$_" -split ',(?=\s*[A-Za-z_]\w*=)' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
    $at = $pair.IndexOf('=')
    if ($at -lt 1) { Fail "-Set takes key=value pairs (a SwarmUI parameter id and its value); got '$pair'" }
    $extra[$pair.Substring(0, $at).Trim().ToLowerInvariant()] = $pair.Substring($at + 1)
  }
  $loraNames = @(); $loraWeights = @()
  foreach ($item in (Split-List $Lora)) {
    $name = $item; $weight = '1'
    if ($item -match '^(.*):(-?\d+(?:\.\d+)?)$') { $name = $matches[1]; $weight = $matches[2] }
    $loraNames += $name; $loraWeights += $weight
  }
  $initData = $null; $maskData = $null
  if ($InitImage) { $initData = ConvertTo-DataUrl $InitImage '-InitImage' }
  if ($Mask) { $maskData = ConvertTo-DataUrl $Mask '-Mask' }

  $port = if ($Port) { $Port } else { Get-SwarmPort }
  if (-not (Test-SwarmUp $port)) {
    # -Port names a server the caller runs (Asset Forge starts one per job on a
    # free port); starting a second one on the default port would not help it.
    # ($BoundParams, not $Port: variable names ignore case, and $port just above is
    # the same variable once it has been assigned in this function.)
    if ($NoStart -or $BoundParams.ContainsKey('Port') -or $env:COMFY_FORGE_NO_AUTOSTART) {
      Fail "SwarmUI is not running on port $port - run ``image-stack.ps1 start`` (headless), or $(Join-Path $swarm 'launch-windows.bat'), and retry"
    }
    Start-Backend
    $port = Get-SwarmPort   # the record `start` just wrote: SwarmUI may have taken the next free port
  }
  elseif ((Get-SwarmBackendState $port) -eq 'loading') { Wait-SwarmBackend $port $null '' }

  $session = New-SwarmSession $port
  $body = Convert-PresetParams $entry.Value.param_map
  $models = Get-SwarmModels $port $session 'Stable-Diffusion'
  if ($Model) { $useModel = Resolve-SwarmModelName $models $Model 'model' }
  elseif ($body['model']) { $useModel = Resolve-SwarmModelName $models ([string]$body['model']) "the model of preset '$Preset'" }
  elseif ($env:COMFY_FORGE_MODEL) { $useModel = Resolve-SwarmModelName $models $env:COMFY_FORGE_MODEL 'COMFY_FORGE_MODEL' }
  else { $useModel = Select-PresetModel $models $Preset }

  if ($loraNames.Count) {
    $loras = Get-SwarmModels $port $session 'LoRA'
    $resolved = @($loraNames | ForEach-Object { (Resolve-SwarmModelName $loras $_ 'LoRA') -replace '\.(safetensors|sft|ckpt)$', '' })
    $body['loras'] = ($resolved -join ',')
    $body['loraweights'] = ($loraWeights -join ',')
  }

  # An edit keeps the size of the image it edits unless a size was asked for.
  if ($InitImage -and -not $BoundParams.ContainsKey('Width') -and -not $BoundParams.ContainsKey('Height')) {
    $size = Get-ImageSize $InitImage
    if ($size) {
      $script:Width = [Math]::Max(64, [int]([Math]::Floor($size[0] / 16) * 16))
      $script:Height = [Math]::Max(64, [int]([Math]::Floor($size[1] / 16) * 16))
    }
  }
  $useSeed = if ($Seed -lt 0) { Get-Random -Minimum 0 -Maximum 2147483647 } else { $Seed }

  $body['session_id'] = $session
  # A preset may carry a prompt of its own (a pose sheet, for instance); it is kept
  # unless a prompt was given.
  if ($BoundParams.ContainsKey('Prompt') -or -not $body['prompt']) { $body['prompt'] = $Prompt }
  $body['images'] = $Count
  $body['model'] = $useModel
  $body['width'] = $Width
  $body['height'] = $Height
  $body['seed'] = $useSeed
  if ($Steps -gt 0) { $body['steps'] = $Steps }
  if ($Cfg -gt 0) { $body['cfgscale'] = $Cfg }
  if ($Negative) { $body['negativeprompt'] = $Negative }
  if ($initData) {
    $body['initimage'] = $initData
    if ($Creativity -ne -1) { $body['initimagecreativity'] = $Creativity }
  }
  if ($maskData) { $body['maskimage'] = $maskData }
  if ($Transparent) { $body['removebackground'] = $true }
  if ($Tileable) { $body['seamlesstileable'] = 'true' }
  if ($Upscale -gt 1) { $body['refinerupscale'] = $Upscale; if (-not $body['refinercontrolpercentage']) { $body['refinercontrolpercentage'] = 0.4 } }
  foreach ($k in $extra.Keys) { $body[$k] = $extra[$k] }

  Note ("preset {0} -> {1} at {2}x{3}, {4} image(s), seed {5}" -f $Preset, $useModel, $body['width'], $body['height'], $Count, $useSeed)
  $r = Invoke-SwarmApi $port 'GenerateText2Image' $body 1800
  if ($r.error) { Fail ("SwarmUI refused: " + $r.error) }
  if (-not @($r.images).Count) { Fail 'SwarmUI finished without returning an image' }

  if (-not $Out) { $script:Out = Join-Path $Root 'out' }
  New-Item -ItemType Directory -Force -Path $Out | Out-Null
  $record = [ordered]@{
    tool = 'comfy-forge generate'; tool_version = (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim()
    preset = $Preset; model = $useModel; prompt = [string]$body['prompt']; negative = [string]$body['negativeprompt']
    width = $body['width']; height = $body['height']; steps = $body['steps']; cfg = $body['cfgscale']
    sampler = $body['sampler']; scheduler = $body['scheduler']; seed = $useSeed
    init_image = $(if ($InitImage) { (Resolve-Path -LiteralPath $InitImage).Path } else { $null })
    creativity = $(if ($initData) { $body['initimagecreativity'] } else { $null })
    mask = $(if ($Mask) { (Resolve-Path -LiteralPath $Mask).Path } else { $null })
    loras = $body['loras']; lora_weights = $body['loraweights']
    transparent = [bool]$Transparent; tileable = [bool]$Tileable; upscale = $(if ($Upscale -gt 1) { $Upscale } else { $null })
    set = $extra
    created = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  }
  $files = @(); $seeds = @(); $index = 0
  foreach ($rel in $r.images) {
    $file = Join-Path $Out (Split-Path -Leaf $rel)
    # SwarmUI returns paths like "View/local/raw/<date>/<file>.png"; older
    # builds return the bare "local/raw/..." path. Handle both.
    $viewPath = if ($rel -like 'View/*') { $rel } else { "View/$rel" }
    $parts = @($viewPath -split '/' | ForEach-Object { [uri]::EscapeDataString($_) })
    $url = "http://127.0.0.1:$port/" + ($parts -join '/')
    Invoke-WebRequest -Uri $url -OutFile $file -TimeoutSec 300 -UseBasicParsing | Out-Null
    # SwarmUI counts the seed up by one for each image of a batch.
    $record['seed'] = $useSeed + $index
    [IO.File]::WriteAllText(($file + '.json'), ($record | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
    $files += (Resolve-Path -LiteralPath $file).Path; $seeds += ($useSeed + $index); $index++
    Ok ("wrote {0}  ({1:N0} KB)" -f $file, ((Get-Item -LiteralPath $file).Length / 1KB))
  }
  if ($Json) {
    Write-Output (ConvertTo-AsciiJson ([ordered]@{
          files = $files; seed = $useSeed; seeds = $seeds; model = $useModel; preset = $Preset
          width = $body['width']; height = $body['height'] }))
  }
}

function Resolve-CheckpointPath([string]$Path, [string]$What) {
  # A path that exists is used as given. A bare file name is looked up in the
  # model trees, so `-Donors "name.safetensors"` works from any folder; two files
  # with that name are an error rather than a guess.
  if (Test-Path -LiteralPath $Path -PathType Leaf) { return $Path }
  if ((Split-Path -Leaf $Path) -ne $Path) { Fail "$What checkpoint not found: $Path" }
  $trees = @()
  $swarm = Get-SwarmRoot
  if ($swarm) {
    $trees += , @((Join-Path $swarm 'Models\Stable-Diffusion'))
    $trees += , @((Join-Path $swarm 'dlbackend\comfy\ComfyUI\models\checkpoints'),
                  (Join-Path $swarm 'dlbackend\comfy\ComfyUI\models\diffusion_models'))
  }
  $comfy = Get-ComfyRoot
  if ($comfy) { $trees += , @((Join-Path $comfy 'models\checkpoints'), (Join-Path $comfy 'models\diffusion_models')) }
  foreach ($tree in $trees) {
    $hits = @()
    foreach ($dir in $tree) {
      if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
      $hits += @(Get-ChildItem -LiteralPath $dir -Recurse -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -eq $Path })
    }
    if ($hits.Count -eq 1) { return $hits[0].FullName }
    if ($hits.Count -gt 1) {
      Fail ("$What checkpoint name '$Path' matches $($hits.Count) files; pass the full path: " +
            (($hits | ForEach-Object { $_.FullName }) -join '; '))
    }
  }
  Fail "$What checkpoint not found: $Path"
}

function Invoke-MergeEngine([string]$Python, [string]$Script, [object[]]$Arguments) {
  # merge_anima.py reports refusals on stderr and the exit code says which one
  # (3 = different lineage, which the caller may retry). Under EAP=Stop, PS 5.1
  # can turn that stderr line into a terminating error, so the retry never ran.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Python $Script @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
    return [int]$LASTEXITCODE
  }
  finally { $ErrorActionPreference = $prev }
}

function Invoke-Merge {
  # Accept -Donors a,b as well as -Donors a,b,c: some shells (and copy-paste
  # from a comma list) deliver the whole list as one string.
  $DonorPaths = @($Donors | ForEach-Object { $_ -split ',' } |
                  ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ })
  if ($DonorPaths.Count -lt 1) { Fail 'merge needs -Donors (one path per checkpoint, up to 4)' }
  $weightText = (@($Weights | ForEach-Object { "$_" -split '[,\s]+' } | Where-Object { $_ }) -join ',')
  # Remember whether the caller chose a base before the default fills it in:
  # a default base must not block the same-lineage retry below.
  $explicitBase = [bool]$Base
  $swarm = Get-SwarmRoot
  if (-not $Base) {
    if (-not $swarm) { Fail 'merge needs -Base, or a SwarmUI install to find Anima-2.9B-preview-v1' }
    $Base = Join-Path $swarm 'Models\Stable-Diffusion\Anima\Anima-2.9B-preview-v1.safetensors'
  }
  $Base = Resolve-CheckpointPath $Base 'base'
  $DonorPaths = @($DonorPaths | ForEach-Object { Resolve-CheckpointPath $_ 'donor' })

  if (-not $Out) {
    $dir = if ($swarm) { Join-Path $swarm 'Models\Stable-Diffusion\Anima' } else { Join-Path $Root 'out' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Out = Join-Path $dir ("Anima-4way-{0}.safetensors" -f $stamp)
  }

  $python = Find-Python
  if (-not $python) { Fail 'no python interpreter was found for the merge; `image-stack.ps1 status` shows what is missing' }
  $mergeScript = Join-Path $PSScriptRoot 'merge_anima.py'
  $reportPath = Join-Path ([IO.Path]::GetTempPath()) ("comfy-forge-merge-" + [guid]::NewGuid().ToString('N').Substring(0,8) + '.json')
  # One builder for every execution: the fallback retry used to rebuild this list
  # by hand and forget --report, so a full checkpoint was routed as UNET-only.
  $buildArgs = {
    param($Anchor)
    $a = @('--base', $Anchor, '--out', $Out, '--method', $Method, '--scale', $Scale,
           '--report', $reportPath)
    foreach ($d in $DonorPaths) { $a += @('--donor', $d) }
    if ($weightText) { $a += @('--weights', $weightText) }
    if ($Force) { $a += '--force' }
    return ,$a
  }
  $argv = & $buildArgs $Base

  Note ("merging {0} donor(s) onto {1}" -f $DonorPaths.Count, (Split-Path -Leaf $Base))
  $exit = Invoke-MergeEngine $python $mergeScript $argv

  # Exit 3 means the base is a different Anima lineage than the donors. Those
  # cannot be blended, and there is no verified ancestor to use instead.
  #
  # The only honest fallback is to treat one donor as the computational anchor
  # and KEEP IT IN THE DONOR LIST with its original weight: its task vector is
  # then zero, but its weight still counts in the denominator, so at scale 1 the
  # blend is exactly sum(w_i*D_i)/sum(w_i) - the recipe the caller asked for.
  # Dropping the anchor and renormalising over the rest silently rewrites the
  # recipe (31.5/27.8/22.2/18.5 becomes 0/40.5/32.4/27.0), so it is opt-in.
  if ($exit -eq 3 -and -not $explicitBase) {
    if (-not $AcceptFallbackAnchor) {
      $msg = ("the base {0} is a different Anima lineage from the donors, so they cannot be blended. " +
              "Pass -Base with a checkpoint from the donors' lineage, or -AcceptFallbackAnchor to anchor " +
              "on {1} while keeping all {2} donors and their weights.") -f `
              (Split-Path -Leaf $Base), (Split-Path -Leaf $DonorPaths[0]), $DonorPaths.Count
      Fail $msg
    }
    $Base = $DonorPaths[0]
    Say ''
    Note ("anchoring on {0}: a different lineage from the requested base, so it becomes both the anchor and donor #1" -f (Split-Path -Leaf $Base))
    Note 'all donors keep their original weights; the anchor contributes a zero task vector rather than being removed'
    $argv = & $buildArgs $Base
    $exit = Invoke-MergeEngine $python $mergeScript $argv
  }
  if ($exit -ne 0) { Fail "merge failed (python exit $exit)" }

  # Make the merge visible in ComfyUI too. A hard link costs no disk space and
  # keeps both trees in sync; a cross-volume fallback copies the file.
  $script:MergeWasFullCheckpoint = $false
  $report = $null
  if (Test-Path -LiteralPath $reportPath) {
    try { $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json } catch { $report = $null }
    Remove-Item -LiteralPath $reportPath -Force -ErrorAction SilentlyContinue
  }
  if ($null -eq $report -or -not $report.out -or -not $report.tensors) {
    # Guessing here is how a full checkpoint ends up in diffusion_models and
    # becomes invisible to CheckpointLoaderSimple. Keep the file, refuse to route.
    Write-Host ("error      the merge wrote {0}, but its report is missing or unreadable; not mirroring it anywhere. Re-run with the same arguments." -f $Out) -ForegroundColor Red
    exit 1
  }
  if ((Resolve-Path -LiteralPath $report.out -ErrorAction SilentlyContinue).Path -ne (Resolve-Path -LiteralPath $Out -ErrorAction SilentlyContinue).Path) {
    Write-Host ("error      merge report describes {0}, not {1}; not mirroring." -f $report.out, $Out) -ForegroundColor Red
    exit 1
  }
  # CheckpointLoaderSimple needs model + clip + vae. One of the two is a partial
  # checkpoint: usable by a diffusion loader, not by "load checkpoint".
  $hasEncoder = [bool]$report.roles.encoder
  $hasVae = [bool]$report.roles.vae
  if ($report.full_checkpoint -and -not ($hasEncoder -and $hasVae)) {
    Note ("checkpoint has {0}; treating it as diffusion-only" -f $(if ($hasEncoder) { 'an encoder but no VAE' } else { 'a VAE but no encoder' }))
  }
  $script:MergeWasFullCheckpoint = ($hasEncoder -and $hasVae)
  Note ("output is a {0} ({1} tensors)" -f $(if ($script:MergeWasFullCheckpoint) { 'full checkpoint' } else { 'diffusion-only model' }), $report.tensors)

  if (-not $NoMirror) {
    # A merge made from a full checkpoint carries encoder + VAE and is loadable
    # by CheckpointLoaderSimple; a UNET-only merge is not.
    $kind = if ($script:MergeWasFullCheckpoint) { 'checkpoint' } else { 'diffusion' }
    $comfyDir = Get-ComfyModelDir -Kind $kind
    if ($comfyDir) {
      $r = Set-ModelMirror -Source $Out -DestinationDirectory $comfyDir
      switch ($r.Kind) {
        'linked'    { Ok ("ComfyUI: " + $r.Message) }
        'in-place'  { Note ("ComfyUI: " + $r.Message) }
        'copied'    { Ok ("ComfyUI: " + $r.Message) }
        default     { Write-Host ("warning    ComfyUI: " + $r.Message) -ForegroundColor Yellow }
      }
    }
  }

  if ($swarm -and $Out.StartsWith($swarm, [StringComparison]::OrdinalIgnoreCase)) {
    Note 'refreshing the SwarmUI model list so the merge appears in the UI'
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$(Get-SwarmPort)/API/TriggerRefresh" -Method Post `
            -ContentType 'application/json' -Body (@{ session_id = (New-SwarmSession (Get-SwarmPort)) } | ConvertTo-Json) `
            -TimeoutSec 30 -UseBasicParsing | Out-Null } catch { Note 'SwarmUI not running; refresh happens on next start' }
    Ok ("model appears as: " + [IO.Path]::GetFileNameWithoutExtension($Out))
  }
}

function Show-Mcp {
  $cp = if ($Port) { $Port } else { Get-ComfyUIPort }
  if (-not $cp) { $cp = 8188 }

  # Compact mode registers 3 meta-tools instead of ~200 schemas: the right
  # choice for a chat agent, and the only sane one for a local model.
  $config = [ordered]@{
    mcpServers = [ordered]@{
      'comfyui' = [ordered]@{
        command = 'npx'
        # --comfyui-url is what the server actually honours; COMFYUI_HOST/PORT
        # are ignored and it falls back to 8188, which is rarely where SwarmUI's
        # backend lives. COMFYUI_URL alone was also ignored on 0.52.206.
        args    = @('-y', 'comfyui-mcp@0.52.206', '--comfyui-url', "http://127.0.0.1:$cp")
        env     = [ordered]@{
          COMFYUI_MCP_TOOL_MODE = 'compact'
        }
      }
    }
  }
  $json = $config | ConvertTo-Json -Depth 8
  if (-not $Write) {
    Say '# ComfyUI MCP client config (comfyui-mcp 0.52.206, compact mode)'
    Say ("# target: http://127.0.0.1:{0}/" -f $cp)
    Say $json
    Say ''
    Say '# write it with:  image-stack.ps1 mcp -Write -Agent claude   (or -Agent <path to a JSON config>)'
    return
  }
  $target = switch ($Agent) {
    'hermes' { Join-Path $env:LOCALAPPDATA 'hermes\config.yaml' }
    'claude' { Join-Path $HOME '.claude.json' }
    'codex'  { Join-Path $HOME '.codex\config.toml' }
    default  { $Agent }
  }
  if ($Agent -in @('hermes', 'codex')) {
    Note "Hermes and Codex do not read a JSON mcpServers block; add this to $target by hand:"
    Say $json
    return
  }
  # The config is the user's file and holds far more than this one entry, so the
  # edit goes through TOOLS\mcp_config.py. PowerShell 5.1's JSON cmdlets read
  # UTF-8 as ANSI, turn anything nested past their depth limit into a string and
  # write a BOM; that round trip damaged a real config and kept no backup.
  $py = Find-Python
  if (-not $py) {
    Write-Host "error  no python was found, so $target was not edited; add this block to it by hand:" -ForegroundColor Red
    Say $json
    exit 1
  }
  $serverFile = Join-Path ([IO.Path]::GetTempPath()) ('comfy-forge-mcp-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.json')
  # A file, not an argument: PowerShell 5.1 strips the quotes out of JSON passed to a native command.
  [IO.File]::WriteAllText($serverFile, ($config.mcpServers.comfyui | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding $false))
  try {
    & $py (Join-Path $PSScriptRoot 'mcp_config.py') --target $target --name comfyui --server-file $serverFile
    $code = $LASTEXITCODE
  }
  finally { Remove-Item -LiteralPath $serverFile -Force -ErrorAction SilentlyContinue }
  if ($code -ne 0) { Fail "the comfyui server was not written into $target" }
  Ok "comfyui server is in $target (port $cp)"
}

if ($Help) { $Verb = 'help' }
switch ($Verb) {
  'status'   { Show-Status }
  'capabilities' { Show-Capabilities }
  'start'    { Start-Backend }
  'stop'     { Stop-Backend }
  'install'  { Invoke-Install }
  'presets'  { Show-Presets }
  'generate' { Invoke-Generate }
  'merge'    { Invoke-Merge }
  'mcp'      { Show-Mcp }
  'python'   {
    # Machine-readable on purpose: one line, the path, nothing else.
    $found = Find-Python
    if (-not $found) { exit 1 }
    Write-Output $found
  }
  'help'     { Get-Help $PSCommandPath -Detailed }
}
