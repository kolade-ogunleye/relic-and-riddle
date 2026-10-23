$ErrorActionPreference = 'Stop'

function Invoke-ContentProbeProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$Arguments,
        [ValidateRange(1, 60)][int]$TimeoutSeconds = 15
    )
    $process = [System.Diagnostics.Process]::new()
    try {
        $process.StartInfo.FileName = $FilePath
        $process.StartInfo.Arguments = $Arguments
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        if (-not $process.Start()) { throw 'Probe process did not start.' }
        # Drain both pipes asynchronously; never return their contents to the caller.
        $stdout = $process.StandardOutput.BaseStream.CopyToAsync([System.IO.Stream]::Null)
        $stderr = $process.StandardError.BaseStream.CopyToAsync([System.IO.Stream]::Null)
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Probe process exceeded its time limit and was terminated.'
        }
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        return $process.ExitCode
    } finally { $process.Dispose() }
}
