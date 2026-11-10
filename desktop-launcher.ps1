[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeRoot = Join-Path $projectRoot 'runtime\docker'
$dataRoot = 'C:\AI\Dukascopy_data'
$labPort = 8767
$env:STRATEGY_LAB_PORT = [string]$labPort
$labUrl = "http://127.0.0.1:$labPort/"
$healthUrl = "http://127.0.0.1:$labPort/api/health"
$versionUrl = "http://127.0.0.1:$labPort/api/version"
$backendUrl = 'http://127.0.0.1:9000/'
$stateRoot = Join-Path $env:LOCALAPPDATA 'DukascopyStrategyLab'
$logRoot = Join-Path $stateRoot 'logs'
$null = New-Item -ItemType Directory -Force -Path $logRoot
$logPath = Join-Path $logRoot ("startup-{0:yyyyMMdd-HHmmss}-{1}.log" -f (Get-Date), $PID)
$script:logPath = $logPath
$script:mutex = $null
$script:ownsMutex = $false

function Write-Log {
    param([string]$Message, [ValidateSet('INFO','WARN','ERROR')][string]$Level = 'INFO')
    $line = '[{0:yyyy-MM-dd HH:mm:ss}] {1} {2}' -f (Get-Date), $Level, $Message
    Add-Content -LiteralPath $script:logPath -Value $line -Encoding UTF8
}

function Show-StartupError {
    param([string]$Message)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "$Message`r`n`r`nLogbestand: $script:logPath",
            'Dukascopy Strategy Lab kon niet starten',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error,
            [System.Windows.Forms.MessageBoxDefaultButton]::Button1,
            [System.Windows.Forms.MessageBoxOptions]::ServiceNotification
        ) | Out-Null
    } catch {
        Write-Log "Windows-melding kon niet worden getoond: $($_.Exception.Message)" 'ERROR'
    }
}

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments, [Parameter(Mandatory)][string]$Purpose)
    $quoted = foreach ($argument in $Arguments) {
        '"' + ([string]$argument).Replace('"','\"') + '"'
    }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $script:dockerPath
    $startInfo.Arguments = $quoted -join ' '
    $startInfo.WorkingDirectory = (Get-Location).Path
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "$Purpose kon niet worden gestart." }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.Result.Trim()
    $stderr = $stderrTask.Result.Trim()
    $code = $process.ExitCode
    if ($stdout) { foreach ($line in ($stdout -split "\r?\n")) { Write-Log "$Purpose`: $line" } }
    if ($stderr) { foreach ($line in ($stderr -split "\r?\n")) { Write-Log "$Purpose`: $line" 'WARN' } }
    if ($code -ne 0) { throw "$Purpose is mislukt (exitcode $code). Zie het logbestand." }
    return $stdout
}

function Test-DockerEngine {
    try {
        $version = Invoke-Docker -Arguments @('info','--format','{{.ServerVersion}}') -Purpose 'Docker Engine controleren'
        if ($version) { return $true }
    } catch { Write-Log "Docker Engine nog niet bereikbaar: $($_.Exception.Message)"; return $false }
    return $false
}

function Find-DockerCli {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Docker\Docker\resources\bin\docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\resources\bin\docker.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($candidates) { return ($candidates | Select-Object -First 1) }
    return $null
}

function Find-DockerDesktop {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Docker\Docker\Docker Desktop.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($candidates) { return ($candidates | Select-Object -First 1) }
    $appPathKeys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Docker Desktop.exe',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Docker Desktop.exe'
    )
    foreach ($key in $appPathKeys) {
        if (Test-Path $key) {
            $candidate = (Get-ItemProperty -LiteralPath $key).'(default)'
            if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
        }
    }
    return $null
}

function Wait-DockerEngine {
    if (Test-DockerEngine) {
        Write-Log 'Docker Engine reageert.'
        Hide-DockerDesktopWindow
        return
    }
    $desktop = Find-DockerDesktop
    if (-not $desktop) { throw 'Docker Engine is niet bereikbaar en Docker Desktop.exe is niet gevonden.' }
    Write-Log "Docker Desktop starten: $desktop"
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    $timeout = [DateTime]::UtcNow.AddSeconds(180)
    while ([DateTime]::UtcNow -lt $timeout) {
        Start-Sleep -Seconds 3
        if (Test-DockerEngine) {
            Write-Log 'Docker Engine is gereed; WSL2 is door Docker Desktop gestart.'
            Hide-DockerDesktopWindow
            return
        }
    }
    throw 'Docker Desktop is gestart, maar Docker Engine werd binnen 180 seconden niet bereikbaar.'
}

function Wait-Postgres {
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    $last = 'onbekend'
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $last = Invoke-Docker -Arguments @('inspect','--format','{{.State.Health.Status}}','postgres') -Purpose 'PostgreSQL-healthcheck'
            if ($last -eq 'healthy') { Write-Log 'PostgreSQL is healthy.'; return }
        } catch { $last = 'container niet beschikbaar' }
        Start-Sleep -Seconds 3
    }
    throw "PostgreSQL werd niet healthy binnen 120 seconden (laatste status: $last)."
}

function Wait-Http {
    param([string]$Uri, [int]$Seconds, [string]$Name)
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Log "$Name reageert met HTTP $($response.StatusCode)."
                return $response
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "$Name reageerde niet binnen $Seconds seconden op $Uri."
}

function Get-LabHealth {
    try { return Invoke-RestMethod -UseBasicParsing -Uri $healthUrl -TimeoutSec 3 }
    catch { return $null }
}

function Get-LabVersion {
    try { return Invoke-RestMethod -UseBasicParsing -Uri $versionUrl -TimeoutSec 3 }
    catch { return $null }
}

function Start-LabRuntime {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    & $python -c 'import fastapi,uvicorn,plotly,pandas,pyarrow'
    if ($LASTEXITCODE -ne 0) { throw "Python-dependencies ontbreken. Server niet gestart; log: $logPath" }
    $stdout = Join-Path $logRoot 'strategy-lab.stdout.log'
    $stderr = Join-Path $logRoot 'strategy-lab.stderr.log'
    Write-Log "Strategy Lab-server verborgen starten met $python op poort $labPort. Serveroutput: $stdout / $stderr"
    Start-Process -FilePath $python -ArgumentList @('-m','strategy_lab.server') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    $null = Wait-Http -Uri $healthUrl -Seconds 45 -Name 'Strategy Lab'
    $health = Get-LabHealth
    $version = Get-LabVersion
    if (-not $health -or $health.status -ne 'ok' -or $health.source_root -ne $dataRoot -or -not $health.source_exists) {
        throw "Strategy Lab startte, maar health/sourcecontrole faalde. Serverlog: $stderr"
    }
    if (-not $version -or -not $version.backend_current) {
        throw "Strategy Lab reageert, maar de geladen build komt niet overeen met de bestanden. Zie serverlog: $stderr"
    }
    Write-Log "Strategy Lab-build bevestigd: API $($version.api_version), backend $($version.backend_build_id), frontend $($version.frontend_build_id)."
}

function Update-StaleLabRuntime {
    param($Version)
    if ($Version -and $Version.backend_current) { return $true }
    if ($Version -and ($null -eq $Version.active_runs -or $Version.active_runs -gt 0)) {
        Write-Log "Verouderde backend aangetroffen, maar actieve runstatus is onbekend of niet nul; herstart uitgesteld." 'WARN'
        return $false
    }
    $listener = Get-NetTCPConnection -LocalPort $labPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $true }
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if (-not $serverProcess -or $serverProcess.Name -notmatch '^python(w)?\.exe$' -or $serverProcess.CommandLine -notmatch '(-m\s+strategy_lab\.server|strategy_lab[\\/]server\.py)') {
        throw "Poort $labPort wordt niet door de herkenbare Strategy Lab-server gebruikt; proces is niet beëindigd."
    }
    $activeRunner = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^docker\.exe$' -and $_.CommandLine -match 'compose.*exec.*jesse_runner\.py'
    } | Select-Object -First 1
    if ($activeRunner) {
        Write-Log "Een Jesse runner is nog actief (PID $($activeRunner.ProcessId)); serverherstart uitgesteld." 'WARN'
        return $false
    }
    if ($Version) {
        try {
            $restartUrl = $labUrl + 'api/runtime/restart'
            $body = Invoke-RestMethod -UseBasicParsing -Method Post -Uri $restartUrl -ContentType 'application/json' -Body '{}' -TimeoutSec 5
            if (-not $body.restart_requested) { throw 'Graceful herstart is niet bevestigd.' }
            Write-Log "Graceful serverherstart aangevraagd voor PID $($serverProcess.ProcessId); actieve runs: 0."
        } catch {
            throw "De verouderde server kon de veilige herstart niet bevestigen; bestaand proces blijft draaien. $($_.Exception.Message)"
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while ([DateTime]::UtcNow -lt $deadline -and (Get-Process -Id $serverProcess.ProcessId -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 250 }
        if (Get-Process -Id $serverProcess.ProcessId -ErrorAction SilentlyContinue) { throw 'Graceful Strategy Lab-herstart liep vast; proces is niet geforceerd beëindigd.' }
    } else {
        Write-Log "Oude API heeft geen build-/runstatusendpoint; exacte server-PID $($serverProcess.ProcessId) gecontroleerd en geen Docker-Jesse-runner gevonden. Alleen die server wordt gestopt." 'WARN'
        Stop-Process -Id $serverProcess.ProcessId -ErrorAction Stop
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while ([DateTime]::UtcNow -lt $deadline -and (Get-Process -Id $serverProcess.ProcessId -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 250 }
        if (Get-Process -Id $serverProcess.ProcessId -ErrorAction SilentlyContinue) { throw 'De oude Strategy Lab-server stopte niet; geen tweede server gestart.' }
    }
    Start-LabRuntime
    return $true
}

function Initialize-NativeWindows {
    if (-not ('DukascopyLauncher.NativeWindow' -as [type])) {
        Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
namespace DukascopyLauncher {
    public static class NativeWindow {
        public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
        [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
        public static IntPtr[] FindWindows(string exactTitle) {
            var found = new List<IntPtr>();
            EnumWindows((hWnd, lParam) => {
                if (!IsWindowVisible(hWnd)) return true;
                int n = GetWindowTextLength(hWnd);
                if (n <= 0) return true;
                var title = new StringBuilder(n + 1);
                GetWindowText(hWnd, title, title.Capacity);
                if (String.Equals(title.ToString(), exactTitle, StringComparison.Ordinal)) found.Add(hWnd);
                return true;
            }, IntPtr.Zero);
            return found.ToArray();
        }
        public static IntPtr[] FindWindowsByProcessIds(uint[] processIds) {
            var found = new List<IntPtr>();
            var accepted = new HashSet<uint>(processIds);
            EnumWindows((hWnd, lParam) => {
                if (!IsWindowVisible(hWnd)) return true;
                uint id;
                GetWindowThreadProcessId(hWnd, out id);
                if (!accepted.Contains(id)) return true;
                int n = GetWindowTextLength(hWnd);
                if (n > 0) found.Add(hWnd);
                return true;
            }, IntPtr.Zero);
            return found.ToArray();
        }
        public static uint ProcessId(IntPtr hWnd) { uint id; GetWindowThreadProcessId(hWnd, out id); return id; }
    }
}
'@
    }
}

function Get-AppWindowHandles {
    Initialize-NativeWindows
    [DukascopyLauncher.NativeWindow]::FindWindows('Dukascopy Strategy Lab')
}

function Hide-DockerDesktopWindow {
    Initialize-NativeWindows
    $ids = @((Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id }))
    $handles = if ($ids.Count -gt 0) { [DukascopyLauncher.NativeWindow]::FindWindowsByProcessIds($ids) } else { @() }
    $hidden = 0
    foreach ($handle in $handles) {
        [void][DukascopyLauncher.NativeWindow]::ShowWindowAsync($handle, 0)
        $hidden++
    }
    if ($hidden -gt 0) { Write-Log "Docker Desktop-venster verborgen; Docker Engine blijft actief ($hidden venster(s))." }
    else { Write-Log 'Geen zichtbaar Docker Desktop-hoofdvenster gevonden; Docker Engine blijft actief.' }
}

function Focus-AppWindow {
    param([IntPtr]$Handle)
    if ($Handle -eq [IntPtr]::Zero) { return $false }
    Initialize-NativeWindows
    [void][DukascopyLauncher.NativeWindow]::ShowWindowAsync($Handle, 3)
    return [DukascopyLauncher.NativeWindow]::SetForegroundWindow($Handle)
}

function Open-AppWindow {
    $existing = @(Get-AppWindowHandles)
    if ($existing.Count -gt 0) {
        Write-Log "Bestaand appvenster gevonden ($($existing.Count) exact getitelde venster(s)); verversen en naar voren brengen."
        [void](Focus-AppWindow $existing[0])
        $shell = New-Object -ComObject WScript.Shell
        Start-Sleep -Milliseconds 300
        [void]$shell.SendKeys('^r')
        if ($existing.Count -gt 1) {
            Initialize-NativeWindows
            foreach ($duplicate in ($existing | Select-Object -Skip 1)) {
                [void][DukascopyLauncher.NativeWindow]::PostMessage($duplicate, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
            }
            Write-Log "Extra Strategy Lab-appvensters gesloten: $($existing.Count - 1)."
        }
        return
    }

    $edgeCandidates = @(@(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    $chromeCandidates = @(@(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

    if ($edgeCandidates) {
        Write-Log 'Microsoft Edge appmodus starten.'
        $browserCommand = '"{0}" --app="{1}" --no-first-run --no-default-browser-check --start-maximized' -f $edgeCandidates[0], $labUrl
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Run($browserCommand, 3, $false)
    } elseif ($chromeCandidates) {
        Write-Log 'Edge ontbreekt; Google Chrome appmodus starten.' 'WARN'
        $browserCommand = '"{0}" --app="{1}" --no-first-run --no-default-browser-check --start-maximized' -f $chromeCandidates[0], $labUrl
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Run($browserCommand, 3, $false)
    } else {
        Write-Log 'Edge en Chrome ontbreken; geregistreerde standaardbrowser gebruiken.' 'WARN'
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Run($labUrl, 3, $false)
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $opened = @(Get-AppWindowHandles)
        if ($opened.Count -gt 0) {
            [void](Focus-AppWindow $opened[0])
            Write-Log 'Appvenster geopend en gefocust (titel Dukascopy Strategy Lab).'
            return
        }
    }
    throw 'De browser is gestart maar het venster Dukascopy Strategy Lab werd niet binnen 25 seconden gevonden.'
}

$exitCode = 0
try {
    $script:mutex = [System.Threading.Mutex]::new($false, 'Local\DukascopyStrategyLab.Launcher')
    try { $script:ownsMutex = $script:mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $script:ownsMutex = $true }
    if (-not $script:ownsMutex) {
        Write-Log 'Een andere startprocedure loopt al; de tweede start wordt samengevoegd.'
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while ([DateTime]::UtcNow -lt $deadline) {
            $windows = @(Get-AppWindowHandles)
            if ($windows.Count -gt 0) { [void](Focus-AppWindow $windows[0]); break }
            Start-Sleep -Milliseconds 500
        }
    } else {
        Write-Log 'Launcher gestart.'
        if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
            throw "Dukascopy-bronmap ontbreekt of is niet bereikbaar: $dataRoot"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $composeRoot 'docker-compose.yml'))) {
            throw "Jesse Compose-configuratie ontbreekt: $composeRoot"
        }
        $script:dockerPath = Find-DockerCli
        if (-not $script:dockerPath) { throw 'Docker CLI is niet gevonden. Docker Desktop is geïnstalleerd maar de lokale docker.exe ontbreekt.' }
        Wait-DockerEngine

        Push-Location $composeRoot
        try {
            $running = Invoke-Docker -Arguments @('compose','ps','--status','running','--services') -Purpose 'Jesse Compose-services controleren'
            $runningServices = @($running -split "\r?\n" | Where-Object { $_.Trim() })
            $missing = @(@('postgres','jesse') | Where-Object { $_ -notin $runningServices })
            if ($missing.Count -gt 0) {
                Write-Log "Ontbrekende services starten: $($missing -join ', '). Bestaande draaiende services blijven staan."
                Invoke-Docker -Arguments @('compose','up','-d','--pull','never') -Purpose 'Ontbrekende Jesse-services starten' | Out-Null
            } else {
                Write-Log 'Jesse en PostgreSQL draaien al; Compose hoeft niets te starten.'
            }
        } finally { Pop-Location }

        Wait-Postgres
        Wait-Http -Uri $backendUrl -Seconds 120 -Name 'Jesse-backend' | Out-Null

        $health = Get-LabHealth
        if ($health) {
            if ($health.status -ne 'ok' -or $health.source_root -ne $dataRoot -or -not $health.source_exists) {
                throw "Poort $labPort antwoordt, maar de Strategy Lab-healthcheck of Dukascopy-bron wijkt af. Verwacht bron: $dataRoot"
            }
            $version = Get-LabVersion
            if (-not $version -or -not $version.backend_current) {
                Write-Log 'Actieve Strategy Lab-build ontbreekt of wijkt af van de actuele backendbestanden; veilige vernieuwing controleren.' 'WARN'
                if (Update-StaleLabRuntime -Version $version) {
                    $health = Get-LabHealth
                    $version = Get-LabVersion
                }
            }
            if (-not $health -or $health.status -ne 'ok' -or $health.source_root -ne $dataRoot -or -not $health.source_exists) {
                throw "Strategy Lab-health/sourcecontrole faalde na buildcontrole. Verwacht bron: $dataRoot"
            }
            if ($version -and $version.backend_current) {
                Write-Log "Strategy Lab draait actueel op poort ${labPort}: API $($version.api_version), backend $($version.backend_build_id), frontend $($version.frontend_build_id)."
            } elseif ($version -and $version.active_runs -gt 0) {
                Write-Log 'Verouderde server blijft tijdens actieve run(s) beschikbaar; waarschuwing wordt in de UI getoond. Herhaal de snelkoppelingsstart na afloop.' 'WARN'
            } else {
                throw 'Strategy Lab-build kon niet actueel worden bevestigd.'
            }
        } else {
            $listener = Get-NetTCPConnection -LocalPort $labPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($listener) {
                $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
                $ownerName = if ($owner) { $owner.ProcessName } else { 'onbekend proces' }
                throw "Poort $labPort is bezet door $ownerName (PID $($listener.OwningProcess)), maar serveert de juiste Strategy Lab-healthcheck niet. Dit proces is niet beëindigd."
            }
            Start-LabRuntime
            $health = Get-LabHealth
        }

        Open-AppWindow
        Write-Log "Klaar: $labUrl (bron: $dataRoot). Venster sluiten laat Docker, Jesse en backtests doorlopen."
    }
} catch {
    $exitCode = 1
    Write-Log $_.Exception.Message 'ERROR'
    Show-StartupError -Message $_.Exception.Message
} finally {
    if ($script:ownsMutex -and $script:mutex) { try { $script:mutex.ReleaseMutex() } catch { } }
    if ($script:mutex) { $script:mutex.Dispose() }
}
exit $exitCode
