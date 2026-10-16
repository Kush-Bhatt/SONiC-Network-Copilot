"""
sonic/rollback/paths.py
--------------------------
Shared path/naming helpers — one place so baseline/run/verify/cleanup
never disagree on where a session's data lives or what its checkpoint
is called.
"""
from pathlib import Path

from sonic.config.settings import BASE_DIR

SESSIONS_DIR = BASE_DIR / "snapshots" / "sessions"


def checkpoint_name(session_id: str) -> str:
    return f"hitl_{session_id}"


def session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id
