BeforeAll {
    . (Join-Path $PSScriptRoot '../lib/ContentDeployerInstall.ps1')
    $script:installer = (Get-Content -LiteralPath (Join-Path $PSScriptRoot '../Install-ContentReleaseDeployer.ps1') -Raw) -replace "`r`n", "`n"
    $script:me = [Security.Principal.WindowsIdentity]::GetCurrent().User

    function New-Tree([hashtable]$Files) {
        $dir = Join-Path $TestDrive ([guid]::NewGuid().ToString('n'))
        foreach ($name in $Files.Keys) {
            $path = Join-Path $dir $name
            New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
            Set-Content -LiteralPath $path -Value $Files[$name] -NoNewline
        }
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        return $dir
    }
    function New-Definition([string]$Arguments, [int]$Triggers = 2) {
        $spec = Get-ContentDeployerTaskSpec -Root 'C:\R' -PowerShell 'C:\W\powershell.exe'
        $trigger = @(
            [pscustomobject]@{ Type = 8; Delay = 'PT2M'; Enabled = $true; Repetition = [pscustomobject]@{ Interval = ''; Duration = '' } }
            [pscustomobject]@{ Type = 1; Delay = ''; Enabled = $true; Repetition = [pscustomobject]@{ Interval = 'PT1M'; Duration = '' } }
            [pscustomobject]@{ Type = 2; Delay = ''; Enabled = $true; Repetition = [pscustomobject]@{ Interval = ''; Duration = '' } }
        )[0..($Triggers - 1)]
        [pscustomobject]@{
            RegistrationInfo = [pscustomobject]@{ Description = $spec.Description }
            Principal        = [pscustomobject]@{ LogonType = 1; RunLevel = 0 }
            Actions          = @([pscustomobject]@{ Path = $spec.Executable; Arguments = $(if ($Arguments) { $Arguments } else { $spec.Arguments }) })
            Triggers         = $trigger
            Settings         = [pscustomobject]@{ ExecutionTimeLimit = 'PT2H'; MultipleInstances = 2; StartWhenAvailable = $true }
        }
    }
}

Describe 'Install plan' {
    It 'copies missing and changed files and leaves identical ones alone' {
        $source = New-Tree @{ 'a.ps1' = 'new'; 'lib\b.ps1' = 'same'; 'c.txt' = 'fresh' }
        $target = New-Tree @{ 'a.ps1' = 'old'; 'lib\b.ps1' = 'same' }
        $plan = @(Get-ContentInstallPlan -SourceRoot $source -DestinationRoot $target -Files @{ 'a.ps1' = 'a.ps1'; 'lib\b.ps1' = 'lib\b.ps1'; 'c.txt' = 'c.txt' })
        ($plan | Where-Object Destination -eq 'a.ps1').Changed | Should -BeTrue
        ($plan | Where-Object Destination -eq 'a.ps1').Exists | Should -BeTrue
        ($plan | Where-Object Destination -eq 'lib\b.ps1').Changed | Should -BeFalse
        ($plan | Where-Object Destination -eq 'c.txt').Exists | Should -BeFalse
        ($plan | Where-Object Destination -eq 'c.txt').Changed | Should -BeTrue
    }

    It 'refuses a set whose source file is missing' {
        $source = New-Tree @{ 'a.ps1' = 'x' }
        { Get-ContentInstallPlan -SourceRoot $source -DestinationRoot $TestDrive -Files @{ 'gone.ps1' = 'gone.ps1' } } | Should -Throw '*Source file is missing*'
    }

    It 'maps every launcher and deployer file the runtime loads' {
        foreach ($f in "'lib\\ContentReleaseHold.ps1'     = 'runtime\\lib\\ContentReleaseHold.ps1'",
                       "'Invoke-ContentReleaseDeployer.ps1' = 'Invoke-ContentReleaseDeployer.ps1'",
                       "'allowed_signers'                   = 'release\\allowed_signers'",
                       "'github_known_hosts'                = 'release\\github_known_hosts'") {
            $script:installer | Should -Match ([regex]::Escape($f).Replace('\\\\', '\\'))
        }
        foreach ($rel in 'runtime/Start-ContentVmWorkload.ps1', 'runtime/content_runtime.py', 'runtime/lib/ContentProbeProcess.ps1',
                         'runtime/lib/ContentWorkloadProcess.ps1', 'runtime/lib/ContentReleaseHold.ps1', 'Invoke-ContentReleaseDeployer.ps1',
                         'lib/ContentReleaseDeployer.ps1', 'release/allowed_signers', 'release/github_known_hosts') {
            Test-Path -LiteralPath (Join-Path $PSScriptRoot "../$rel") | Should -BeTrue -Because "$rel is installed"
        }
    }
}

Describe 'ACL checks' {
    It 'finds an allow rule and reports a missing one' {
        $dir = New-Tree @{ 'x' = 'y' }
        Test-ContentAclAllows -Path $dir -Sid $script:me -Rights 'ReadAndExecute' | Should -BeTrue
        $other = [Security.Principal.SecurityIdentifier]::new('S-1-5-21-1-2-3-4242')
        Test-ContentAclAllows -Path $dir -Sid $other -Rights 'ReadAndExecute' | Should -BeFalse
        Test-ContentAclAllows -Path (Join-Path $dir 'missing') -Sid $script:me -Rights 'Read' | Should -BeFalse
    }

    It 'finds a deny rule' {
        $dir = New-Tree @{ '.env' = 'secret' }
        $file = Join-Path $dir '.env'
        $other = [Security.Principal.SecurityIdentifier]::new('S-1-5-21-1-2-3-4242')
        Test-ContentAclDenies -Path $file -Sid $other -Rights 'Read' | Should -BeFalse
        $acl = Get-Acl -LiteralPath $file
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($other, 'Read', 'Deny'))
        Set-Acl -LiteralPath $file -AclObject $acl
        Test-ContentAclDenies -Path $file -Sid $other -Rights 'Read' | Should -BeTrue
    }
}

Describe 'Settings and signer' {
    It 'reads the pinned signer principal' {
        Get-ContentSignerPrincipal -Path (Join-Path $PSScriptRoot '../release/allowed_signers') | Should -Contain 'kolade-ogunleye'
    }

    It 'builds the deployer settings' {
        $s = New-ContentDeployerSettings -Git 'C:\g\git.exe' -Ssh 'C:\s\ssh.exe' -Repository 'git@github.com:kolade-ogunleye/relic-and-riddle.git' -Signer 'kolade-ogunleye' -CheckMinutes 2
        @($s.Keys) | Should -Be @('git', 'ssh', 'repository', 'signer', 'check_minutes')
        $s.check_minutes | Should -Be 2
    }

    It 'refuses a non-SSH repository, a bad signer, an out-of-range interval and a quoted path' {
        { New-ContentDeployerSettings -Git 'g' -Ssh 's' -Repository 'https://github.com/a/b.git' -Signer 'x' -CheckMinutes 2 } | Should -Throw '*GitHub SSH URL*'
        { New-ContentDeployerSettings -Git 'g' -Ssh 's' -Repository 'git@github.com:a/b.git' -Signer 'x y' -CheckMinutes 2 } | Should -Throw '*principal*'
        { New-ContentDeployerSettings -Git 'g' -Ssh 's' -Repository 'git@github.com:a/b.git' -Signer 'x' -CheckMinutes 0 } | Should -Throw '*between 1 and 60*'
        { New-ContentDeployerSettings -Git 'C:\a"b\git.exe' -Ssh 's' -Repository 'git@github.com:a/b.git' -Signer 'x' -CheckMinutes 2 } | Should -Throw '*quote*'
    }
}

Describe 'Task specification' {
    It 'runs the installed deployer copy as LAB\rr-deploy$, one instance at a time' {
        $spec = Get-ContentDeployerTaskSpec -Root 'C:\R' -PowerShell 'C:\W\powershell.exe'
        $spec.Name | Should -Be 'RelicAndRiddle-ReleaseDeployer-Unattended'
        $spec.Identity | Should -Be 'LAB\rr-deploy$'
        $spec.Arguments | Should -Be '-NoProfile -NonInteractive -WindowStyle Hidden -File "C:\R\tools\deploy\Invoke-ContentReleaseDeployer.ps1"'
        $spec.MultipleInstances | Should -Be 2
        $spec.ExecutionTimeLimit | Should -Be 'PT2H'
    }

    It 'recognizes a matching definition and rejects drift' {
        $spec = Get-ContentDeployerTaskSpec -Root 'C:\R' -PowerShell 'C:\W\powershell.exe'
        Test-ContentDeployerTaskMatches -Definition (New-Definition -Arguments '') -Spec $spec | Should -BeTrue
        Test-ContentDeployerTaskMatches -Definition (New-Definition -Arguments '-File "C:\elsewhere.ps1"') -Spec $spec | Should -BeFalse
        Test-ContentDeployerTaskMatches -Definition (New-Definition -Arguments '' -Triggers 3) -Spec $spec | Should -BeFalse
    }
}

Describe 'Installer safety' {
    It 'runs only elevated on the production VM' {
        $script:installer | Should -Match "if \(\`$env:COMPUTERNAME -ne 'CONTENTCLIENTWI'\) \{ throw"
        $script:installer | Should -Match "IsInRole\(\[Security\.Principal\.WindowsBuiltInRole\]::Administrator\)\) \{ throw 'Run elevated\.' \}"
    }

    It 'changes nothing until every prerequisite holds, including a stopped pipeline' {
        $script:installer | Should -Match '(?s)if \(\$problems\.Count\) \{\s+throw \("Nothing was changed\.'
        $script:installer | Should -Match "control\\Pipeline\.stop"
        $script:installer | Should -Match "is running; install while the pipeline and backup are stopped"
        $before = $script:installer.Substring(0, $script:installer.IndexOf('if ($problems.Count)'))
        $before | Should -Not -Match 'Copy-Item|Set-Content -LiteralPath \$settingsPath|RegisterTaskDefinition|ssh-keygen.*-f \$key'
    }

    It 'never changes identities, ACLs or the workload tasks, and never starts the deployer' {
        $script:installer | Should -Not -Match 'Set-Acl|SetAccessRule|AddAccessRule|icacls'
        $script:installer | Should -Not -Match 'New-ADServiceAccount|Install-ADServiceAccount|Add-LocalGroupMember|ntrights'
        $script:installer | Should -Not -Match 'Start-ScheduledTask|\.Run\(|schtasks'
        $script:installer | Should -Not -Match "RegisterTaskDefinition\('RelicAndRiddle-Production"
        $script:installer | Should -Not -Match '\.env.*(Get-Content|Copy-Item)'
    }

    It 'refuses a deployer that could change its own installed copy or its key' {
        $script:installer | Should -Match "can change its own installed copy in \`$deployDir; refusing to continue"
        $script:installer | Should -Match "can write secrets\\Deploy; it must only read its key"
    }

    It 'backs up what it replaces and never prints the private key' {
        $script:installer | Should -Match 'rollback\\deployer-install-\$stamp'
        $script:installer | Should -Match "Get-Content -LiteralPath `"\`$key\.pub`" -Raw"
        $script:installer | Should -Not -Match 'Get-Content -LiteralPath \$key\b'
        $script:installer | Should -Not -Match 'Get-Content -LiteralPath "\$key"'
    }

    It 'takes over no task registered under another identity' {
        $script:installer | Should -Match 'exists with another identity \(\$stored\); not overwritten'
    }
}
