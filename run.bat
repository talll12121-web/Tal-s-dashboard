@echo off
REM -- Trading Dashboard launcher (Windows) --
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing / updating dependencies...
pip install -q -r requirements.txt

echo.
echo Starting Trading Dashboard at http://localhost:5000
echo (Make sure IB Gateway / TWS is running for live data.)
echo.
python -m backend.app
pause
