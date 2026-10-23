$ErrorActionPreference = 'Stop'

# Helpers for Invoke-ContentReleaseDeployer.ps1. Kept free of host paths and identities so they
# can be tested on any machine. Windows PowerShell 5.1 compatible (the VM's shell).

function Invoke-ContentNative {
    # Runs a native command and returns its exit code and output. Windows PowerShell 5.1 turns a
    # native command's stderr into terminating errors under $ErrorActionPreference = 'Stop', and
    # git reports normal progress on stderr, so commands are run as processes instead.
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [hashtable]$Environment = @{},
        [string[]]$RemoveEnvironment = @(),
        [int]$TimeoutSeconds = 600
    )
    $quoted = foreach ($a in $ArgumentList) {
        if ($a -eq '') { '""' }
        elseif ($a -notmatch '[\s"]') { $a }
        else { '"' + ($a -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
    }
    $process = [Diagnostics.Process]::new()
    try {
        $process.StartInfo.FileName = $FilePath
        $process.StartInfo.Arguments = ($quoted -join ' ')
        $process.StartInfo.WorkingDirectory = $WorkingDirectory
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        foreach ($name in $Environment.Keys) { $process.StartInfo.EnvironmentVariables[$name] = [string]$Environment[$name] }
        foreach ($name in $RemoveEnvironment) { $process.StartInfo.EnvironmentVariables.Remove($name) }
        if (-not $process.Start()) { throw "$FilePath did not start." }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            & "$env:SystemRoot/System32/taskkill.exe" /PID $process.Id /T /F | Out-Null
            $process.WaitForExit()
            throw "$([IO.Path]::GetFileName($FilePath)) exceeded $TimeoutSeconds seconds and was stopped."
        }
        $process.WaitForExit()
        [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdout.Result; Stderr = $stderr.Result }
    } finally { $process.Dispose() }
}

function ConvertTo-ContentReleaseVersion {
    # v1.2.3 or v1.2.3-rcN; a final release sorts after its release candidates. Anything else is
    # not a release tag.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Tag)
    if ($Tag -notmatch '^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$') { return $null }
    $rc = if ($Matches[4]) { [int]$Matches[4] } else { [int]::MaxValue }
    [pscustomobject]@{ Tag = $Tag; Key = [long[]]@([long]$Matches[1], [long]$Matches[2], [long]$Matches[3], [long]$rc) }
}

function Compare-ContentReleaseVersion {
    param([Parameter(Mandatory)]$Left, [Parameter(Mandatory)]$Right)
    for ($i = 0; $i -lt 4; $i++) {
        if ($Left.Key[$i] -ne $Right.Key[$i]) { return [Math]::Sign($Left.Key[$i] - $Right.Key[$i]) }
    }
    return 0
}

function Select-ContentReleaseCandidate {
    # The newest release tag newer than the running one, skipping tags already turned down.
    param(
        [string[]]$Tags = @(),
        [Parameter(Mandatory)][AllowEmptyString()][string]$Current,
        [string[]]$Rejected = @()
    )
    $running = ConvertTo-ContentReleaseVersion -Tag $Current
    if (-not $running) { return $null }
    $best = $null
    foreach ($tag in $Tags) {
        if ($Rejected -contains $tag) { continue }
        $version = ConvertTo-ContentReleaseVersion -Tag $tag
        if (-not $version -or (Compare-ContentReleaseVersion $version $running) -le 0) { continue }
        if (-not $best -or (Compare-ContentReleaseVersion $version $best) -gt 0) { $best = $version }
    }
    if ($best) { return $best.Tag }
    return $null
}

function Test-ContentReleaseSignature {
    # A release is installed only if its annotated tag carries an SSH signature from the pinned
    # signer (the operator's smartcard key). The signer list is installed by host administration
    # and is read-only to the deployer, so neither a repository write nor this account can widen it.
    param(
        [Parameter(Mandatory)][string]$Git,
        [Parameter(Mandatory)][string]$App,
        [Parameter(Mandatory)][string]$Tag,
        [Parameter(Mandatory)][string]$SignersFile,
        [Parameter(Mandatory)][string]$Signer
    )
    $type = Invoke-ContentGit -Git $Git -App $App -Arguments @('cat-file', '-t', "refs/tags/$Tag")
    if ($type.ExitCode -ne 0 -or $type.Stdout.Trim() -ne 'tag') {
        return [pscustomobject]@{ Valid = $false; Detail = "$Tag is not an annotated tag" }
    }
    $signers = $SignersFile.Replace('\', '/')
    $check = Invoke-ContentGit -Git $Git -App $App -Arguments @('-c', 'gpg.format=ssh', '-c', "gpg.ssh.allowedSignersFile=$signers",
        'verify-tag', $Tag)
    $good = $check.ExitCode -eq 0 -and $check.Stderr -match ('Good "git" signature for ' + [regex]::Escape($Signer) + ' with ')
    $detail = if ($good) { "signed by $Signer" } else { "$Tag has no valid signature from $Signer" }
    [pscustomobject]@{ Valid = $good; Detail = $detail }
}

function Invoke-ContentGit {
    # git as the deployer on a checkout another account owns: trust exactly this one directory.
    param(
        [Parameter(Mandatory)][string]$Git,
        [Parameter(Mandatory)][string]$App,
        [Parameter(Mandatory)][string[]]$Arguments,
        [hashtable]$Environment = @{},
        [int]$TimeoutSeconds = 600
    )
    $safe = $App.Replace('\', '/')
    Invoke-ContentNative -FilePath $Git -ArgumentList (@('-c', "safe.directory=$safe", '-C', $App) + $Arguments) `
        -WorkingDirectory $App -Environment $Environment -TimeoutSeconds $TimeoutSeconds
}

function Assert-ContentGit {
    param([Parameter(Mandatory)]$Result, [Parameter(Mandatory)][string]$What)
    if ($Result.ExitCode -ne 0) { throw "$What failed (exit $($Result.ExitCode)): $($Result.Stderr.Trim())" }
    return $Result.Stdout
}

function Update-ContentTree {
    # Moves the checkout to $Target. A production data file the release also changes keeps
    # production's copy unless the two differ only in line endings (then the release's is taken);
    # a real difference stops the update. Requirements are installed when they changed.
    param(
        [Parameter(Mandatory)][string]$Git,
        [Parameter(Mandatory)][string]$App,
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$Python
    )
    $head = (Assert-ContentGit (Invoke-ContentGit -Git $Git -App $App -Arguments @('rev-parse', 'HEAD')) 'reading HEAD').Trim()
    $releaseFiles = @((Assert-ContentGit (Invoke-ContentGit -Git $Git -App $App -Arguments @('diff', '--name-only', $head, $Target)) 'listing release files') -split "`n" |
        ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $status = Assert-ContentGit (Invoke-ContentGit -Git $Git -App $App -Arguments @('status', '--porcelain', '--untracked-files=no')) 'reading local changes'
    foreach ($line in ($status -split "`n" | Where-Object { $_.Trim() })) {
        $file = $line.Substring(3).Trim()
        if ($releaseFiles -notcontains $file) { continue }
        $difference = Invoke-ContentGit -Git $Git -App $App -Arguments @('diff', '--ignore-cr-at-eol', $Target, '--', $file)
        if ($difference.Stdout.Trim()) { throw "Production changed $file differently from the release; resolve it manually." }
        Assert-ContentGit (Invoke-ContentGit -Git $Git -App $App -Arguments @('checkout', $Target, '--', $file)) "checkout of $file" | Out-Null
    }
    Assert-ContentGit (Invoke-ContentGit -Git $Git -App $App -Arguments @('checkout', '--quiet', '--detach', $Target)) 'checkout' | Out-Null
    $requirements = Invoke-ContentGit -Git $Git -App $App -Arguments @('diff', '--quiet', $head, $Target, '--', 'requirements.txt')
    if ($requirements.ExitCode -ne 0) {
        $pip = Invoke-ContentNative -FilePath $Python -ArgumentList @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', 'requirements.txt') `
            -WorkingDirectory $App -TimeoutSeconds 1800
        if ($pip.ExitCode -ne 0) { throw "Installing the release's requirements failed (exit $($pip.ExitCode)): $($pip.Stderr.Trim())" }
    }
    [pscustomobject]@{ Previous = $head; Current = $Target; RequirementsChanged = ($requirements.ExitCode -ne 0) }
}

function Read-ContentJson {
    param([Parameter(Mandatory)][string]$Path)
    try { return (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { return $null }
}

function Write-ContentJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function ConvertTo-ContentUtc {
    # Timestamps in the state files are ISO 8601 UTC. Windows PowerShell 5.1 leaves them as text and
    # a plain [datetime] cast turns them into local time, which then compares wrongly with UtcNow.
    param([Parameter(Mandatory)][AllowNull()]$Value)
    if ($null -eq $Value -or $Value -eq '') { return [datetime]::MinValue }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
    return [DateTimeOffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
}

function Wait-ContentCondition {
    # Polls $Condition until it returns something truthy or the time runs out ($null).
    param(
        [Parameter(Mandatory)][scriptblock]$Condition,
        [Parameter(Mandatory)][double]$Minutes,
        [double]$PollSeconds = 3
    )
    $deadline = [datetime]::UtcNow.AddMinutes($Minutes)
    do {
        $value = & $Condition
        if ($value) { return $value }
        Start-Sleep -Milliseconds ([int]($PollSeconds * 1000))
    } while ([datetime]::UtcNow -lt $deadline)
    return $null
}
