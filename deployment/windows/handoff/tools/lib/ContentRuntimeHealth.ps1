$ErrorActionPreference = 'Stop'

function Get-ContentRuntimeHealth {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateSet('Pipeline', 'Backup')][string]$Workload,
        [Parameter(Mandatory)][string]$TaskState,
        [psobject]$Receipt,
        [bool]$WorkerMatches = $false,
        [bool]$PauseRequested = $false,
        [datetime]$LastRunUtc = [datetime]::MinValue,
        [datetime]$NowUtc = [datetime]::UtcNow
    )
    $healthy = $false
    $reason = 'MissingOrFailedWorkload'
    $age = $NowUtc - $LastRunUtc
    if ($PauseRequested) { $healthy = $true; $reason = 'OperatorPaused' }
    elseif ($TaskState -eq 'Running' -and $age.TotalSeconds -ge 0 -and $age.TotalMinutes -le 5) {
        $healthy = $true; $reason = 'Starting'
    } elseif ($Workload -eq 'Pipeline') {
        $healthy = $TaskState -eq 'Running' -and $null -ne $Receipt -and
            $Receipt.Mode -eq 'Run' -and -not $Receipt.Validation -and -not $Receipt.Completed -and $WorkerMatches
        if ($healthy) { $reason = 'Running' }
    } elseif ($TaskState -eq 'Running' -and $age.TotalSeconds -ge 0 -and $age.TotalHours -le 2) {
        $healthy = $true; $reason = 'BackupInProgress'
    } elseif ($TaskState -ne 'Disabled' -and $null -ne $Receipt -and $Receipt.Mode -eq 'Run' -and
              -not $Receipt.Validation -and $Receipt.Completed -and $null -ne $Receipt.ExitCode -and
              $Receipt.ExitCode -eq 0 -and $Receipt.CompletedAtUtc) {
        $completed = ([datetime]$Receipt.CompletedAtUtc).ToUniversalTime()
        $healthy = $completed -le $NowUtc.AddMinutes(5) -and $completed -ge $NowUtc.AddMinutes(-45)
        if ($healthy) { $reason = 'RecentVerifiedCycle' }
    }
    [pscustomobject]@{ Workload = $Workload; Healthy = [bool]$healthy; Reason = $reason }
}
