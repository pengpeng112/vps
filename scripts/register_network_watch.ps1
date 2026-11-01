# Register the local read-only observation task. Uses a distinct name from VPS-WebProbe.
$ErrorActionPreference = 'Stop'
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot 'network_watch.py')).Path
$pythonPath = (& python.exe -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Could not resolve the actual Python interpreter'
}
$taskName = 'VPS-NetworkWatch'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "$taskName already exists; inspect it before changing the schedule"
}
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"' + $scriptPath + '" --scheduled') -WorkingDirectory (Split-Path $scriptPath)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'Local read-only network observation every 15 minutes' | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
