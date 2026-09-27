# M04: register VPS-WebProbe every 2h (revert: schtasks /Delete /TN VPS-WebProbe /F)
$py = 'C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$script = 'F:\vps\scripts\webprobe.py'
Write-Output ("python exists: " + (Test-Path $py))
$a = New-ScheduledTaskAction -Execute $py -Argument $script
Write-Output ("action: " + $a.Execute)
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 30)
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::FromMinutes(15))
Register-ScheduledTask -TaskName 'VPS-WebProbe' -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Output ('VPS-WebProbe: ' + (Get-ScheduledTask -TaskName 'VPS-WebProbe').State)
