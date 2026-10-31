$ErrorActionPreference = 'Stop'

function Start-ContentLoggedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$StdoutPath,
        [Parameter(Mandatory)][string]$StderrPath
    )
    $process = [Diagnostics.Process]::new()
    $stdout = $null
    $stderr = $null
    try {
        $stdout = [IO.File]::Open($StdoutPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $stderr = [IO.File]::Open($StderrPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $process.StartInfo.FileName = $FilePath
        $process.StartInfo.Arguments = $Arguments
        $process.StartInfo.WorkingDirectory = $WorkingDirectory
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        if (-not $process.Start()) { throw 'Workload process did not start.' }
        $outputTask = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $errorTask = $process.StandardError.BaseStream.CopyToAsync($stderr)
        [pscustomobject]@{ Process = $process; Stdout = $stdout; Stderr = $stderr; OutputTask = $outputTask; ErrorTask = $errorTask }
    } catch {
        if ($stdout) { $stdout.Dispose() }
        if ($stderr) { $stderr.Dispose() }
        $process.Dispose()
        throw
    }
}

function Complete-ContentLoggedProcess {
    param([Parameter(Mandatory)][psobject]$Worker)
    if (-not $Worker.Process.HasExited) { throw 'Workload is still running; completion is unproven.' }
    $Worker.Process.WaitForExit()
    if (-not $Worker.OutputTask.Wait(5000) -or -not $Worker.ErrorTask.Wait(5000)) {
        throw 'Workload output streams did not close; completion is unproven.'
    }
    $Worker.Stdout.Flush()
    $Worker.Stderr.Flush()
    $code = $Worker.Process.ExitCode
    if ($null -eq $code) { throw 'Worker exit code unavailable; no success receipt can be written.' }
    return [int]$code
}

function Close-ContentLoggedProcess {
    param([Parameter(Mandatory)][psobject]$Worker)
    $Worker.Stdout.Dispose()
    $Worker.Stderr.Dispose()
    $Worker.Process.Dispose()
}
