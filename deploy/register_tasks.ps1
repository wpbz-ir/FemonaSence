$ErrorActionPreference = "Stop"
$Project = "C:\CinemaVault"
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Tasks = @(
    @{Name="FemonaSense Web"; Args="-m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1"},
    @{Name="FemonaSense Runtime"; Args="-m app.workers.runtime_worker"},
    @{Name="FemonaSense Media"; Args="-m app.workers.media_worker"}
)
foreach ($t in $Tasks) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument $t.Args -WorkingDirectory $Project
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
}
Write-Host "SCHEDULED_TASKS_REGISTERED" -ForegroundColor Green
