$ErrorActionPreference = 'Stop'

# The launcher's side of release deployment. The release deployer (its own account, no admin rights)
# writes control\Pipeline.hold; the launcher stops its worker tree and waits, still running as the
# managed task. While held it runs the deployer's verification request (control\Pipeline.verify:
# the commit the tree should now be at) under the pipeline account, with production's own settings,
# and reports each step in logs\Pipeline\Hold.json. Removing the hold resumes the worker.

function Write-ContentHoldState {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('held', 'verifying', 'verified', 'resumed')][string]$State,
        [string]$Commit = '',
        [object]$Passed = $null,
        [object]$ExitCode = $null,
        [string]$Detail = ''
    )
    $record = [ordered]@{
        State = $State; Commit = $Commit; Passed = $Passed; ExitCode = $ExitCode; Detail = $Detail
        LauncherPid = $PID; AtUtc = [datetime]::UtcNow.ToString('o')
    }
    $temporary = "$Path.tmp"
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Get-ContentVerifyRequest {
    param([Parameter(Mandatory)][string]$Path)
    $line = Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction SilentlyContinue
    if ($line -and $line.Trim() -match '^[0-9a-f]{40}$') { return $line.Trim() }
    return $null
}

function Get-ContentTreeCommit {
    # The deployer checks releases out detached, so .git\HEAD holds the commit itself. Read directly:
    # running git as this account would trip its ownership check on another account's checkout.
    param([Parameter(Mandatory)][string]$AppRoot)
    $head = Get-Content -LiteralPath (Join-Path $AppRoot '.git\HEAD') -TotalCount 1 -ErrorAction SilentlyContinue
    if ($head -and $head.Trim() -match '^[0-9a-f]{40}$') { return $head.Trim() }
    return $null
}

function Invoke-ContentReleaseVerification {
    # The offline test suite for the tree as it now stands, as this account with production's settings.
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$AppRoot,
        [Parameter(Mandatory)][string]$LogDirectory,
        [Parameter(Mandatory)][string]$Commit,
        [int]$TimeoutMinutes = 30
    )
    . (Join-Path $PSScriptRoot 'ContentWorkloadProcess.ps1')
    $actual = Get-ContentTreeCommit -AppRoot $AppRoot
    if ($actual -ne $Commit) { return [pscustomobject]@{ Passed = $false; ExitCode = $null; Detail = "the tree is at $actual, not $Commit" } }
    $name = 'verify-' + $Commit.Substring(0, 12)
    $previous = $env:PYTHONDONTWRITEBYTECODE
    $env:PYTHONDONTWRITEBYTECODE = '1'  # this account has no write access to the code
    try {
        $run = Start-ContentLoggedProcess -FilePath $Python -Arguments '-m pytest -q -p no:cacheprovider' -WorkingDirectory $AppRoot `
            -StdoutPath (Join-Path $LogDirectory "$name.stdout.log") -StderrPath (Join-Path $LogDirectory "$name.stderr.log")
    } finally { $env:PYTHONDONTWRITEBYTECODE = $previous }
    try {
        if (-not $run.Process.WaitForExit($TimeoutMinutes * 60 * 1000)) {
            & "$env:SystemRoot/System32/taskkill.exe" /PID $run.Process.Id /T /F | Out-Null
            $run.Process.WaitForExit()
            return [pscustomobject]@{ Passed = $false; ExitCode = $null; Detail = "the test suite exceeded $TimeoutMinutes minutes" }
        }
        $code = Complete-ContentLoggedProcess -Worker $run
        $detail = if ($code -eq 0) { 'the test suite passed' } else { "the test suite failed (exit $code); see $name.stdout.log" }
        return [pscustomobject]@{ Passed = ($code -eq 0); ExitCode = $code; Detail = $detail }
    } finally { Close-ContentLoggedProcess -Worker $run }
}

function Wait-ContentReleaseHold {
    # Returns 'Resumed' once the hold is removed, or 'Stopped' if the stop file appears meanwhile.
    param(
        [Parameter(Mandatory)][string]$HoldPath,
        [Parameter(Mandatory)][string]$StopPath,
        [Parameter(Mandatory)][string]$VerifyPath,
        [Parameter(Mandatory)][string]$StatePath,
        [Parameter(Mandatory)][scriptblock]$Verify,
        [scriptblock]$Alert = {},
        [int]$AlertAfterMinutes = 180,
        [double]$PollSeconds = 2
    )
    Write-ContentHoldState -Path $StatePath -State held
    $since = [datetime]::UtcNow
    $alerted = $false
    $verified = $null
    while (Test-Path -LiteralPath $HoldPath) {
        if (Test-Path -LiteralPath $StopPath) { return 'Stopped' }
        $request = Get-ContentVerifyRequest -Path $VerifyPath
        if ($request -and $request -ne $verified) {
            Write-ContentHoldState -Path $StatePath -State verifying -Commit $request
            try { $result = & $Verify $request }
            catch { $result = [pscustomobject]@{ Passed = $false; ExitCode = $null; Detail = "verification could not run: $_" } }
            $verified = $request
            Write-ContentHoldState -Path $StatePath -State verified -Commit $request -Passed ([bool]$result.Passed) `
                -ExitCode $result.ExitCode -Detail ([string]$result.Detail)
        }
        if (-not $alerted -and [datetime]::UtcNow -gt $since.AddMinutes($AlertAfterMinutes)) {
            & $Alert
            $alerted = $true
        }
        Start-Sleep -Milliseconds ([int]($PollSeconds * 1000))
    }
    Write-ContentHoldState -Path $StatePath -State resumed -Commit ([string]$verified)
    return 'Resumed'
}
