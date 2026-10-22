BeforeAll {
    $script:legacyStopPath = Join-Path $PSScriptRoot '../../../tools/Stop-ContentVmLegacyTasks.ps1'
}

Describe 'Content legacy idle cutover' {
    BeforeEach {
        $script:savedComputer = $env:COMPUTERNAME
        $env:COMPUTERNAME = 'CONTENTCLIENTWI'
        $script:legacyTasks = @(
            [pscustomobject]@{ TaskName = 'RelicAndRiddle-Production'; State = 'Ready' },
            [pscustomobject]@{ TaskName = 'RelicAndRiddle-ProductionBackup'; State = 'Ready' }
        )
        Mock Get-Content { '{"app_root":"C:\fixture"}' } -ParameterFilter { $LiteralPath -like '*launch.json' }
        Mock Get-Content { '{"pid":123,"started":"2026-10-21T00:00:00Z"}' } -ParameterFilter { $LiteralPath -like '*runtime-Production.pid.json' }
        $legacyFixture = $script:legacyTasks
        Mock Get-ScheduledTask { $legacyFixture }
        Mock Get-Process { $null }
        Mock Disable-ScheduledTask { }
    }
    AfterEach { $env:COMPUTERNAME = $script:savedComputer }

    It 'inspects by default without disabling retained schedules' {
        $result = & $script:legacyStopPath
        $result.Applied | Should -BeFalse
        $result.WorkerPresent | Should -BeFalse
        Should -Invoke Disable-ScheduledTask -Times 0 -Exactly
    }
    It 'refuses a running backup before changing any schedule' {
        $script:legacyTasks[1].State = 'Running'
        { & $script:legacyStopPath -Apply } | Should -Throw '*backup is running*'
        Should -Invoke Disable-ScheduledTask -Times 0 -Exactly
    }
    It 'refuses an inconsistent running launcher without a matching worker' {
        $script:legacyTasks[0].State = 'Running'
        { & $script:legacyStopPath -Apply } | Should -Throw '*no matching worker*'
        Should -Invoke Disable-ScheduledTask -Times 0 -Exactly
    }
    It 'can disable already stopped retained tasks without running any application provider' {
        $result = & $script:legacyStopPath -Apply
        $result.Applied | Should -BeTrue
        Should -Invoke Disable-ScheduledTask -Times 2 -Exactly
    }
    It 'refuses a foreign computer before reading any manifest' {
        $env:COMPUTERNAME = 'OTHER'
        { & $script:legacyStopPath -Apply } | Should -Throw '*CONTENTCLIENTWI*'
        Should -Invoke Get-Content -Times 0 -Exactly
    }
}
