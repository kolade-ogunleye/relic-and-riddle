#Requires -Version 5.1
<#
.SYNOPSIS
Declares a SYSTEM runtime watchdog, independent of application credentials.
#>
[CmdletBinding(SupportsShouldProcess)]
param([bool]$Enabled = $false)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Runtime watch registration is restricted to CONTENTCLIENTWI.' }
$name = 'HNSHL-Content-RuntimeWatch'
$path = Join-Path $PSScriptRoot 'Watch-ContentVmRuntime.ps1'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Runtime watch script is missing.' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$existing = @($folder.GetTasks(1) | Where-Object Name -eq $name)
$description = 'HNSHL content process and verified-backup watchdog v1; no app credentials'
$executable = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$path`""
if ($existing.Count -gt 0) {
    $task = $existing[0]
    $definition = $task.Definition
    $triggers = @($definition.Triggers)
    $triggersMatch = $definition.Triggers.Count -eq 2 -and
        @($triggers | Where-Object { $_.Type -eq 8 -and $_.Delay -eq 'PT5M' -and $_.Enabled }).Count -eq 1 -and
        @($triggers | Where-Object { $_.Type -eq 2 -and $_.DaysInterval -eq 1 -and $_.Enabled -and
            $_.Repetition.Interval -eq 'PT15M' -and $_.Repetition.Duration -eq 'P1D' }).Count -eq 1
    $stored = [string]$definition.Principal.UserId
    $sid = if ($stored -match '^S-1-') { [Security.Principal.SecurityIdentifier]::new($stored).Value }
    else { [Security.Principal.NTAccount]::new($stored).Translate([Security.Principal.SecurityIdentifier]).Value }
    if ($sid -ne 'S-1-5-18') { throw 'Watch task has an unexpected principal; not overwritten.' }
    if ($definition.RegistrationInfo.Description -ceq $description -and $definition.Principal.LogonType -eq 5 -and
        $definition.Principal.RunLevel -eq 1 -and $definition.Actions.Count -eq 1 -and
        $definition.Actions.Item(1).Path -ieq $executable -and $definition.Actions.Item(1).Arguments -ceq $arguments -and
        $triggersMatch -and $definition.Settings.StartWhenAvailable -and $definition.Settings.MultipleInstances -eq 2 -and
        $definition.Settings.ExecutionTimeLimit -eq 'PT5M' -and $task.Enabled -eq $Enabled) {
        [pscustomobject]@{ Task = $name; Changed = $false; Enabled = $Enabled }
        return
    }
    if ($task.State -eq 4) { throw 'Runtime watch is running with configuration drift; retry after it finishes.' }
}
if ($PSCmdlet.ShouldProcess($name, 'Register independent content runtime watch')) {
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = $description
    $definition.Principal.UserId = 'SYSTEM'
    $definition.Principal.LogonType = 5
    $definition.Principal.RunLevel = 1
    $definition.Settings.Enabled = $Enabled
    $definition.Settings.StartWhenAvailable = $true
    $definition.Settings.ExecutionTimeLimit = 'PT5M'
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $boot = $definition.Triggers.Create(8)
    $boot.Delay = 'PT5M'
    $boot.Enabled = $true
    $daily = $definition.Triggers.Create(2)
    $daily.StartBoundary = (Get-Date).AddMinutes(15).ToString('s')
    $daily.DaysInterval = 1
    $daily.Repetition.Interval = 'PT15M'
    $daily.Repetition.Duration = 'P1D'
    $daily.Enabled = $true
    $action = $definition.Actions.Create(0)
    $action.Path = $executable
    $action.Arguments = $arguments
    $folder.RegisterTaskDefinition($name, $definition, 6, 'SYSTEM', $null, 5) | Out-Null
    [pscustomobject]@{ Task = $name; Changed = $true; Enabled = $Enabled }
}
