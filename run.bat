@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Creating virtual env...
    py -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
echo.
echo Starting HubSpot Rep Dashboard at http://localhost:5050
echo Press Ctrl+C to stop.
echo.
start "" http://localhost:5050
python app.py
