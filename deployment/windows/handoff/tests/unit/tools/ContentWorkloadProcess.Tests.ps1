BeforeAll {
    . (Join-Path $PSScriptRoot '../../../tools/lib/ContentWorkloadProcess.ps1')
}

Describe 'Logged workload native exit codes' {
    It 'captures a fast nonzero exit and closes both output streams' {
        $worker = Start-ContentLoggedProcess -FilePath $env:ComSpec -Arguments '/d /c "echo fixture-output & echo fixture-error 1>&2 & exit /b 7"' `
            -WorkingDirectory $TestDrive -StdoutPath (Join-Path $TestDrive 'stdout.log') -StderrPath (Join-Path $TestDrive 'stderr.log')
        try {
            $worker.Process.WaitForExit()
            Complete-ContentLoggedProcess -Worker $worker | Should -Be 7
        } finally { Close-ContentLoggedProcess -Worker $worker }
        (Get-Content (Join-Path $TestDrive 'stdout.log') -Raw) | Should -Match 'fixture-output'
        (Get-Content (Join-Path $TestDrive 'stderr.log') -Raw) | Should -Match 'fixture-error'
    }

    It 'returns zero as an explicit integer for successful completion' {
        $worker = Start-ContentLoggedProcess -FilePath $env:ComSpec -Arguments '/d /c exit /b 0' `
            -WorkingDirectory $TestDrive -StdoutPath (Join-Path $TestDrive 'stdout.log') -StderrPath (Join-Path $TestDrive 'stderr.log')
        try {
            $worker.Process.WaitForExit()
            $code = Complete-ContentLoggedProcess -Worker $worker
            $code | Should -BeOfType ([int])
            $code | Should -Be 0
        } finally { Close-ContentLoggedProcess -Worker $worker }
    }

    It 'refuses to report completion while the worker remains alive' {
        $worker = Start-ContentLoggedProcess -FilePath "$env:SystemRoot/System32/ping.exe" -Arguments '-n 10 127.0.0.1' `
            -WorkingDirectory $TestDrive -StdoutPath (Join-Path $TestDrive 'stdout.log') -StderrPath (Join-Path $TestDrive 'stderr.log')
        try {
            { Complete-ContentLoggedProcess -Worker $worker } | Should -Throw '*still running*'
        } finally {
            if (-not $worker.Process.HasExited) { $worker.Process.Kill(); $worker.Process.WaitForExit() }
            Close-ContentLoggedProcess -Worker $worker
        }
    }
}
