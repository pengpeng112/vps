@echo off
title Fix Agent URL
echo Password: [REDACTED] (type blind)
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "sh -s" < "%~dp0fix-agent-url.txt"
echo.
pause
