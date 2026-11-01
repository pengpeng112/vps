@echo off
title R8 Clock + Heartbeat Fix
echo Password: [REDACTED] (type blind)
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "sh -s" < "%~dp0fix-r8-clock.txt"
echo.
pause
