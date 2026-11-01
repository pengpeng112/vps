@echo off
title Fix Router Remote
echo ============================================================
echo  Password is:  [REDACTED]   (type it blind when asked)
echo ============================================================
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "cp /etc/routermgmt/tunnel.conf /etc/routermgmt/tunnel.conf.bak && sed -i 's/^VPS_HOST=.*/VPS_HOST=154.36.137.54/; s/^VPS_PORT=.*/VPS_PORT=28722/' /etc/routermgmt/tunnel.conf && /etc/init.d/mgmt-tunnel restart && sleep 10 && curl -sk -m 6 -o /dev/null -w 'RESULT=%%{http_code}' https://127.0.0.1:18443/api/v1/health"
echo.
echo RESULT=200 = OK, remote restored. Other output: screenshot it.
pause
