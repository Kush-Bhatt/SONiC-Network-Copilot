"""
sonic/rollback/baseline.py
--------------------------
Creates the immutable rollback baseline for one HITL session.

Per switch:

  1. sudo config checkpoint <name>

Then:

  2. capture(actual_t1)

Finally:

  3. persist checkpoint_name.txt

Called exactly once per session.
"""


from sonic.rollback.capture import capture
from sonic.rollback.errors import RollbackError
from sonic.rollback.paths import checkpoint_name, session_dir
from sonic.switch.ssh_client import run_command

def create_baseline(session_id: str, mgmt_ips: dict) -> dict:
    session = session_dir(session_id)
    session.mkdir(parents=True, exist_ok=True)

    checkpoint = checkpoint_name(session_id)

    for name, ip in sorted(mgmt_ips.items()):
        # `config checkpoint` snapshots the running ConfigDB (Redis), but the
        # actual_t1 capture below reads /etc/sonic/config_db.json from disk.
        # If Redis holds unsaved state, the two diverge and verify_rollback()
        # later flags a phantom diff. Persist Redis -> disk first so the
        # baseline snapshot matches the checkpoint we roll back to.
        for cmd in ("sudo config save -y", f"sudo config checkpoint {checkpoint}"):
            print(f"[rollback] {name} ({ip}): {cmd}")

            success, out, err = run_command(ip, cmd)

            if out:
                print(f"    stdout: {out}")

            if err:
                print(f"    stderr: {err}")

            if not success:
                raise RollbackError(
                    f"{name}: command failed: {cmd}: {err or out}"
                )

    actual_t1_dir = session / "actual_t1"

    capture(actual_t1_dir, mgmt_ips)

    (session / "checkpoint_name.txt").write_text(
        checkpoint,
        encoding="utf-8",
    )

    return {
        "session_dir": session,
        "checkpoint": checkpoint,
        "actual_t1_dir": actual_t1_dir,
    }