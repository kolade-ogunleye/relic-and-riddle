#Requires -Version 5.1
<#
.SYNOPSIS
Registers disabled content API canaries or unattended tasks; never starts them.
.DESCRIPTION
Production registration is a separate explicit stage after successful canaries.
The existing RelicAndRiddle tasks are never modified by this tool.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Canary', 'Production')][string]$Stage = 'Canary',
    [switch]$EnableProduction
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Workload registration is restricted to CONTENTCLIENTWI.' }
$launcher = Join-Path $PSScriptRoot 'Start-ContentVmWorkload.ps1'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Managed workload launcher is missing.' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
if ($Stage -eq 'Canary') {
    $production = @($folder.GetTasks(1) | Where-Object Name -in @('RelicAndRiddle-Production-Unattended', 'RelicAndRiddle-ProductionBackup-Unattended'))
    if (@($production | Where-Object { $_.Enabled -or $_.State -eq 4 }).Count -gt 0) {
        throw 'Production tasks are enabled or running; complete the controlled rollback before returning to canaries.'
    }
}
foreach ($workload in 'Pipeline', 'Backup') {
    $prefix = if ($workload -eq 'Pipeline') { 'RelicAndRiddle-Production' } else { 'RelicAndRiddle-ProductionBackup' }
    $name = if ($Stage -eq 'Canary') { "$prefix-UnattendedCanary" } else { "$prefix-Unattended" }
    $identity = if ($workload -eq 'Pipeline') { 'LAB\rr-pipeline$' } else { 'LAB\rr-backup$' }
    $mode = if ($Stage -eq 'Canary') { 'Canary' } else { 'Run' }
    $enabled = $Stage -eq 'Production' -and [bool]$EnableProduction
    $description = "HNSHL managed $workload $Stage v1; pipeline remains dry-run; original task retained"
    $executable = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$launcher`" -Workload $workload -Mode $mode"
    $timeLimit = if ($Stage -eq 'Canary') { 'PT5M' } elseif ($workload -eq 'Pipeline') { 'PT0S' } else { 'PT2H' }
    $triggerCount = if ($Stage -eq 'Canary') { 0 } elseif ($workload -eq 'Pipeline') { 1 } else { 2 }
    $existing = @($folder.GetTasks(1) | Where-Object Name -eq $name)
    if ($existing.Count -gt 0) {
        $task = $existing[0]
        $stored = [string]$task.Definition.Principal.UserId
        if ($stored -ine $identity) {
            try {
                $expectedSid = [Security.Principal.NTAccount]::new($identity).Translate([Security.Principal.SecurityIdentifier]).Value
                $actualSid = if ($stored -match '^S-1-') { [Security.Principal.SecurityIdentifier]::new($stored).Value }
                else { [Security.Principal.NTAccount]::new($stored).Translate([Security.Principal.SecurityIdentifier]).Value }
            } catch { throw "Workload $name has an unexpected identity that could not be resolved; not overwritten." }
            if ($actualSid -ne $expectedSid) { throw "Workload $name has an unexpected identity; not overwritten." }
        }
        $existingDefinition = $task.Definition
        $triggers = @($existingDefinition.Triggers)
        $triggerMatch = $existingDefinition.Triggers.Count -eq $triggerCount
        $restartMatch = $Stage -eq 'Canary' -or ($existingDefinition.Settings.RestartCount -eq 3 -and
            $existingDefinition.Settings.RestartInterval -eq 'PT1M')
        if ($Stage -eq 'Production') {
            $triggerMatch = $triggerMatch -and @($triggers | Where-Object { $_.Type -eq 8 -and $_.Delay -eq 'PT1M' -and $_.Enabled }).Count -eq 1
            if ($workload -eq 'Backup') {
                $triggerMatch = $triggerMatch -and @($triggers | Where-Object { $_.Type -eq 2 -and $_.DaysInterval -eq 1 -and $_.Repetition.Interval -eq 'PT10M' -and $_.Enabled }).Count -eq 1
            }
        }
        if ($existingDefinition.RegistrationInfo.Description -ceq $description -and
            $existingDefinition.Principal.LogonType -eq 1 -and $existingDefinition.Principal.RunLevel -eq 0 -and
            $existingDefinition.Actions.Count -eq 1 -and $existingDefinition.Actions.Item(1).Path -ieq $executable -and
            $existingDefinition.Actions.Item(1).Arguments -ceq $arguments -and $triggerMatch -and $restartMatch -and
            $existingDefinition.Settings.ExecutionTimeLimit -eq $timeLimit -and
            $existingDefinition.Settings.MultipleInstances -eq 2 -and
            $existingDefinition.Settings.StartWhenAvailable -and $task.Enabled -eq $enabled) {
            [pscustomobject]@{ Task = $name; Changed = $false; Enabled = $enabled }
            continue
        }
        if ($task.State -eq 4) { throw "Workload $name is running with configuration drift; stop it before deployment." }
    }
    if ($PSCmdlet.ShouldProcess($name, 'Register disabled managed content workload')) {
        $definition = $scheduler.NewTask(0)
        $definition.RegistrationInfo.Description = $description
        $definition.Principal.UserId = $identity
        $definition.Principal.LogonType = 1
        $definition.Principal.RunLevel = 0
        $definition.Settings.Enabled = $enabled
        $definition.Settings.StartWhenAvailable = $true
        $definition.Settings.ExecutionTimeLimit = $timeLimit
        $definition.Settings.MultipleInstances = 2
        $definition.Settings.DisallowStartIfOnBatteries = $false
        $definition.Settings.StopIfGoingOnBatteries = $false
        if ($Stage -eq 'Production') {
            $definition.Settings.RestartCount = 3
            $definition.Settings.RestartInterval = 'PT1M'
            $boot = $definition.Triggers.Create(8)
            $boot.Delay = 'PT1M'
            $boot.Enabled = $true
            if ($workload -eq 'Backup') {
                $daily = $definition.Triggers.Create(2)
                $daily.StartBoundary = (Get-Date).Date.ToString('s')
                $daily.DaysInterval = 1
                $daily.Repetition.Interval = 'PT10M'
                $daily.Repetition.Duration = 'P1D'
                $daily.Enabled = $true
            }
        }
        $action = $definition.Actions.Create(0)
        $action.Path = $executable
        $action.Arguments = $arguments
        $action.WorkingDirectory = Split-Path $PSScriptRoot -Parent
        $folder.RegisterTaskDefinition($name, $definition, 6, $identity, $null, 1) | Out-Null
        [pscustomobject]@{ Task = $name; Changed = $true; Enabled = $enabled }
    }
}
