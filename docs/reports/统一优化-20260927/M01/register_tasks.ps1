# M01: 重建 PC 跳板隧道计划任务（原任务丢失；内容按 tunnel.bat/verify.ps1 注释复原）
# AIHMS-JumpTunnel: SYSTEM AtStartup, 运行 tunnel.bat（msys ssh -R 18422/18322 jump@DO）
# AIHMS-JumpTunnel-Verify: SYSTEM AtLogon, 运行 verify.ps1（自检 VPS 监听并写 reboot-verify.txt）
$ErrorActionPreference = 'Stop'

$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\cmd.exe' -Argument '/c "C:\tools\jump-tunnel\tunnel.bat"'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# ExecutionTimeLimit 零=不限时（默认 72h 会杀死常驻隧道循环）
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'AIHMS-JumpTunnel' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Output ('JumpTunnel registered: ' + (Get-ScheduledTask -TaskName 'AIHMS-JumpTunnel').State)

$vaction = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\tools\jump-tunnel\verify.ps1'
$vtrigger = New-ScheduledTaskTrigger -AtLogOn
$vsettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::FromMinutes(10))
Register-ScheduledTask -TaskName 'AIHMS-JumpTunnel-Verify' -Action $vaction -Trigger $vtrigger `
    -Principal $principal -Settings $vsettings -Force | Out-Null
Write-Output ('Verify registered: ' + (Get-ScheduledTask -TaskName 'AIHMS-JumpTunnel-Verify').State)

Start-ScheduledTask -TaskName 'AIHMS-JumpTunnel'
Start-Sleep -Seconds 10
Write-Output ('JumpTunnel after run: ' + (Get-ScheduledTask -TaskName 'AIHMS-JumpTunnel').State)
