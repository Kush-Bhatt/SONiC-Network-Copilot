"""
sonic/rollback/
--------------------------
ConfigDB + FRR rollback mechanism for the HITL self-healing loop.
Consolidates the ConfigDB + FRR rollback mechanism into its own
top-level package (no longer under sonic/hitl/ — rollback is
infrastructure state control, not a human-review concern), alongside
the pieces it's built from:

  errors.py     — RollbackError (hard-stop signal, never partial rollback)
  frr_delta.py  — parse_bgp_block / build_vtysh_commands / compute_frr_plan
                  (FRR add/remove delta computation — frr-reload is NOT
                  available on this SONiC image, confirmed missing
                  frr-pythontools, so this is our own proven mechanism)
  paths.py      — checkpoint_name() / session_dir() naming helpers
  capture.py    — capture_full() / delete_dir() snapshot helpers
  core.py       — create_baseline() / rollback_to_baseline() /
                  verify_rollback() / delete_checkpoint()

sonic/tests/manual_test_frr_rollback.py remains a SEPARATE, standalone
experimentation harness — its FRR logic is deliberately duplicated, not
imported, so it can keep evolving independently for quick experiments
without touching the real pipeline's source of truth here.
"""
from sonic.rollback.cleanup import cleanup_round_artifacts, cleanup_session
from sonic.rollback.errors import RollbackError
from sonic.rollback.frr_delta import parse_bgp_block, build_vtysh_commands, compute_frr_plan
from sonic.rollback.paths import checkpoint_name, session_dir, SESSIONS_DIR
from sonic.rollback.capture import capture_full, delete_dir
from sonic.rollback.core import (
    create_baseline,
    capture_round,
    rollback_to_baseline,
    verify_rollback,
    delete_checkpoint,
)

__all__ = [
    "RollbackError",
    "parse_bgp_block",
    "build_vtysh_commands",
    "compute_frr_plan",
    "checkpoint_name",
    "session_dir",
    "SESSIONS_DIR",
    "capture_full",
    "delete_dir",
    "cleanup_round_artifacts",
    "cleanup_session",
    "create_baseline",
    "capture_round",
    "rollback_to_baseline",
    "verify_rollback",
    "delete_checkpoint",
]