"""
Authentication
==============

Two gates:
  * A password login for you (session cookie) - protects the whole dashboard
    once it's on the public internet.
  * A bridge token - a shared secret the desktop IBKR agent uses to push live
    data, so random people can't POST fake quotes/fills.

Config (env):
  APP_PASSWORD   the password you type to log in. If unset, login is DISABLED
                 (fine for purely-local use; set it before deploying to cloud).
  SECRET_KEY     Flask session signing key. Auto-generated if unset (local only;
                 set a fixed value in the cloud so sessions survive restarts).
  BRIDGE_TOKEN   shared secret for the desktop bridge's push endpoints.
"""

from __future__ import annotations
import os
import secrets
from functools import wraps

from flask import session, request, jsonify, redirect

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

LOGIN_REQUIRED = bool(APP_PASSWORD)


def check_password(pw: str) -> bool:
    return LOGIN_REQUIRED and secrets.compare_digest(pw or "", APP_PASSWORD)


def is_logged_in() -> bool:
    return (not LOGIN_REQUIRED) or session.get("auth") is True


def require_login(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not is_logged_in():
            if request.path.startswith("/api/"):
                return jsonify({"error": "auth required"}), 401
            return redirect("/login")
        return fn(*a, **k)
    return wrapper


def require_bridge_token(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not BRIDGE_TOKEN:
            return jsonify({"error": "bridge token not configured on server"}), 503
        token = request.headers.get("X-Bridge-Token", "")
        if not secrets.compare_digest(token, BRIDGE_TOKEN):
            return jsonify({"error": "invalid bridge token"}), 403
        return fn(*a, **k)
    return wrapper
