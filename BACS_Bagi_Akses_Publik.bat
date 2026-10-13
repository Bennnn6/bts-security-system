@echo off
cd /d "%~dp0"
title BACS Enterprise - Bagi Akses Online Publik

if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" share_online.py
pause
