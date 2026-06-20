#!/usr/bin/env bash
# -- Trading Dashboard launcher (macOS / Linux) --
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installing / updating dependencies..."
pip install -q -r requirements.txt

echo
echo "Starting Trading Dashboard at http://localhost:5000"
echo "(Make sure IB Gateway / TWS is running for live data.)"
echo
python -m backend.app
