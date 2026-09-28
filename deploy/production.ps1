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

# --- Read .env so TELEGRAM_MODE / START_BOT set only in .env are honored ---
# The app itself loads .env from the project root (services run with
# WorkingDirectory = $Project), so read the same file here. Lines are KEY=VALUE;
# comments (#) and blank lines are ignored; CRLF and optional quotes tolerated.
# Shell environment variables always OVERRIDE .env values.
$dotenvKeys = @{}
$envFile = Join-Path $Project ".env"
if (-not (Test-Path $envFile)) {
    $envFile = Join-Path $PSScriptRoot ".env"
}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $eqIndex = $trimmed.IndexOf("=")
        $key = $trimmed.Substring(0, $eqIndex).Trim()
        $value = $trimmed.Substring($eqIndex + 1).Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($key.Length -gt 0) { $dotenvKeys[$key] = $value }
    }
}

$effTelegramMode = $env:TELEGRAM_MODE
if ([string]::IsNullOrWhiteSpace($effTelegramMode) -and $dotenvKeys.ContainsKey("TELEGRAM_MODE")) {
    $effTelegramMode = $dotenvKeys["TELEGRAM_MODE"]
}
$effStartBot = $env:START_BOT
if ([string]::IsNullOrWhiteSpace($effStartBot) -and $dotenvKeys.ContainsKey("START_BOT")) {
    $effStartBot = $dotenvKeys["START_BOT"]
}
# Propagate the effective values so child processes match the gate decision.
if (-not [string]::IsNullOrWhiteSpace($effTelegramMode)) { $env:TELEGRAM_MODE = $effTelegramMode }
if (-not [string]::IsNullOrWhiteSpace($effStartBot)) { $env:START_BOT = $effStartBot }

$telegramMode = ""
if (-not [string]::IsNullOrWhiteSpace($effTelegramMode)) { $telegramMode = $effTelegramMode.Trim().ToLowerInvariant() }

$botWillStart = $false
if ($telegramMode -eq "polling") {
    if ($effStartBot -eq "0") {
        Write-Host "[BOT] Skipped: START_BOT=0" -ForegroundColor Yellow
        Write-Host "BOT skipped (START_BOT=0) - panel and workers are starting without the Telegram bot." -ForegroundColor Yellow
    } else {
        $botWillStart = $true
    }
}
elseif ($telegramMode -eq "webhook") {
    Write-Host "[BOT] Skipped: TELEGRAM_MODE=webhook (start via API process)" -ForegroundColor Yellow
}
elseif ([string]::IsNullOrWhiteSpace($telegramMode)) {
    Write-Host "[BOT] Skipped: TELEGRAM_MODE not set (set TELEGRAM_MODE=polling in .env to enable)" -ForegroundColor Yellow
}
else {
    Write-Host "[BOT] Skipped: TELEGRAM_MODE=$telegramMode (not polling; set TELEGRAM_MODE=polling in .env to enable)" -ForegroundColor Yellow
}

if ($botWillStart) {
    $services += @{
        Name = "BOT"
        Args = @(".\main.py")
        Out = Join-Path $LogDir "bot.out.log"
        Err = Join-Path $LogDir "bot.err.log"
    }
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
