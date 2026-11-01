# Set the Realtek wired NIC to static 192.168.1.2/24 for u-boot flashing.
# ASCII-only on purpose: Windows PowerShell 5.1 fails to parse UTF-8-no-BOM scripts.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File F:\vps\scripts\flash-net-set.ps1
$ErrorActionPreference = 'Stop'
$a = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like 'Realtek*' } | Select-Object -First 1
if (-not $a) { Write-Host 'Realtek adapter not found'; exit 1 }
Write-Host ("Adapter: " + $a.Name + " (" + $a.Status + " / " + $a.LinkSpeed + ")")
Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceIndex $a.ifIndex -Dhcp Disabled
New-NetIPAddress -InterfaceIndex $a.ifIndex -IPAddress 192.168.1.2 -PrefixLength 24
Write-Host 'Done. NIC is 192.168.1.2/24, no default gateway.'
Write-Host 'Verify: ping -S 192.168.1.2 192.168.1.1'
