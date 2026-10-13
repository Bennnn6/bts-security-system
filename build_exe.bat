@echo off
cd /d "%~dp0"
title BACS - Build Standalone Windows EXE
echo ========================================================
echo  BACS ENTERPRISE - BUILD EXECUTABLE (.EXE)
echo  Mengompilasi aplikasi desktop utuh via PyInstaller
echo ========================================================

if not exist "%~dp0venv\Scripts\pyinstaller.exe" (
    echo [!] PyInstaller tidak ditemukan di venv.
    echo [*] Menginstal PyInstaller...
    "%~dp0venv\Scripts\pip.exe" install pyinstaller
)

echo [*] Memulai proses kompilasi aplikasi utuh BACS...
echo [*] Menyertakan template HTML, static assets, model Face ID, dan database...

"%~dp0venv\Scripts\pyinstaller.exe" ^
    --name="BACS_Enterprise" ^
    --onedir ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --add-data "app/templates;app/templates" ^
    --add-data "app/static;app/static" ^
    --add-data "venv/Lib/site-packages/face_recognition_models;face_recognition_models" ^
    --hidden-import=engineio.async_drivers.threading ^
    --hidden-import=jinja2 ^
    --hidden-import=sqlite3 ^
    --hidden-import=pywebview ^
    desktop_app.py

echo.
echo ========================================================
echo  KOMPILASI SELESAI!
echo  Aplikasi utuh .exe tersedia di: dist\BACS_Enterprise\BACS_Enterprise.exe
echo ========================================================
pause
