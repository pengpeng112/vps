@echo off
title Router Fix v8
echo ==========================================================
echo  Password: [REDACTED]   (type it blind when asked)
echo ==========================================================
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "sh -s" < "%~dp0fix-cmds.txt"
echo.
echo ----------------------------------------------------------
echo HEALTH: 200 + EGRESS: 154.36.139.155 + AGENT: 1 = SUCCESS
echo ----------------------------------------------------------
pause
