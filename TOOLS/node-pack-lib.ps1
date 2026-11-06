# Node-pack state evaluation, kept in its own file so tests can load it without
# running the CLI. `image-stack.ps1` dot-sources this.
#
# One rule: a directory is not an installation. "locked" means the right
# repository, at the pinned commit, with a status command that actually ran and
# reported no changes. Everything else is named for what it is.

function Get-OriginSlug([string]$Url) {
  # git@github.com:o/r.git, https://github.com/o/r, https://github.com/o/r.git
  # and a trailing slash all name the same repository.
  if (-not $Url) { return $null }
  $u = $Url.Trim()
  if ($u -match '^git@([^:]+):(.+?)(?:\.git)?/?$') { return ($matches[1] + '/' + $matches[2]).ToLower() }
  if ($u -match '^(?:https?|ssh)://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$') { return ($matches[1] + '/' + $matches[2]).ToLower() }
  return $u.ToLower()
}

function Invoke-GitText([string]$Dir, [string[]]$GitArgs) {
  # PS 5.1 turns native stderr into a terminating error under EAP=Stop, so a
  # "no such remote" message would abort the evaluator instead of returning an
  # empty result. Capture text and exit code explicitly.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = (& git -C $Dir @GitArgs 2>$null | Out-String).Trim()
    $code = [int]$LASTEXITCODE
  }
  finally { $ErrorActionPreference = $prev }
  return [pscustomobject]@{ Out = $out; Code = $code }
}

function Test-GitCommand {
  # Some Windows installs ship a `git` shim that fails instead of a repository.
  $r = Invoke-GitText (Get-Location).Path @('--version')
  return ($r.Code -eq 0)
}

function Get-NodePackState([string]$Dir, $Pin) {
  $dest = Join-Path $Dir $Pin.name
  if (-not (Test-Path -LiteralPath $dest)) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'missing'; Sha = $null; Pin = $Pin.sha; Detail = $null }
  }
  if (-not (Test-GitCommand)) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'unverifiable'; Sha = $null; Pin = $Pin.sha; Detail = 'git missing' }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $dest '.git'))) {
    # A folder can exist without being the repository we pinned (copied pack, or
    # a checkout that carries somebody else's .git). It cannot be verified.
    return [pscustomobject]@{ Name = $Pin.name; State = 'unverifiable'; Sha = $null; Pin = $Pin.sha; Detail = 'not a git checkout' }
  }
  $originR = Invoke-GitText $dest @('remote', 'get-url', 'origin')
  $headR = Invoke-GitText $dest @('rev-parse', 'HEAD')
  $origin = $originR.Out
  $head = $headR.Out
  if ($headR.Code -ne 0 -or -not $head) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'unverifiable'; Sha = $null; Pin = $Pin.sha; Detail = 'no valid HEAD' }
  }
  # No origin at all is unverifiable, not locked: nothing proves the repository.
  if (-not $origin) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'unverifiable'; Sha = $head; Pin = $Pin.sha; Detail = 'no origin remote' }
  }
  if ((Get-OriginSlug $origin) -ne (Get-OriginSlug $Pin.url)) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'foreign-repo'; Sha = $head; Pin = $Pin.sha; Detail = $origin }
  }
  if ($head -ne $Pin.sha) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'drift'; Sha = $head; Pin = $Pin.sha; Detail = $null }
  }
  $statusR = Invoke-GitText $dest @('status', '--porcelain')
  $dirty = $statusR.Out
  if ($statusR.Code -ne 0) {
    # A status command that failed proves nothing about the tree. Calling that
    # "locked" would vouch for contents nobody checked.
    return [pscustomobject]@{ Name = $Pin.name; State = 'unverifiable'; Sha = $head; Pin = $Pin.sha; Detail = 'git status failed' }
  }
  if ($dirty) {
    return [pscustomobject]@{ Name = $Pin.name; State = 'dirty'; Sha = $head; Pin = $Pin.sha; Detail = $null }
  }
  return [pscustomobject]@{ Name = $Pin.name; State = 'locked'; Sha = $head; Pin = $Pin.sha; Detail = $null }
}

function Clear-OccupiedPackDir([string]$Dest) {
  <#
    Returns 'free', 'aside:<path>' or 'checkout'.

    `git clone` refuses to write into a directory that already exists, so a
    failed clone leaves a folder that blocks every later install. A folder with
    no .git is an incomplete download, not a working copy: move it aside (never
    delete it). A real checkout is left completely alone - resetting it would
    discard the user's edits - and reported so the caller can explain.
  #>
  if (-not (Test-Path -LiteralPath $Dest)) { return 'free' }
  if (Test-Path -LiteralPath (Join-Path $Dest '.git')) { return 'checkout' }
  $aside = $Dest + '.unverified-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
  Move-Item -LiteralPath $Dest -Destination $aside -Force
  return ('aside:' + $aside)
}
