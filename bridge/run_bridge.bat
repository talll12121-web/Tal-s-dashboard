@echo off
REM -- Desktop IBKR Bridge launcher (Windows) --
REM Run this on your trading PC while IB Gateway / TWS is open.
cd /d "%~dp0\.."

if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

echo.
echo Starting IBKR bridge -> pushing live data to your cloud dashboard.
echo (Keep this window open while trading. Close it when done.)
echo.
python -m bridge.ibkr_bridge
pause
