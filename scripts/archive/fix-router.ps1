# Router emergency fix v3 - retarget mgmt tunnel to 66cloud GIA (154.36.137.54)
# v3: no download needed - uses Windows built-in ssh. User types router password once.
# Success indicator: RESULT=200
$router = "192.168.132.1"
$cmd    = "cp /etc/routermgmt/tunnel.conf /etc/routermgmt/tunnel.conf.bak && sed -i 's/^VPS_HOST=.*/VPS_HOST=154.36.137.54/; s/^VPS_PORT=.*/VPS_PORT=28722/' /etc/routermgmt/tunnel.conf && /etc/init.d/mgmt-tunnel restart && sleep 10 && curl -sk -m 6 -o /dev/null -w 'RESULT=%{http_code}' https://127.0.0.1:18443/api/v1/health"

Write-Host "=== Router Emergency Fix v3 ==="
Write-Host ""
Write-Host "When prompted: password is  [REDACTED]   (input is invisible, type it blind + Enter)"
Write-Host ""
ssh -o StrictHostKeyChecking=accept-new root@$router $cmd
Write-Host ""
Write-Host "RESULT=200 above = SUCCESS (mgmt restored via 66cloud, our side takes over)"
Write-Host "Other output / timeout = line to GIA also dead, screenshot this window"
Read-Host "Press Enter to close"
