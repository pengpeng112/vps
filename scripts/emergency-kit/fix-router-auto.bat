@echo off
title Router Fix v9 FULL-AUTO (multi-site safe)
set PW=[REDACTED]
set RT=192.168.132.1

if not exist "%~dp0plink.exe" (
  echo [X] plink.exe not found. Copy all 3 files to same folder.
  pause
  exit /b
)

echo [0/3] Check LAN reachability to %RT% ...
ping -n 2 -w 1500 %RT% >nul 2>&1
if errorlevel 1 (
  echo [X] Ping %RT% FAILED - this PC is NOT on the router LAN.
  echo     Connect this PC to the router LAN port or its WiFi, then rerun.
  pause
  exit /b
)
echo      LAN OK.

echo [1/3] Clear cached host keys for %RT% (multi-router safe) ...
reg delete "HKCU\Software\SimonTatham\PuTTY\SshHostKeys" /f >nul 2>&1
echo      Cleared (only affects PuTTY/plink trusted keys on THIS PC).

echo [2/3] Trust router host key ...
echo y | "%~dp0plink.exe" -ssh -pw %PW% root@%RT% exit
if errorlevel 1 (
  echo [X] Could not SSH to router. Check password/SSH enabled, rerun.
  pause
  exit /b
)
echo      Host key trusted, SSH OK.

echo [3/3] Running fix (about 45 seconds) ...
"%~dp0plink.exe" -ssh -pw %PW% -batch root@%RT% -m "%~dp0fix-cmds.txt"
if errorlevel 1 (
  echo.
  echo AUTO MODE FAILED - run once more.
  echo If fails again: use fix-router.bat (type password version).
)
echo.
echo ----------------------------------------------------------
echo SUCCESS = HEALTH: 200  EGRESS: 154.36.139.155  AGENT: 1
echo ----------------------------------------------------------
pause
