#Requires -Version 5.1
<#
.SYNOPSIS
Creates a disabled one-time backup cycle or isolated cloud restore validation task.
.DESCRIPTION
Returns a unique task name. Operator explicitly starts it, inspects its receipt,
then removes the validation task. It does not alter production task schedules.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Cycle', 'Restore')][string]$Operation = 'Cycle',
    [string]$SnapshotId = ''
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Backup validation is restricted to CONTENTCLIENTWI.' }
if ($Operation -eq 'Restore' -and $SnapshotId -notmatch '^production-\d{8}T\d{6}Z-[a-f0-9]{12}$') {
    throw 'Cloud restore validation requires a valid production snapshot identity.'
}
if ($Operation -eq 'Cycle' -and $SnapshotId) { throw 'A cycle validation does not accept a snapshot identity.' }
$launcher = Join-Path $PSScriptRoot 'Start-ContentVmWorkload.ps1'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Workload launcher is missing.' }
$name = 'HNSHL-Content-Backup-Validation-' + [guid]::NewGuid().ToString('N')
if ($PSCmdlet.ShouldProcess($name, "Register disabled backup $Operation validation")) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = "HNSHL one-time backup $Operation validation; no schedule"
    $definition.Principal.UserId = 'LAB\rr-backup$'
    $definition.Principal.LogonType = 1
    $definition.Principal.RunLevel = 0
    $definition.Settings.Enabled = $false
    $definition.Settings.ExecutionTimeLimit = 'PT2H'
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $mode = if ($Operation -eq 'Cycle') { 'Run' } else { 'Restore' }
    $action = $definition.Actions.Create(0)
    $action.Path = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $action.Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$launcher`" -Workload Backup -Mode $mode -Validation"
    if ($Operation -eq 'Restore') { $action.Arguments += " -SnapshotId $SnapshotId" }
    $action.WorkingDirectory = Split-Path $PSScriptRoot -Parent
    # TASK_CREATE only: a collision cannot overwrite an existing definition.
    $scheduler.GetFolder('\').RegisterTaskDefinition($name, $definition, 2, 'LAB\rr-backup$', $null, 1) | Out-Null
    [pscustomobject]@{ TaskName = $name; Operation = $Operation; SnapshotId = $SnapshotId }
}
