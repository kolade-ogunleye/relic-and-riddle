BeforeAll {
    $script:probeRegistrationPath = Join-Path $PSScriptRoot '../../../tools/Register-ContentVmIdentityProbes.ps1'
    $script:gpuProbePath = Join-Path $PSScriptRoot '../../../tools/Test-ContentVmBackgroundGpu.ps1'
    $script:accountInstallPath = Join-Path $PSScriptRoot '../../../tools/Install-ContentVmManagedAccounts.ps1'
    $script:workloadRegistrationPath = Join-Path $PSScriptRoot '../../../tools/Register-ContentVmWorkloads.ps1'
    $script:workloadLauncherPath = Join-Path $PSScriptRoot '../../../tools/Start-ContentVmWorkload.ps1'
    $script:backupValidationPath = Join-Path $PSScriptRoot '../../../tools/Register-ContentVmBackupValidation.ps1'
    $script:watchRegistrationPath = Join-Path $PSScriptRoot '../../../tools/Register-ContentVmRuntimeWatch.ps1'
}

Describe 'Content identity probe task registration' {
    BeforeEach {
        $script:originalComputerName = $env:COMPUTERNAME
        $env:COMPUTERNAME = 'CONTENTCLIENTWI'
        $script:probeFolderFixture = [pscustomobject]@{ Tasks = @(); Writes = 0 }
        $script:probeFolderFixture | Add-Member ScriptMethod GetTasks { param($flags) $this.Tasks }
        $script:probeFolderFixture | Add-Member ScriptMethod RegisterTaskDefinition {
            param($name, $definition, $flags, $identity, $password, $logonType)
            $safePrincipal = ($logonType -eq 1 -and $definition.Principal.RunLevel -eq 0) -or
                ($logonType -eq 5 -and $identity -eq 'SYSTEM' -and $definition.Principal.RunLevel -eq 1)
            if ($null -ne $password -or -not $safePrincipal -or $definition.Settings.Enabled) { throw 'Unsafe probe registration' }
            $this.Writes++
            $this.Tasks += [pscustomobject]@{ Name = $name; Definition = $definition; State = 3; Enabled = $false }
        }
        $schedulerFixture = [pscustomobject]@{ Folder = $script:probeFolderFixture }
        $schedulerFixture | Add-Member ScriptMethod Connect { }
        $schedulerFixture | Add-Member ScriptMethod GetFolder { param($path) $this.Folder }
        $schedulerFixture | Add-Member ScriptMethod NewTask {
            param($flags)
            $actions = [pscustomobject]@{ Count = 0; Entry = $null }
            $actions | Add-Member ScriptMethod Create {
                param($type)
                $this.Count = 1
                $this.Entry = [pscustomobject]@{ Path = ''; Arguments = ''; WorkingDirectory = '' }
                return $this.Entry
            }
            $actions | Add-Member ScriptMethod Item { param($index) $this.Entry }
            $triggers = [System.Collections.ArrayList]::new()
            Add-Member -InputObject $triggers -MemberType ScriptMethod -Name Create -Value {
                param($type)
                $trigger = [pscustomobject]@{ Type = $type; Delay = ''; Enabled = $false; StartBoundary = ''; DaysInterval = 0; Repetition = [pscustomobject]@{ Interval = ''; Duration = '' } }
                $null = $this.Add($trigger)
                return $trigger
            }
            [pscustomobject]@{
                RegistrationInfo = [pscustomobject]@{ Description = '' }
                Principal = [pscustomobject]@{ UserId = ''; LogonType = 0; RunLevel = 0 }
                Settings = [pscustomobject]@{ Enabled = $true; ExecutionTimeLimit = ''; MultipleInstances = 0; DisallowStartIfOnBatteries = $true; StopIfGoingOnBatteries = $true; StartWhenAvailable = $false; RestartCount = 0; RestartInterval = '' }
                Triggers = $triggers
                Actions = $actions
            }
        }
        Mock New-Object { $schedulerFixture } -ParameterFilter { $ComObject -eq 'Schedule.Service' }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -like '*Test-ContentVmBackgroundGpu.ps1' }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -like '*Start-ContentVmWorkload.ps1' }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -like '*Watch-ContentVmRuntime.ps1' }
    }

    AfterEach { $env:COMPUTERNAME = $script:originalComputerName }

    It 'registers two disabled, triggerless, limited gMSA probes with no saved password' {
        $result = @(& $script:probeRegistrationPath)
        $result.Count | Should -Be 2
        $script:probeFolderFixture.Writes | Should -Be 2
        foreach ($task in $script:probeFolderFixture.Tasks) {
            $task.Definition.Principal.RunLevel | Should -Be 0
            $task.Definition.Triggers.Count | Should -Be 0
            $task.Definition.Settings.Enabled | Should -BeFalse
            $task.Definition.Settings.ExecutionTimeLimit | Should -Be 'PT2M'
            $task.Name | Should -Not -BeLike 'RelicAndRiddle-*'
        }
    }

    It 'does not rewrite matching disabled probes' {
        $null = & $script:probeRegistrationPath
        $result = @(& $script:probeRegistrationPath)
        @($result | Where-Object Changed).Count | Should -Be 0
        $script:probeFolderFixture.Writes | Should -Be 2
    }

    It 'refuses to replace a probe owned by another identity' {
        $null = & $script:probeRegistrationPath
        $script:probeFolderFixture.Tasks[0].Definition.Principal.UserId = 'LAB\operator'
        { & $script:probeRegistrationPath } | Should -Throw '*unexpected identity*'
        $script:probeFolderFixture.Writes | Should -Be 2
    }

    It 'does not register tasks in WhatIf mode' {
        $null = & $script:probeRegistrationPath -WhatIf
        $script:probeFolderFixture.Writes | Should -Be 0
    }

    It 'rejects a foreign computer before opening Task Scheduler' {
        $env:COMPUTERNAME = 'OTHER'
        { & $script:probeRegistrationPath } | Should -Throw '*CONTENTCLIENTWI*'
        Should -Invoke New-Object -Times 0 -Exactly -ParameterFilter { $ComObject -eq 'Schedule.Service' }
    }

    It 'refuses a GPU probe under the operator identity before writing evidence' {
        { & $script:gpuProbePath -Workload Pipeline } | Should -Throw '*identity mismatch*'
    }

    It 'refuses account installation on a foreign computer' {
        $env:COMPUTERNAME = 'OTHER'
        { & $script:accountInstallPath } | Should -Throw '*CONTENTCLIENTWI*'
    }

    It 'registers API canaries disabled with no triggers and keeps registration idempotent' {
        $result = @(& $script:workloadRegistrationPath)
        $result.Count | Should -Be 2
        foreach ($task in $script:probeFolderFixture.Tasks) {
            $task.Name | Should -BeLike '*-UnattendedCanary'
            $task.Definition.Triggers.Count | Should -Be 0
            $task.Enabled | Should -BeFalse
            $task.Definition.Actions.Item(1).Arguments | Should -BeLike '*-Mode Canary'
        }
        $again = @(& $script:workloadRegistrationPath)
        @($again | Where-Object Changed).Count | Should -Be 0
        $script:probeFolderFixture.Writes | Should -Be 2
    }

    It 'registers production definitions with boot triggers and backup retry scheduling, still disabled by default' {
        $result = @(& $script:workloadRegistrationPath -Stage Production)
        $result.Count | Should -Be 2
        $pipeline = $script:probeFolderFixture.Tasks | Where-Object Name -eq 'RelicAndRiddle-Production-Unattended'
        $backup = $script:probeFolderFixture.Tasks | Where-Object Name -eq 'RelicAndRiddle-ProductionBackup-Unattended'
        $pipeline.Definition.Triggers.Count | Should -Be 1
        $pipeline.Definition.Triggers[0].Type | Should -Be 8
        $pipeline.Definition.Triggers[0].Delay | Should -Be 'PT1M'
        $backup.Definition.Triggers.Count | Should -Be 2
        $backup.Definition.Triggers[1].Repetition.Interval | Should -Be 'PT10M'
        $pipeline.Enabled | Should -BeFalse
        $backup.Enabled | Should -BeFalse
    }

    It 'does not touch tasks when workload registration is a dry run' {
        $null = & $script:workloadRegistrationPath -WhatIf
        $script:probeFolderFixture.Writes | Should -Be 0
    }

    It 'refuses the workload launcher under a different identity before reading its manifest' {
        { & $script:workloadLauncherPath -Workload Pipeline } | Should -Throw '*identity mismatch*'
    }

    It 'registers a unique disabled backup validation with no triggers' {
        $result = & $script:backupValidationPath -Operation Cycle
        $result.TaskName | Should -BeLike 'HNSHL-Content-Backup-Validation-*'
        $task = $script:probeFolderFixture.Tasks[0]
        $task.Definition.Principal.UserId | Should -Be 'LAB\rr-backup$'
        $task.Definition.Triggers.Count | Should -Be 0
        $task.Enabled | Should -BeFalse
        $task.Definition.Actions.Item(1).Arguments | Should -BeLike '*-Mode Run -Validation'
    }

    It 'rejects a restore path traversal before opening Task Scheduler' {
        { & $script:backupValidationPath -Operation Restore -SnapshotId '../../other' } | Should -Throw '*snapshot identity*'
        $script:probeFolderFixture.Writes | Should -Be 0
    }

    It 'registers a disabled SYSTEM watchdog with boot and periodic triggers, idempotently' {
        $null = & $script:watchRegistrationPath
        $task = $script:probeFolderFixture.Tasks[0]
        $task.Definition.Principal.UserId | Should -Be 'SYSTEM'
        $task.Definition.Principal.LogonType | Should -Be 5
        $task.Definition.Triggers.Count | Should -Be 2
        $task.Definition.Triggers[1].Repetition.Interval | Should -Be 'PT15M'
        $task.Enabled | Should -BeFalse
        $task.Definition.Principal.UserId = 'S-1-5-18'
        $again = & $script:watchRegistrationPath
        $again.Changed | Should -BeFalse
        $script:probeFolderFixture.Writes | Should -Be 1
    }

    It 'refuses to return to canaries while production is enabled' {
        $null = & $script:workloadRegistrationPath -Stage Production
        $script:probeFolderFixture.Tasks[0].Enabled = $true
        { & $script:workloadRegistrationPath -Stage Canary } | Should -Throw '*controlled rollback*'
        $script:probeFolderFixture.Writes | Should -Be 2
    }
}
