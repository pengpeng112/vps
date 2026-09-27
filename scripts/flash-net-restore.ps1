# Restore the Realtek wired NIC to DHCP after flashing (company net or iStoreOS 192.168.100.x).
# ASCII-only on purpose: Windows PowerShell 5.1 fails to parse UTF-8-no-BOM scripts.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File F:\vps\scripts\flash-net-restore.ps1
$ErrorActionPreference = 'Stop'

$adapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like 'Realtek*' } | Select-Object -First 1
if (-not $adapter) {
    Write-Host '[!] Realtek adapter not found'
    exit 1
}

Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.PrefixOrigin -eq 'Manual' } |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -Dhcp Enabled -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Where-Object { $_.NextHop -ne '0.0.0.0' } |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue

ipconfig /release $adapter.Name 2>$null | Out-Null
ipconfig /renew $adapter.Name 2>$null | Out-Null
Start-Sleep 3

$ip = (
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1
).IPAddress

if (-not $ip) {
    Write-Host "[!] No DHCP address yet (link may still be negotiating). Adapter: $($adapter.Name)"
    exit 2
}

Write-Host "[OK] DHCP restored, current IP: $ip"
if ($ip -like '192.168.100.*') {
    Write-Host '>> iStoreOS network reached. Open http://192.168.100.1 (root/password)'
} elseif ($ip -like '10.*') {
    Write-Host '>> Back on company network'
}
