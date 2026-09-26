$ErrorActionPreference = "Stop"

# UTF-8 end to end (Persian output to redirected logs must not hit cp1252)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Project = "C:\CinemaVault"
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$LogDir = Join-Path $Project "logs"

if (-not (Test-Path $Python)) {
    throw "PYTHON_VENV_MISSING"
}

New-Item -ItemType Directory -Force $LogDir | Out-Null

$services = @(
    @{
        Name = "API"
        Args = @("-m","uvicorn","app.api.main:app","--host","127.0.0.1","--port","8000","--workers","1")
        Out = Join-Path $LogDir "api.out.log"
        Err = Join-Path $LogDir "api.err.log"
    },
    @{
        Name = "MEDIA"
        Args = @("-m","app.workers.media_worker")
        Out = Join-Path $LogDir "media_worker.out.log"
        Err = Join-Path $LogDir "media_worker.err.log"
    },
    @{
        Name = "RUNTIME"
        Args = @("-m","app.workers.runtime_worker")
        Out = Join-Path $LogDir "runtime_worker.out.log"
        Err = Join-Path $LogDir "runtime_worker.err.log"
    }
)

if ($env:TELEGRAM_MODE -eq "polling" -and $env:START_BOT -ne "0") {
    $services += @{
        Name = "BOT"
        Args = @(".\main.py")
        Out = Join-Path $LogDir "bot.out.log"
        Err = Join-Path $LogDir "bot.err.log"
    }
}
elseif ($env:TELEGRAM_MODE -eq "polling" -and $env:START_BOT -eq "0") {
    # Run without the bot (e.g. when api.telegram.org is unreachable):
    #   $env:START_BOT = "0"  then run start_local.ps1 -- panel and workers start, bot is skipped.
    Write-Host "BOT skipped (START_BOT=0) - panel and workers are starting without the Telegram bot." -ForegroundColor Yellow
}

foreach ($service in $services) {
    Set-Content -Path $service.Out -Value "" -Encoding UTF8
    Set-Content -Path $service.Err -Value "" -Encoding UTF8
}

$processes = @()

foreach ($service in $services) {
    $processes += [PSCustomObject]@{
        Name = $service.Name
        Process = Start-Process `
            -FilePath $Python `
            -ArgumentList $service.Args `
            -WorkingDirectory $Project `
            -RedirectStandardOutput $service.Out `
            -RedirectStandardError $service.Err `
            -PassThru
    }
}

Start-Sleep -Seconds 10

$botFailed = $false

foreach ($item in $processes) {
    if ($item.Process.HasExited) {
        Write-Host ""
        Write-Host "=== SERVICE FAILED: $($item.Name) ===" -ForegroundColor Red

        $service = $services | Where-Object Name -eq $item.Name

        Write-Host "--- STDERR ---" -ForegroundColor Yellow
        if (Test-Path $service.Err) {
            Get-Content $service.Err -ErrorAction SilentlyContinue | Select-Object -Last 160
        }

        Write-Host "--- STDOUT ---" -ForegroundColor Yellow
        if (Test-Path $service.Out) {
            Get-Content $service.Out -ErrorAction SilentlyContinue | Select-Object -Last 120
        }

        if ($item.Name -eq "BOT") {
            # Non-fatal: when api.telegram.org is unreachable, the panel and
            # workers must stay online. Only the bot is affected.
            $botFailed = $true
            Write-Host "BOT failed but panel and workers keep running." -ForegroundColor Yellow
            Write-Host "Hints:" -ForegroundColor Yellow
            Write-Host "  1) Test connectivity: Test-NetConnection api.telegram.org -Port 443"
            Write-Host "  2) If Telegram is blocked, set TELEGRAM_PROXY_URL in .env (e.g. socks5://127.0.0.1:10808)"
            Write-Host "  3) To start without the bot: set START_BOT=0 before running start_local.ps1"
            continue
        }

        foreach ($other in $processes) {
            if (-not $other.Process.HasExited) {
                Stop-Process -Id $other.Process.Id -Force -ErrorAction SilentlyContinue
            }
        }

        throw "SERVICE_START_FAILED:$($item.Name)"
    }
}

Write-Host ""
if ($botFailed) {
    Write-Host "WINDOWS_PROCESS_STARTUP_OK (BOT failed - panel is online, see hints above)" -ForegroundColor Yellow
} else {
    Write-Host "WINDOWS_PROCESS_STARTUP_OK"
}
$processes | ForEach-Object {
    if ($_.Process.HasExited) {
        Write-Host "$($_.Name)_PID=FAILED"
    } else {
        Write-Host "$($_.Name)_PID=$($_.Process.Id)"
    }
}
