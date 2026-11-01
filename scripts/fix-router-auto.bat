@echo off
title Router Fix v7 FULL-AUTO
set PW=[REDACTED]

if not exist "%~dp0plink.exe" (
  echo Downloading plink...
  curl -fsSL -o "%~dp0plink.exe" https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe
)
if not exist "%~dp0plink.exe" (
  echo Download failed. Run once with internet working, or use manual version.
  pause
  exit /b
)

echo [1/2] Trusting router host key (one time)...
echo y | "%~dp0plink.exe" -ssh -pw %PW% root@192.168.132.1 exit >nul 2>&1

echo [2/2] Running fix (about 30 seconds)...
"%~dp0plink.exe" -ssh -pw %PW% -batch root@192.168.132.1 -m "%~dp0fix-cmds.txt"
if errorlevel 1 (
  echo.
  echo AUTO MODE FAILED - likely host key step. Run once more.
  echo If it fails again: tell us, we give you the type-password version.
)
echo.
echo ----------------------------------------------------------
echo Success = lines: DEVICE / HEALTH: 200 / EGRESS: 154.36.137.54
echo ----------------------------------------------------------
pause
