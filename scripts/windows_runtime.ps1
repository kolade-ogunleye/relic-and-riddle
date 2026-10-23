# Idempotent runtime preparation and per-environment, hidden task management.
# Never creates, copies, or rewrites .env. InstallTask never starts the task.
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'InstallTask', 'Start', 'Stop', 'Status')]
    [string]$Action = 'Prepare',
    [string]$AppPath = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet('Development', 'Production')]
    [string]$Environment = 'Development',
    [ValidateSet('Validation', 'Live', 'ConfiguredBot')]
    [string]$Mode = 'ConfiguredBot',
    [string]$ExpectedBotId,
    [string]$ForbiddenBotId,
    [string]$FfmpegBin
)
$ErrorActionPreference = 'Stop'
if ($Environment -eq 'Production') {
    throw 'Production task lifecycle belongs to lab automation. This helper manages development only.'
}
$app = (Resolve-Path -LiteralPath $AppPath).Path
$envFile = Join-Path $app '.env'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Provision $envFile separately with this environment's credentials; it is never copied from dev."
}
$envHash = (Get-FileHash -LiteralPath $envFile -Algorithm SHA256).Hash
foreach ($relative in @('logs', 'output', 'cache', 'archive', 'data', 'data/topics',
                         'data/scripts', 'data/sfx', 'data/publication_locks',
                         'assets', 'fonts', 'docs/issues')) {
    New-Item -ItemType Directory -Path (Join-Path $app $relative) -Force | Out-Null
}
$taskName = "RelicAndRiddle-$Environment"
if ($Action -eq 'Prepare') {
    Write-Output "Prepared runtime directories: $app; .env preserved."
    return
}
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
if ($Action -eq 'InstallTask') {
    if ($ExpectedBotId -notmatch '^\d+$' -or $ExpectedBotId -eq $ForbiddenBotId) {
        throw 'Supply the expected bot ID and a different forbidden bot ID.'
    }
    if ($Environment -eq 'Production' -and -not $ForbiddenBotId) {
        throw 'Production requires the development bot ID as ForbiddenBotId.'
    }
    if ($Environment -eq 'Development' -and $Mode -eq 'Live') {
        throw 'This launcher does not activate live development publishing.'
    }
    if ($Mode -eq 'ConfiguredBot' -and $Environment -ne 'Development') {
        throw 'ConfiguredBot preserves the existing development bot mode; production uses the daemon.'
    }
    # A full path avoids a package-manager PATH change silently replacing FFmpeg.
    if (-not $FfmpegBin) { $FfmpegBin = Split-Path -Parent (Get-Command ffmpeg.exe).Source }
    $FfmpegBin = (Resolve-Path -LiteralPath $FfmpegBin).Path
    foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $FfmpegBin $tool))) { throw "Missing $tool in $FfmpegBin" }
    }
    $existing = @($folder.GetTasks(0) | Where-Object Name -eq $taskName)
    if ($existing -and $existing[0].State -eq 4) { throw "Stop $taskName before updating its launcher." }
    $quotedApp = $app.Replace("'", "''")
    $quotedBin = $FfmpegBin.Replace("'", "''")
    $launcherPath = Join-Path $app "logs/runtime-$Environment.ps1"
    $command = if ($Mode -eq 'ConfiguredBot') { 'bot' } elseif ($Mode -eq 'Validation') { 'daemon --dry-run' } else { 'daemon' }
    $launcher = @(
        '$ErrorActionPreference = "Continue"',
        "Set-Location '$quotedApp'",
        "`$env:PATH = '$quotedBin;' + `$env:PATH",
        "`$env:RELIC_ENVIRONMENT = '$Environment'",
        "`$actual = & .\.venv\Scripts\python.exe -c 'from src.config import TELEGRAM_BOT_TOKEN; print(TELEGRAM_BOT_TOKEN.split(chr(58))[0])'",
        "if (`$LASTEXITCODE -ne 0 -or `$actual.Trim() -ne '$ExpectedBotId') { throw 'Bot identity mismatch; worker not started.' }",
        "if (Test-Path 'logs/runtime-$Environment.stop') { exit 0 }",
        # One worker at a time: a task restart that killed only this launcher left its worker running, and
        # each later start added another bot (seven competed for one token on 2026-10-23).
        "if (Test-Path 'logs/runtime-$Environment.pid.json') {",
        "    `$prior = Get-Content 'logs/runtime-$Environment.pid.json' -Raw | ConvertFrom-Json",
        "    `$alive = Get-Process -Id `$prior.pid -ErrorAction SilentlyContinue",
        "    if (`$alive -and -not `$alive.HasExited -and `$alive.Path -eq (Join-Path '$quotedApp' '.venv\Scripts\python.exe') -and `$alive.StartTime.ToUniversalTime().Ticks -eq ([datetimeoffset]`$prior.started).UtcDateTime.Ticks) {",
        "        throw 'A worker from an earlier start is still running; stop it with scripts/windows_runtime.ps1 -Action Stop.'",
        '    }',
        '}',
        "`$worker = Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList '-u main.py $command' -WorkingDirectory '$quotedApp' -WindowStyle Hidden -RedirectStandardOutput 'logs/runtime-$Environment.stdout.log' -RedirectStandardError 'logs/runtime-$Environment.stderr.log' -PassThru",
        "@{ pid = `$worker.Id; started = `$worker.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content 'logs/runtime-$Environment.pid.json'",
        'while (-not $worker.WaitForExit(1000)) {',
        "    if (Test-Path 'logs/runtime-$Environment.stop') {",
        '        & taskkill.exe /PID $worker.Id /T /F | Out-Null',
        "        if (`$LASTEXITCODE -ne 0) { throw 'Failed to stop worker tree in its owning login session.' }",
        '        $worker.WaitForExit()',
        '        break',
        '    }',
        '}',
        'exit $worker.ExitCode'
    )
    $launcher | Set-Content -LiteralPath $launcherPath -Encoding UTF8
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = "Relic & Riddle $Environment daemon $Mode; preserves host .env. Requires this user's login session."
    $definition.Principal.UserId = $identity
    $definition.Principal.LogonType = 3 # InteractiveToken; no saved password.
    $definition.Principal.RunLevel = 0
    $definition.Settings.ExecutionTimeLimit = 'PT0S'
    $definition.Settings.MultipleInstances = 2 # IgnoreNew
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.RestartCount = 3
    $definition.Settings.RestartInterval = 'PT1M'
    $trigger = $definition.Triggers.Create(9) # AtLogon for this user, not arbitrary users.
    $trigger.UserId = $identity
    $actionDefinition = $definition.Actions.Create(0)
    $actionDefinition.Path = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $actionDefinition.Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`""
    $actionDefinition.WorkingDirectory = $app
    $folder.RegisterTaskDefinition($taskName, $definition, 6, $identity, $null, 3) | Out-Null
} else {
    $task = $folder.GetTask($taskName)
    if ($Action -eq 'Start') {
        if ($task.State -ne 4) {
            $stopFile = Join-Path $app "logs/runtime-$Environment.stop"
            if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
            $task.Enabled = $true
            $task.Run($null) | Out-Null
        }
    }
    if ($Action -eq 'Stop') {
        $database = Join-Path $app 'data/pipeline.db'
        if (Test-Path -LiteralPath $database) {
            Push-Location -LiteralPath $app
            try {
                $countCode = "import sqlite3; from pathlib import Path; from src.operator_jobs import live_job_count; c=sqlite3.connect(Path('data/pipeline.db').resolve().as_uri()+'?mode=ro',uri=True); print(c.execute('SELECT COUNT(*) FROM runs WHERE status IN (?,?)',('producing','publishing')).fetchone()[0]+live_job_count(c))"
                $active = & .\.venv\Scripts\python.exe -c $countCode
                if ($LASTEXITCODE -ne 0 -or [int]$active -gt 0) {
                    throw 'A run/upload or background operator job is active or unverifiable; let it finish or explicitly cancel/reconcile it before stopping for deployment.'
                }
            } finally { Pop-Location }
        }
        $task.Enabled = $false
        # The launcher kills its own child tree in the owning desktop session.
        # SSH may inspect that process but cannot necessarily terminate it.
        'requested' | Set-Content -LiteralPath (Join-Path $app "logs/runtime-$Environment.stop")
        for ($attempt = 0; $attempt -lt 30 -and $task.State -eq 4; $attempt++) { Start-Sleep -Seconds 1 }
        if ($task.State -eq 4) { throw 'Worker did not stop; do not clean up or deploy over it.' }
        $pidFile = Join-Path $app "logs/runtime-$Environment.pid.json"
        if (Test-Path -LiteralPath $pidFile) {
            $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
            $worker = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
            if ($worker -and -not $worker.HasExited) {
                if ($worker.Path -ne (Join-Path $app '.venv\Scripts\python.exe') -or
                    $worker.StartTime.ToUniversalTime().Ticks -ne ([datetimeoffset]$record.started).UtcDateTime.Ticks) {
                    throw 'Worker PID identity changed; no process terminated.'
                }
                throw 'The task ended but its Python worker survived; cleanup remains blocked.'
            }
        }
    }
}
if ((Get-FileHash -LiteralPath $envFile -Algorithm SHA256).Hash -ne $envHash) {
    throw '.env changed during runtime preparation; investigate before proceeding.'
}
$task = $folder.GetTask($taskName)
[pscustomobject]@{Task = $taskName; State = $task.State; LastResult = $task.LastTaskResult; EnvPreserved = $true}
