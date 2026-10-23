BeforeAll {
    . (Join-Path $PSScriptRoot '../runtime/lib/ContentReleaseHold.ps1')
    $script:commit = 'a' * 40
    function New-HoldFixture {
        $dir = Join-Path $TestDrive ([guid]::NewGuid().ToString('n'))
        New-Item -ItemType Directory -Path $dir | Out-Null
        $paths = @{
            HoldPath = Join-Path $dir 'Pipeline.hold'; StopPath = Join-Path $dir 'Pipeline.stop'
            VerifyPath = Join-Path $dir 'Pipeline.verify'; StatePath = Join-Path $dir 'Hold.json'
        }
        Set-Content -LiteralPath $paths.HoldPath -Value 'v1.0.0-rc10'
        return $paths
    }
}

Describe 'Release hold' {
    It 'runs a verification request once, reports it, and resumes when the hold is removed' {
        $p = New-HoldFixture
        Set-Content -LiteralPath $p.VerifyPath -Value $script:commit
        $script:calls = 0
        $hold = $p.HoldPath
        $verify = {
            param($c)
            $script:calls++
            $c | Should -Be $script:commit
            [pscustomobject]@{ Passed = $true; ExitCode = 0; Detail = 'the test suite passed' }
        }
        $states = [Collections.Generic.List[string]]::new()
        $state = $p.StatePath
        $job = { param($c) $r = & $verify $c; $states.Add((Get-Content $state -Raw | ConvertFrom-Json).State); Remove-Item $hold; $r }.GetNewClosure()
        $outcome = Wait-ContentReleaseHold @p -Verify $job -PollSeconds 0.05
        $outcome | Should -Be 'Resumed'
        $script:calls | Should -Be 1
        $states | Should -Be @('verifying')
        (Get-Content $p.StatePath -Raw | ConvertFrom-Json).State | Should -Be 'resumed'
    }

    It 'records the verification outcome for the deployer while still held' {
        $p = New-HoldFixture
        Set-Content -LiteralPath $p.VerifyPath -Value $script:commit
        $hold = $p.HoldPath
        $state = $p.StatePath
        $box = @{}
        $verify = { param($c) [pscustomobject]@{ Passed = $false; ExitCode = 1; Detail = 'the test suite failed (exit 1)' } }
        $alert = { $box.seen = Get-Content $state -Raw | ConvertFrom-Json; Remove-Item $hold }.GetNewClosure()
        Wait-ContentReleaseHold @p -Verify $verify -Alert $alert -AlertAfterMinutes 0 -PollSeconds 0.05 | Should -Be 'Resumed'
        $box.seen.State | Should -Be 'verified'
        $box.seen.Passed | Should -BeFalse
        $box.seen.Commit | Should -Be $script:commit
        $box.seen.Detail | Should -Match 'failed'
    }

    It 'reports a verification that could not run as failed' {
        $p = New-HoldFixture
        Set-Content -LiteralPath $p.VerifyPath -Value $script:commit
        $hold = $p.HoldPath
        $state = $p.StatePath
        $box = @{}
        $alert = { $box.seen = Get-Content $state -Raw | ConvertFrom-Json; Remove-Item $hold }.GetNewClosure()
        Wait-ContentReleaseHold @p -Verify { throw 'python missing' } -Alert $alert -AlertAfterMinutes 0 -PollSeconds 0.05 | Out-Null
        $box.seen.Passed | Should -BeFalse
        $box.seen.Detail | Should -Match 'python missing'
    }

    It 'ignores a malformed verification request' {
        $p = New-HoldFixture
        Set-Content -LiteralPath $p.VerifyPath -Value 'not-a-commit'
        $hold = $p.HoldPath
        $script:calls = 0
        $alert = { Remove-Item $hold }.GetNewClosure()
        Wait-ContentReleaseHold @p -Verify { $script:calls++ } -Alert $alert -AlertAfterMinutes 0 -PollSeconds 0.05 | Out-Null
        $script:calls | Should -Be 0
    }

    It 'stops when the operator stop file appears during a hold' {
        $p = New-HoldFixture
        Set-Content -LiteralPath $p.StopPath -Value ''
        Wait-ContentReleaseHold @p -Verify { } -PollSeconds 0.05 | Should -Be 'Stopped'
        Test-Path $p.HoldPath | Should -BeTrue  # the deployer's hold is left for it to resolve
    }
}

Describe 'Release verification' {
    BeforeAll {
        $script:app = Join-Path $TestDrive 'app'
        New-Item -ItemType Directory -Path (Join-Path $script:app '.git') -Force | Out-Null
        $script:logs = Join-Path $TestDrive 'logs'
        New-Item -ItemType Directory -Path $script:logs -Force | Out-Null
        $script:python = Join-Path $TestDrive 'python.cmd'
        Set-Content -LiteralPath $script:python -Value '@exit /b %FAKE_PYTEST_EXIT%' -Encoding ASCII
    }

    It 'refuses when the tree is not at the requested commit' {
        Set-Content -LiteralPath (Join-Path $script:app '.git/HEAD') -Value ('b' * 40)
        $r = Invoke-ContentReleaseVerification -Python $script:python -AppRoot $script:app -LogDirectory $script:logs -Commit $script:commit
        $r.Passed | Should -BeFalse
        $r.Detail | Should -Match 'not'
    }

    It 'passes on a zero exit and fails on any other' -TestCases @(@{ Code = 0; Passed = $true }, @{ Code = 1; Passed = $false }) {
        param($Code, $Passed)
        Set-Content -LiteralPath (Join-Path $script:app '.git/HEAD') -Value $script:commit
        $env:FAKE_PYTEST_EXIT = [string]$Code
        try {
            $r = Invoke-ContentReleaseVerification -Python $script:python -AppRoot $script:app -LogDirectory $script:logs -Commit $script:commit
        } finally { Remove-Item Env:FAKE_PYTEST_EXIT }
        $r.Passed | Should -Be $Passed
        $r.ExitCode | Should -Be $Code
    }
}
