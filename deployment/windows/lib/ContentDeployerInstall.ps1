$ErrorActionPreference = 'Stop'

# Helpers for Install-ContentReleaseDeployer.ps1. Kept free of host paths and identities so they
# can be tested on any machine. Windows PowerShell 5.1 compatible (the VM's shell).

function Get-ContentInstallPlan {
    # Which files of a set need copying: missing at the destination, or different by SHA-256.
    # Returns one entry per file, so the installer can back up exactly what it replaces.
    param(
        [Parameter(Mandatory)][string]$SourceRoot,
        [Parameter(Mandatory)][string]$DestinationRoot,
        [Parameter(Mandatory)][hashtable]$Files   # destination relative path -> source relative path
    )
    foreach ($destination in ($Files.Keys | Sort-Object)) {
        $from = Join-Path $SourceRoot $Files[$destination]
        $to = Join-Path $DestinationRoot $destination
        if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "Source file is missing: $from" }
        $exists = Test-Path -LiteralPath $to -PathType Leaf
        $same = $exists -and ((Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash)
        [pscustomobject]@{ Destination = $destination; Source = $from; Target = $to; Exists = $exists; Changed = -not $same }
    }
}

function Test-ContentAclAllows {
    # True when $Path carries an Allow rule for $Sid that includes all of $Rights (inherited or not).
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Sid, [Parameter(Mandatory)][string]$Rights)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $wanted = [Security.AccessControl.FileSystemRights]$Rights
    $rules = @((Get-Acl -LiteralPath $Path).Access | Where-Object {
        $_.AccessControlType -eq 'Allow' -and
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $Sid -and
        ($_.FileSystemRights -band $wanted) -eq $wanted
    })
    return $rules.Count -gt 0
}

function Test-ContentAclDenies {
    # True when $Path carries a Deny rule for $Sid that includes $Rights.
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Sid, [Parameter(Mandatory)][string]$Rights)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $wanted = [Security.AccessControl.FileSystemRights]$Rights
    $rules = @((Get-Acl -LiteralPath $Path).Access | Where-Object {
        $_.AccessControlType -eq 'Deny' -and
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $Sid -and
        ($_.FileSystemRights -band $wanted) -eq $wanted
    })
    return $rules.Count -gt 0
}

function Get-ContentSignerPrincipal {
    # The principal names in an allowed_signers file (first field of each non-comment line).
    param([Parameter(Mandatory)][string]$Path)
    @(Get-Content -LiteralPath $Path | Where-Object { $_.Trim() -and -not $_.TrimStart().StartsWith('#') } |
        ForEach-Object { ($_.Trim() -split '\s+')[0] })
}

function New-ContentDeployerSettings {
    # The deployer's deploy.json. Validated here so a bad value fails the install, not the first run.
    param(
        [Parameter(Mandatory)][string]$Git,
        [Parameter(Mandatory)][string]$Ssh,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string]$Signer,
        [Parameter(Mandatory)][int]$CheckMinutes
    )
    if ($Repository -notmatch '^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$') {
        throw "Repository must be a GitHub SSH URL (git@github.com:owner/name.git): $Repository"
    }
    if ($Signer -notmatch '^[A-Za-z0-9_.@-]+$') { throw "Signer is not a valid allowed_signers principal: $Signer" }
    if ($CheckMinutes -lt 1 -or $CheckMinutes -gt 60) { throw 'CheckMinutes must be between 1 and 60.' }
    foreach ($p in $Git, $Ssh) { if ($p.Contains('"')) { throw "A tool path contains a quote: $p" } }
    [ordered]@{ git = $Git; ssh = $Ssh; repository = $Repository; signer = $Signer; check_minutes = $CheckMinutes }
}

function Get-ContentDeployerTaskSpec {
    # The scheduled task the deployer runs from. The installer compares an existing task with this
    # and re-registers it only when it differs; it never takes over a task with another identity.
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$PowerShell)
    $script = Join-Path $Root 'tools\deploy\Invoke-ContentReleaseDeployer.ps1'
    [pscustomobject]@{
        Name               = 'RelicAndRiddle-ReleaseDeployer-Unattended'
        Identity           = 'LAB\rr-deploy$'
        Description        = 'HNSHL release deployer v1: installs smartcard-signed releases when production is idle (docs/deployment.md)'
        Executable         = $PowerShell
        Arguments          = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$script`""
        WorkingDirectory   = $Root
        RepeatInterval     = 'PT1M'
        BootDelay          = 'PT2M'
        ExecutionTimeLimit = 'PT2H'   # suite (40 min) + in-window verification (45 min) + 3 min watch
        MultipleInstances  = 2        # TASK_INSTANCES_IGNORE_NEW: one deployer at a time
    }
}

function Test-ContentDeployerTaskMatches {
    # Compares a registered task definition (COM object or test double) with the spec.
    param([Parameter(Mandatory)]$Definition, [Parameter(Mandatory)]$Spec)
    $actions = @($Definition.Actions)
    $triggers = @($Definition.Triggers)
    $boot = @($triggers | Where-Object { $_.Type -eq 8 -and $_.Delay -eq $Spec.BootDelay -and $_.Enabled })
    $repeat = @($triggers | Where-Object { $_.Type -eq 1 -and $_.Repetition.Interval -eq $Spec.RepeatInterval -and
        -not $_.Repetition.Duration -and $_.Enabled })
    return ($Definition.RegistrationInfo.Description -ceq $Spec.Description -and
        $Definition.Principal.LogonType -eq 1 -and $Definition.Principal.RunLevel -eq 0 -and
        $actions.Count -eq 1 -and $actions[0].Path -ieq $Spec.Executable -and $actions[0].Arguments -ceq $Spec.Arguments -and
        $triggers.Count -eq 2 -and $boot.Count -eq 1 -and $repeat.Count -eq 1 -and
        $Definition.Settings.ExecutionTimeLimit -eq $Spec.ExecutionTimeLimit -and
        $Definition.Settings.MultipleInstances -eq $Spec.MultipleInstances -and
        $Definition.Settings.StartWhenAvailable)
}
