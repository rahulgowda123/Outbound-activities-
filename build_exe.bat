@echo off
REM Builds RepDashboard.exe via PyInstaller.
REM Output: dist\RepDashboard.exe (single-file)

cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual env...
    py -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing build deps...
pip install --quiet --disable-pip-version-check -r requirements.txt
pip install --quiet --disable-pip-version-check pyinstaller

echo.
echo Building RepDashboard.exe ...
echo (this takes 1-2 minutes the first time)
echo.

pyinstaller --noconfirm --onefile --clean ^
    --name RepDashboard ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --hidden-import dotenv ^
    --collect-submodules flask ^
    launcher.py

if exist dist\RepDashboard.exe (
    echo.
    echo ============================================
    echo   Build OK: dist\RepDashboard.exe
    echo ============================================
    echo.
    echo Next steps:
    echo   1. Copy dist\RepDashboard.exe wherever you want it.
    echo   2. Put a .env file in the SAME folder as the .exe.
    echo      One line:  HUBSPOT_TOKEN=pat-na1-...
    echo   3. Double-click the .exe to run.
    echo.
) else (
    echo.
    echo BUILD FAILED. See messages above.
    echo.
)
pause
