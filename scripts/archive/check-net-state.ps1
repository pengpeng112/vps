Write-Host "=== Adapters ==="
Get-NetAdapter | Format-Table Name, InterfaceDescription, Status, LinkSpeed -AutoSize
Write-Host "=== IPv4 addresses ==="
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^127\.' } | Format-Table InterfaceAlias, IPAddress, PrefixLength -AutoSize
Write-Host "=== Default routes ==="
Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Format-Table InterfaceAlias, NextHop -AutoSize
Write-Host "=== Neighbor 192.168.2.1 ==="
Get-NetNeighbor -AddressFamily IPv4 -IPAddress 192.168.2.1 -ErrorAction SilentlyContinue | Format-Table ifIndex, IPAddress, LinkLayerAddress, State -AutoSize
