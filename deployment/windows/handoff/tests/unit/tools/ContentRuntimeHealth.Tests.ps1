BeforeAll {
    . (Join-Path $PSScriptRoot '../../../tools/lib/ContentRuntimeHealth.ps1')
}

Describe 'Independent content runtime health' {
    BeforeEach {
        $script:now = [datetime]::UtcNow
        $script:receipt = [pscustomobject]@{ Mode = 'Run'; Validation = $false; Completed = $true; ExitCode = 0; CompletedAtUtc = $script:now.ToString('o') }
    }

    It 'requires an actual matching running pipeline worker' {
        $script:receipt.Completed = $false
        (Get-ContentRuntimeHealth -Workload Pipeline -TaskState Running -Receipt $script:receipt -WorkerMatches $true).Healthy | Should -BeTrue
        (Get-ContentRuntimeHealth -Workload Pipeline -TaskState Running -Receipt $script:receipt -WorkerMatches $false).Healthy | Should -BeFalse
    }

    It 'does not mistake a validation receipt for a production backup' {
        $script:receipt.Validation = $true
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Ready -Receipt $script:receipt).Healthy | Should -BeFalse
    }

    It 'accepts a recent successful verified backup cycle' {
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Ready -Receipt $script:receipt).Healthy | Should -BeTrue
    }

    It 'rejects stale, missing and null-exit-code backup receipts' {
        $script:receipt.CompletedAtUtc = $script:now.AddHours(-2).ToString('o')
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Ready -Receipt $script:receipt).Healthy | Should -BeFalse
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Ready).Healthy | Should -BeFalse
        $script:receipt.CompletedAtUtc = $script:now.ToString('o')
        $script:receipt.ExitCode = $null
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Ready -Receipt $script:receipt).Healthy | Should -BeFalse
    }

    It 'allows bounded startup and upload time but rejects a stuck backup' {
        (Get-ContentRuntimeHealth -Workload Pipeline -TaskState Running -LastRunUtc $script:now.AddMinutes(-2)).Healthy | Should -BeTrue
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Running -LastRunUtc $script:now.AddMinutes(-60)).Healthy | Should -BeTrue
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Running -LastRunUtc $script:now.AddHours(-3)).Healthy | Should -BeFalse
    }

    It 'alerts on disabled production tasks unless explicitly paused by the operator' {
        (Get-ContentRuntimeHealth -Workload Backup -TaskState Disabled -Receipt $script:receipt).Healthy | Should -BeFalse
        (Get-ContentRuntimeHealth -Workload Pipeline -TaskState Disabled -PauseRequested $true).Reason | Should -Be 'OperatorPaused'
    }
}
