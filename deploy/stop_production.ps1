$ErrorActionPreference = "SilentlyContinue"

$Project = "C:\CinemaVault"

Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*$Project*" -and
        (
            $_.CommandLine -match "uvicorn" -or
            $_.CommandLine -match "app.workers.runtime_worker" -or
            $_.CommandLine -match "app.workers.media_worker" -or
            $_.CommandLine -match "main.py"
        )
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host "فمونا سنس production processes stopped." -ForegroundColor Yellow
