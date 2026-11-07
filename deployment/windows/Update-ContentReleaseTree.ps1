<#
.SYNOPSIS
Updates the application tree on the production VM inside the promotion window.
.DESCRIPTION
Run from the operator's SSH session (no elevation) after Promote-ContentRelease.ps1 has written
"stopped" to the hand-off flag. Expects the release staged beforehand in $Stage:
  rr-release.bundle        git bundle of the release (fetched into refs/remotes/stage/release)
  realesrgan_x4plus.onnx   the upscaler model (verified against its SHA-256)
  wheels\                  new dependency wheels (offline install; falls back to PyPI)

Steps: check out the commit (production data files the release does not touch keep their local
changes), install requirements, place the model, run the test suite, then write
"ready <commit>". On any failure it returns the tree to the previous commit and signals that one
instead, so the pipeline restarts on the version that was running.
#>
param(
    [Parameter(Mandatory)][string]$Commit,
    [string]$App = "$HOME\relic-and-riddle",
    [string]$Stage = "$HOME\relic-stage",
    [string]$HandoffFlag = "$HOME\relic-promote.flag",
    [string]$ModelSha256 = '1dd781a70b7e849b473bf8cdb801f8cf1abfc42438de313048227691a854a3d3'
)
$ErrorActionPreference = 'Stop'
function Write-Step($text) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $text) }
function Invoke-Native([scriptblock]$Call, [string]$What) {
    & $Call
    if ($LASTEXITCODE) { throw "$What failed (exit $LASTEXITCODE)" }
}

if (-not ((Get-Content -LiteralPath $HandoffFlag -TotalCount 1 -ErrorAction SilentlyContinue) -match '^stopped')) {
    throw 'The pipeline is not stopped (no "stopped" in the hand-off flag); run Promote-ContentRelease.ps1 first.'
}
Set-Location $App
$python = Join-Path $App '.venv\Scripts\python.exe'
$previous = (git rev-parse HEAD).Trim()
$target = (git rev-parse "$Commit^{commit}").Trim()
Write-Step "Updating $previous -> $target"
try {
    # A file the release changes but production already holds identically (line endings aside) would block
    # checkout as a local change; take the release copy only when git sees no content difference.
    $releaseFiles = git diff --name-only HEAD $target
    foreach ($file in (git status --porcelain --untracked-files=no | ForEach-Object { $_.Substring(3) })) {
        if ($releaseFiles -contains $file) {
            if (git diff --ignore-cr-at-eol $target -- $file) { throw "Production changed $file differently from the release; resolve it manually." }
            Invoke-Native { git checkout $target -- $file } "checkout of $file"
        }
    }
    Invoke-Native { git checkout --quiet --detach $target } 'checkout'
    Write-Step "Checked out $(git describe --tags --always HEAD)"

    & $python -m pip install --quiet --no-index --find-links (Join-Path $Stage 'wheels') -r requirements.txt
    if ($LASTEXITCODE) {
        Write-Step 'Offline install incomplete; installing from PyPI.'
        Invoke-Native { & $python -m pip install --quiet -r requirements.txt } 'pip install'
    }
    Write-Step 'Requirements installed.'

    $model = Join-Path $App 'tools\models\realesrgan_x4plus.onnx'
    New-Item -ItemType Directory -Force (Split-Path $model) | Out-Null
    Copy-Item -Force (Join-Path $Stage 'realesrgan_x4plus.onnx') $model
    if ((Get-FileHash $model).Hash -ne $ModelSha256.ToUpper()) { throw 'Upscaler model hash mismatch.' }
    Write-Step 'Upscaler model placed and verified.'

    $env:PATH = 'C:\ProgramData\HNSHL\ContentRuntime\bin;' + $env:PATH
    Invoke-Native { & $python -m pytest -q -p no:cacheprovider -n 4 } 'test suite'
    Set-Content -LiteralPath $HandoffFlag -Value "ready $target" -Encoding ASCII
    Write-Step "ready $target"
} catch {
    Write-Warning "Update failed: $_. Returning to $previous."
    git checkout --quiet --detach $previous
    & $python -m pip install --quiet -r requirements.txt
    Set-Content -LiteralPath $HandoffFlag -Value "ready $previous" -Encoding ASCII
    Write-Step "ready $previous (rolled back; the pipeline restarts on the previous version)"
    exit 1
}
