[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ConfigPath = '',
    [string]$PythonPath = 'C:\Python311\python.exe',
    [ValidateRange(15, 120)][int]$IntervalMinutes = 30,
    [string]$BackupDirectory = ''
)

# Idempotent: registers ONE task, "Dukascopy Strategy Lab Daily Sync", and replaces any earlier definition.
# Runs whether or not the app is open (as the signed-in user), catches up missed starts (StartWhenAvailable),
# never wakes the computer, never runs two instances. Each start runs `data_check --scheduled`: heartbeat, then a
# provider request ONLY when the supervisor says a complete session day is missing, its update time has passed
# and no backoff covers it. Re-running this script keeps the same schedule anchor (no drift).
$ErrorActionPreference = 'Stop'
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ConfigPath) { $ConfigPath = Join-Path $root 'strategy_lab\daily_sync.local.json' }
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$python = if (Test-Path -LiteralPath $PythonPath -PathType Leaf) { $PythonPath } else { (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw 'pythonw.exe ontbreekt naast de actieve Python-installatie.'
}

Push-Location -LiteralPath $root
try {
    & $python -m strategy_lab.daily_sync --config $config --check-config
    if ($LASTEXITCODE -ne 0) { throw 'De synchronisatieconfiguratie is ongeldig.' }
}
finally { Pop-Location }

$taskName = 'Dukascopy Strategy Lab Daily Sync'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-m strategy_lab.data_check --scheduled --config "{0}"' -f $config
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $root
# Fixed anchor (today 00:00:15 local) so a re-install never shifts the schedule.
$anchor = (Get-Date).Date.AddSeconds(15)
$clockTrigger = New-ScheduledTaskTrigger -Once -At $anchor -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$settings.WakeToRun = $false
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($taskName, 'Register or update daily synchronization')) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        if (-not $BackupDirectory) { $BackupDirectory = Join-Path $root 'evidence\v10\data' }
        New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
        Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $BackupDirectory 'scheduled_task_previous.xml') -Encoding Unicode
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($clockTrigger, $logonTrigger) `
        -Settings $settings -Principal $principal -Description 'Dukascopy M1 intake: supervisor-gated, catches up missed runs' -Force | Out-Null
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Write-Host
}
