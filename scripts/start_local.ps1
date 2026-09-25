$ErrorActionPreference = "Stop"
Set-Location "C:\CinemaVault"

$pythonPath = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) { throw "PYTHON_VENV_MISSING" }
if (-not (Test-Path ".\.env")) { throw "ENV_FILE_MISSING" }

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:APP_ENV = "development"
$env:TELEGRAM_MODE = "polling"
$env:PUBLIC_BASE_URL = "http://127.0.0.1:8000"
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"
$env:RATE_LIMIT_ENABLED = "0"
$env:RUN_MAINTENANCE_IN_BOT = "0"
$env:RUN_NOTIFICATIONS_IN_BOT = "0"
$env:TELEGRAM_STORAGE_REQUIRED = "0"
$env:STARTUP_VALIDATE = "1"

& .\deploy\production.ps1
