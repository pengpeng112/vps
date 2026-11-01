# 刷机前运行：把本机 Realtek 千兆网卡设为静态 IP 192.168.1.10（u-boot 刷机网段）
# 用法：powershell -ExecutionPolicy Bypass -File flash-net-setup.ps1
# 完成刷机后运行 flash-net-restore.ps1 恢复
$ErrorActionPreference = "Stop"

$adapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "Realtek*" } | Select-Object -First 1
if (-not $adapter) {
    Write-Host "[!] 未找到 Realtek 千兆网卡" -ForegroundColor Red
    exit 1
}

Write-Host "目标网卡: $($adapter.Name) ($($adapter.InterfaceDescription)) 当前速率: $($adapter.LinkSpeed)"
if ($adapter.Status -ne "Up") {
    Write-Host "[!] 网卡未连接：请把网线插到路由器 WAN 口后再运行" -ForegroundColor Yellow
}

# 清掉已有 IPv4（含 DHCP 租约），再关 DHCP，避免 New-NetIPAddress 因地址冲突静默失败
Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -Dhcp Disabled -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress "192.168.1.10" -PrefixLength 24 | Out-Null
Remove-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "0.0.0.0/0" -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "[OK] 已设置 192.168.1.10/24（无默认网关，不影响其他网卡）" -ForegroundColor Green
Write-Host "`n验证：ping 192.168.1.1  （u-boot 应答即链路正常）"

$ping = Test-Connection -ComputerName 192.168.1.1 -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($ping) {
    Write-Host "[OK] 192.168.1.1 可达，浏览器打开 http://192.168.1.1" -ForegroundColor Green
} else {
    Write-Host "[!] 暂时 ping 不通：检查是否插的 WAN 口、链路是否协商到 1 Gbps" -ForegroundColor Yellow
}
