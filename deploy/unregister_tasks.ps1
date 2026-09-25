$names=@("FemonaSense Web","FemonaSense Runtime","FemonaSense Media")
foreach($n in $names){ Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
Write-Host "SCHEDULED_TASKS_REMOVED" -ForegroundColor Yellow
