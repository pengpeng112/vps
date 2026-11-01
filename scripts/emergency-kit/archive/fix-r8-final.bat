@echo off
title R8 Final Fix
echo ==========================================================
echo  Password: [REDACTED]   (type it blind when asked)
echo ==========================================================
echo.
ssh -o StrictHostKeyChecking=no root@192.168.132.1 "sh -s" < "%~dp0fix-r8-final.txt"
echo.
echo ----------------------------------------------------------
echo SUCCESS = p43: 200 + agent: 1 + ALL_DONE
echo ----------------------------------------------------------
pause
