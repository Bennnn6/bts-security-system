@echo off
title Smart BTS Gate Security System
echo ====================================================
echo Menjalankan Smart BTS Gate Security System
echo Virtual Environment: venv (Python 3.11)
echo ====================================================
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment 'venv' tidak ditemukan!
    pause
    exit /b 1
)
call .\venv\Scripts\activate.bat
python run.py %*
pause
