@echo off
chcp 65001 >nul
title 路由器应急修复
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix-router.ps1"
