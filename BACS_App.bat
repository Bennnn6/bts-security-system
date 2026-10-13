@echo off
cd /d "%~dp0"
title BACS Enterprise - Smart BTS Security System

echo ========================================================
echo  BACS ENTERPRISE - SMART BTS SECURITY SYSTEM
echo  Memulai Aplikasi Desktop Mandiri...
echo ========================================================

if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0venv\Scripts\python.exe"
) else if exist ".\venv\Scripts\python.exe" (
    set "PY_EXE=.\venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" desktop_app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================================
    echo  [!] Terjadi kendala saat membuka aplikasi desktop.
    echo      Kode status: %ERRORLEVEL%
    echo ========================================================
    pause
)
