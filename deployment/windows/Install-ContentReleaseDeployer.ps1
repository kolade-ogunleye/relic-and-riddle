#Requires -Version 5.1
<#
.SYNOPSIS
One-time, elevated installation of the release deployer on content-client-win-01 (approved host
administration).
.DESCRIPTION
Run once on CONTENTCLIENTWI, elevated, from the application checkout of the release being
promoted, while the pipeline is stopped: for example in a second elevated window while
Promote-ContentRelease.ps1 waits for "ready <commit>". It changes nothing unless every
prerequisite holds, and is safe to run again (it only replaces what differs).

Prerequisites, checked first (lab plays 50, 51 and 54 provide them; this script never changes
identities or ACLs):
- LAB\rr-deploy$ exists, is not a local administrator and holds the batch logon right;
- it can read the runtime root, tools and bin, and git; it can change logs\Deploy, staging,
  control and the application tree; it can only read secrets\Deploy; it is denied the .env;
- the pipeline and backup tasks are not running, and the pipeline's stop file is in place.

Then it:
1. backs up every installed file it is about to replace into rollback\deployer-install-<UTC>;
2. installs the hold-aware launcher (tools\Start-ContentVmWorkload.ps1 and its libraries);
3. installs the deployer, the pinned signer list and GitHub's host keys into tools\deploy,
   which the deployer can read but not change;
4. writes deploy.json (git, ssh, repository, signer, check interval);
5. creates the read-only deploy key secrets\Deploy\github_ed25519 if it does not exist and prints
   its public half (the private half is never printed);
6. registers RelicAndRiddle-ReleaseDeployer-Unattended as LAB\rr-deploy$, every minute and at boot.

It does not start the deployer, change the pipeline or backup tasks, read .env, or touch the
database or the backup. Add the printed public key to GitHub as a read-only deploy key, then
finish the promotion; the deployer's first run reports in logs\Deploy\status.json.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Repository = 'git@github.com:kolade-ogunleye/relic-and-riddle.git',
    [string]$Signer = 'kolade-ogunleye',
    [ValidateRange(1, 60)][int]$CheckMinutes = 2,
    # MinGit, the operator's per-user winget package (lab play 54, content_git_base).
    [string]$Git = 'C:\Users\omokolade.LAB\AppData\Local\Microsoft\WinGet\Packages\Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe\cmd\git.exe'
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'The deployer installation is restricted to CONTENTCLIENTWI.' }
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

. (Join-Path $PSScriptRoot 'lib/ContentReleaseDeployer.ps1')   # Invoke-ContentNative
. (Join-Path $PSScriptRoot 'lib/ContentDeployerInstall.ps1')

$root = 'C:\ProgramData\HNSHL\ContentRuntime'
$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$keygen = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh-keygen.exe'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
function Write-Step([string]$Text) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Text) }

$launcherFiles = @{
    'Start-ContentVmWorkload.ps1'    = 'runtime\Start-ContentVmWorkload.ps1'
    'content_runtime.py'             = 'runtime\content_runtime.py'
    'lib\ContentProbeProcess.ps1'    = 'runtime\lib\ContentProbeProcess.ps1'
    'lib\ContentWorkloadProcess.ps1' = 'runtime\lib\ContentWorkloadProcess.ps1'
    'lib\ContentReleaseHold.ps1'     = 'runtime\lib\ContentReleaseHold.ps1'
}
$deployerFiles = @{
    'Invoke-ContentReleaseDeployer.ps1' = 'Invoke-ContentReleaseDeployer.ps1'
    'lib\ContentReleaseDeployer.ps1'    = 'lib\ContentReleaseDeployer.ps1'
    'allowed_signers'                   = 'release\allowed_signers'
    'github_known_hosts'                = 'release\github_known_hosts'
}

# --- Prerequisites: all checked, all reported, nothing changed before they hold. ----------------
$problems = [Collections.Generic.List[string]]::new()
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
$app = [string]$manifest.app_root
try { $deployer = [Security.Principal.NTAccount]::new('LAB\rr-deploy$').Translate([Security.Principal.SecurityIdentifier]) }
catch { throw 'LAB\rr-deploy$ does not exist on this host yet: apply lab plays 50, 51 and 54 first.' }

$admins = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
if (@(Get-LocalGroupMember -SID $admins | ForEach-Object { $_.SID }) -contains $deployer) {
    $problems.Add('LAB\rr-deploy$ is a local administrator; it must not be.')
}
$rights = Join-Path $env:TEMP "rr-deploy-rights-$stamp.inf"
try {
    & "$env:SystemRoot\System32\secedit.exe" /export /areas USER_RIGHTS /cfg $rights | Out-Null
    $batch = (Get-Content -LiteralPath $rights | Select-String '^SeBatchLogonRight').Line
    if ($batch -notmatch [regex]::Escape("*$($deployer.Value)")) { $problems.Add('LAB\rr-deploy$ lacks the batch logon right (lab play 51).') }
} finally { Remove-Item -LiteralPath $rights -Force -ErrorAction SilentlyContinue }

if (-not (Test-Path -LiteralPath $Git -PathType Leaf)) { $problems.Add("git is not at $Git.") }
foreach ($tool in $ssh, $keygen) { if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { $problems.Add("$tool is missing.") } }
$gitBase = Split-Path (Split-Path $Git -Parent) -Parent
$expectations = @(
    @($root, 'ReadAndExecute', 'the runtime root (play 51)'),
    @((Join-Path $root 'tools'), 'ReadAndExecute', 'tools (play 51)'),
    @((Join-Path $root 'bin'), 'ReadAndExecute', 'bin (play 51)'),
    @($gitBase, 'ReadAndExecute', 'git (play 54)'),
    @($app, 'Modify', 'the application tree (play 54)'),
    @((Join-Path $root 'control'), 'Modify', 'control (play 54)'),
    @((Join-Path $root 'logs\Pipeline'), 'ReadAndExecute', 'logs\Pipeline (play 54)'),
    @((Join-Path $root 'logs\Deploy'), 'Modify', 'logs\Deploy (play 54)'),
    @((Join-Path $root 'staging'), 'Modify', 'staging (play 54)'),
    @((Join-Path $root 'secrets\Deploy'), 'Read', 'secrets\Deploy (play 54)')
)
foreach ($e in $expectations) {
    if (-not (Test-ContentAclAllows -Path $e[0] -Sid $deployer -Rights $e[1])) { $problems.Add("LAB\rr-deploy$ lacks $($e[1]) on $($e[2]): $($e[0])") }
}
if (Test-ContentAclAllows -Path (Join-Path $root 'secrets\Deploy') -Sid $deployer -Rights 'WriteData') {
    $problems.Add('LAB\rr-deploy$ can write secrets\Deploy; it must only read its key.')
}
if (-not (Test-ContentAclDenies -Path (Join-Path $app '.env') -Sid $deployer -Rights 'Read')) {
    $problems.Add('LAB\rr-deploy$ is not denied the application .env (play 54).')
}
$signers = Get-ContentSignerPrincipal -Path (Join-Path $PSScriptRoot 'release\allowed_signers')
if ($signers -notcontains $Signer) { $problems.Add("The pinned signer list has no principal '$Signer'.") }

$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
foreach ($name in 'RelicAndRiddle-Production-Unattended', 'RelicAndRiddle-ProductionBackup-Unattended') {
    $t = @($folder.GetTasks(1) | Where-Object Name -eq $name)
    if ($t.Count -and $t[0].State -eq 4) { $problems.Add("$name is running; install while the pipeline and backup are stopped.") }
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'control\Pipeline.stop'))) {
    $problems.Add('The pipeline stop file is not in place; run this during a promotion (Promote-ContentRelease.ps1).')
}
$settings = New-ContentDeployerSettings -Git $Git -Ssh $ssh -Repository $Repository -Signer $Signer -CheckMinutes $CheckMinutes
$spec = Get-ContentDeployerTaskSpec -Root $root -PowerShell $powershell
$existing = @($folder.GetTasks(1) | Where-Object Name -eq $spec.Name)
if ($existing.Count) {
    $stored = [string]$existing[0].Definition.Principal.UserId
    $storedSid = try {
        if ($stored -match '^S-1-') { [Security.Principal.SecurityIdentifier]::new($stored) }
        else { [Security.Principal.NTAccount]::new($stored).Translate([Security.Principal.SecurityIdentifier]) }
    } catch { $null }
    if ($storedSid -ne $deployer) { $problems.Add("$($spec.Name) exists with another identity ($stored); not overwritten.") }
    if ($existing[0].State -eq 4) { $problems.Add("$($spec.Name) is running; wait for it to finish.") }
}
if ($problems.Count) {
    throw ("Nothing was changed. Fix these first:`n - " + ($problems -join "`n - "))
}
Write-Step 'Prerequisites hold: identity, rights, ACLs, signer, and a stopped pipeline.'

# --- 1-3. Back up what changes, then install the launcher and the deployer. --------------------
$plans = @(
    @{ Label = 'launcher'; Root = (Join-Path $root 'tools'); Plan = @(Get-ContentInstallPlan -SourceRoot $PSScriptRoot -DestinationRoot (Join-Path $root 'tools') -Files $launcherFiles) }
    @{ Label = 'deployer'; Root = (Join-Path $root 'tools\deploy'); Plan = @(Get-ContentInstallPlan -SourceRoot $PSScriptRoot -DestinationRoot (Join-Path $root 'tools\deploy') -Files $deployerFiles) }
)
$backup = Join-Path $root "rollback\deployer-install-$stamp"
foreach ($set in $plans) {
    foreach ($file in @($set.Plan | Where-Object Changed)) {
        if ($file.Exists -and $PSCmdlet.ShouldProcess($file.Target, 'Back up before replacing')) {
            $copy = Join-Path $backup (Join-Path $set.Label $file.Destination)
            New-Item -ItemType Directory -Path (Split-Path $copy -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $file.Target -Destination $copy -Force
        }
        if ($PSCmdlet.ShouldProcess($file.Target, "Install the $($set.Label) file")) {
            New-Item -ItemType Directory -Path (Split-Path $file.Target -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $file.Source -Destination $file.Target -Force
        }
    }
    $changed = @($set.Plan | Where-Object Changed).Count
    Write-Step "$($set.Label): $changed of $(@($set.Plan).Count) files installed or replaced$(if ($changed -and (Test-Path $backup)) { "; previous copies in $backup" })."
}
$deployDir = Join-Path $root 'tools\deploy'
if (-not $WhatIfPreference) {
    if (-not (Test-ContentAclAllows -Path $deployDir -Sid $deployer -Rights 'ReadAndExecute')) { throw "LAB\rr-deploy$ cannot read $deployDir; check the tools ACL (play 51)." }
    if (Test-ContentAclAllows -Path $deployDir -Sid $deployer -Rights 'WriteData') { throw "LAB\rr-deploy$ can change its own installed copy in $deployDir; refusing to continue." }
}

# --- 4. deploy.json ------------------------------------------------------------------------------
$settingsPath = Join-Path $root 'deploy.json'
$json = $settings | ConvertTo-Json -Depth 3
$current = if (Test-Path -LiteralPath $settingsPath) { Get-Content -LiteralPath $settingsPath -Raw } else { '' }
if ($current.Trim() -ne $json.Trim() -and $PSCmdlet.ShouldProcess($settingsPath, 'Write the deployer settings')) {
    if ($current) { New-Item -ItemType Directory -Path $backup -Force | Out-Null; Copy-Item -LiteralPath $settingsPath -Destination (Join-Path $backup 'deploy.json') -Force }
    Set-Content -LiteralPath $settingsPath -Value $json -Encoding UTF8
    Write-Step "Settings written: $settingsPath"
}

# --- 5. Read-only deploy key ---------------------------------------------------------------------
$key = Join-Path $root 'secrets\Deploy\github_ed25519'
if (-not (Test-Path -LiteralPath $key) -and $PSCmdlet.ShouldProcess($key, 'Generate the read-only deploy key')) {
    $made = Invoke-ContentNative -FilePath $keygen -ArgumentList @('-q', '-t', 'ed25519', '-N', '', '-C', 'rr-deploy@CONTENTCLIENTWI read-only', '-f', $key)
    if ($made.ExitCode -ne 0) { throw "ssh-keygen failed (exit $($made.ExitCode)): $($made.Stderr.Trim())" }
    Write-Step 'Deploy key generated.'
}
# Windows ssh-keygen gives a new private key an explicit ACL for its creator only, which hid it from
# the deployer: its first fetch failed with "Load key ...: Permission denied" (2026-10-23). The key
# goes back to the folder's lab-defined permissions (SYSTEM, Administrators, the deployer read).
if ((Test-Path -LiteralPath $key) -and -not (Test-ContentFileInherits -Path $key) -and
    $PSCmdlet.ShouldProcess($key, 'Give the deploy key its folder''s permissions')) {
    Reset-ContentFileInheritance -Path $key
    Write-Step 'Deploy key now has its folder''s permissions (the deployer can read it).'
}
if ((Test-Path -LiteralPath $key) -and -not (Test-ContentKeyOwner -Path $key) -and
    $PSCmdlet.ShouldProcess($key, 'Give the deploy key to the Administrators group')) {
    Set-ContentKeyOwner -Path $key   # OpenSSH rejects a key owned by another user account
    Write-Step 'Deploy key owned by Administrators (Windows OpenSSH requires it).'
}
if ((Test-Path -LiteralPath $key) -and -not (Test-ContentAclAllows -Path $key -Sid $deployer -Rights 'Read')) {
    throw "LAB\rr-deploy$ still cannot read $key; check the secrets\Deploy ACL (lab play 54)."
}
if (Test-Path -LiteralPath "$key.pub") {
    $fingerprint = (Invoke-ContentNative -FilePath $keygen -ArgumentList @('-l', '-f', "$key.pub")).Stdout.Trim()
    Write-Step "Deploy key: $fingerprint"
    Write-Host '  Add this public key to GitHub as a READ-ONLY deploy key (repository Settings > Deploy keys; leave "Allow write access" off):'
    Write-Host ('  ' + (Get-Content -LiteralPath "$key.pub" -Raw).Trim())
}

# --- 6. Scheduled task ---------------------------------------------------------------------------
$registered = $existing.Count -and (Test-ContentDeployerTaskMatches -Definition $existing[0].Definition -Spec $spec)
if ($registered) { Write-Step "$($spec.Name) is already registered as specified." }
elseif ($PSCmdlet.ShouldProcess($spec.Name, 'Register the release deployer task')) {
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = $spec.Description
    $definition.Principal.UserId = $spec.Identity
    $definition.Principal.LogonType = 1
    $definition.Principal.RunLevel = 0
    $definition.Settings.Enabled = $true
    $definition.Settings.StartWhenAvailable = $true
    $definition.Settings.ExecutionTimeLimit = $spec.ExecutionTimeLimit
    $definition.Settings.MultipleInstances = $spec.MultipleInstances
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $boot = $definition.Triggers.Create(8)
    $boot.Delay = $spec.BootDelay
    $boot.Enabled = $true
    $repeat = $definition.Triggers.Create(1)
    $repeat.StartBoundary = (Get-Date).AddMinutes(1).ToString('s')
    $repeat.Repetition.Interval = $spec.RepeatInterval
    $repeat.Enabled = $true
    $action = $definition.Actions.Create(0)
    $action.Path = $spec.Executable
    $action.Arguments = $spec.Arguments
    $action.WorkingDirectory = $spec.WorkingDirectory
    $folder.RegisterTaskDefinition($spec.Name, $definition, 6, $spec.Identity, $null, 1) | Out-Null
    Write-Step "$($spec.Name) registered as $($spec.Identity) (every minute and at boot). It waits while the stop file is in place."
}

Write-Step 'Installed. Next: add the deploy key on GitHub (read-only), then finish the promotion. The deployer reports in logs\Deploy\status.json and /deploy.'
