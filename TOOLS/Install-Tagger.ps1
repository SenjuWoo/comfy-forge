# Installs the WD14 v3 image taggers used by TOOLS/anima_dataset.py.
# Weights are Apache-2.0 and land outside the repository, so nothing here is committed.
#
#   powershell -File TOOLS/Install-Tagger.ps1            # default: eva02 (best quality)
#   powershell -File TOOLS/Install-Tagger.ps1 -Name swinv2
#
[CmdletBinding()]
param(
    [ValidateSet('eva02', 'swinv2', 'both')]
    [string]$Name = 'eva02',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repos = @{
    eva02  = @{ repo = 'SmilingWolf/wd-eva02-large-tagger-v3'; size = 1260435999 }
    swinv2 = @{ repo = 'SmilingWolf/wd-swinv2-tagger-v3';      size = 467460978 }
}

$root = if ($env:COMFY_KIT_TAGGER_DIR) { $env:COMFY_KIT_TAGGER_DIR }
        else { Join-Path $env:LOCALAPPDATA 'comfy-kit\taggers' }

$targets = if ($Name -eq 'both') { @('eva02', 'swinv2') } else { @($Name) }

foreach ($key in $targets) {
    $repo = $repos[$key].repo
    $expected = $repos[$key].size
    $dir = Join-Path $root $repo
    New-Item -ItemType Directory -Path $dir -Force | Out-Null

    foreach ($file in @('model.onnx', 'selected_tags.csv')) {
        $dest = Join-Path $dir $file
        if ((Test-Path -LiteralPath $dest) -and -not $Force) {
            $have = (Get-Item -LiteralPath $dest).Length
            if ($have -gt 0 -and ($file -ne 'model.onnx' -or $have -eq $expected)) {
                Write-Host "ok   $repo/$file already present ($have bytes)"
                continue
            }
        }
        $url = "https://huggingface.co/$repo/resolve/main/$file"
        Write-Host "get  $repo/$file"
        $tmp = "$dest.part"
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        $size = (Get-Item -LiteralPath $tmp).Length
        if ($file -eq 'model.onnx' -and $size -ne $expected) {
            Remove-Item -LiteralPath $tmp -Force
            throw "$repo/model.onnx is $size bytes, expected $expected - download truncated"
        }
        if ($file -eq 'selected_tags.csv' -and $size -lt 1000) {
            Remove-Item -LiteralPath $tmp -Force
            throw "$repo/selected_tags.csv looks wrong ($size bytes)"
        }
        Move-Item -LiteralPath $tmp -Destination $dest -Force
        Write-Host "ok   $repo/$file ($size bytes)"
    }
}

Write-Host ""
Write-Host "taggers in $root"
Write-Host "run: python TOOLS/anima_dataset.py --images <folder> --trigger <word> --concept `"<idea>`""
