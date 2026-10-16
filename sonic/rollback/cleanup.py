"""
sonic/rollback/cleanup.py
-------------------------
Cleanup helpers for HITL session artifacts.

Round cleanup removes t2/t3 snapshots so the next retry starts fresh.
Session cleanup removes the whole session directory once the operator has
accepted the change or the run aborts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sonic.rollback.paths import session_dir


def _safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True


def cleanup_round_artifacts(session_id: str) -> dict:
    """
    Remove transient round artifacts that should not survive a retry.
    """
    session = session_dir(session_id)
    removed = []
    for label in ("t2", "t3"):
        if _safe_rmtree(session / label):
            removed.append(label)
    return {"session_dir": session, "removed": removed}


def cleanup_session(session_id: str) -> dict:
    """
    Remove the entire session directory.

    Call this only after the checkpoint has already been deleted.
    """
    session = session_dir(session_id)
    deleted = _safe_rmtree(session)
    return {"session_dir": session, "deleted": deleted}