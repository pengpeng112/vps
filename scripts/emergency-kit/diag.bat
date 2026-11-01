@echo off
title R8 Diagnostic
echo ==========================================================
echo  Password: [REDACTED]   (type it blind when asked)
echo ==========================================================
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "sh -s" < "%~dp0diag-cmds.txt"
echo.
echo ----------------------------------------------------------
echo Copy ALL output above and send back
echo ----------------------------------------------------------
pause
