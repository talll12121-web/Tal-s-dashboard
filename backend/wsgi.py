"""WSGI entry point for production servers (gunicorn).

Usage:  gunicorn backend.wsgi:app
In cloud deployment IB_ENABLED should be false (the desktop bridge pushes
live data), so importing this does not try to open an IBKR socket.
"""
from .app import app  # noqa: F401
