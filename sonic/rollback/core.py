"""
sonic/rollback/core.py
----------------------
Session-level rollback helpers for the HITL loop.

This module owns the concrete flow used by the orchestrator:
  - create_baseline() once per session
  - capture_round() for t2/t3 snapshots
  - rollback_to_baseline() when a human rejects a round
  - verify_rollback() to ensure the rollback actually restored the
    baseline before another Ansible attempt is allowed
  - delete_checkpoint() during cleanup
"""

from __future__ import annotations

from pathlib import Path

from sonic.hitl.feedback import build_diff_bundle
from sonic.rollback.baseline import create_baseline as _create_baseline
from sonic.rollback.capture import capture, delete_dir
from sonic.rollback.errors import RollbackError
from sonic.rollback.frr_delta import build_vtysh_commands, compute_frr_plan
from sonic.rollback.paths import checkpoint_name, session_dir
from sonic.switch.ssh_client import get_frr_config, run_command


def _checkpoint_file(session_id: str) -> Path:
    return session_dir(session_id) / "checkpoint_name.txt"


def _read_checkpoint(session_id: str) -> str:
    checkpoint_file = _checkpoint_file(session_id)
    if checkpoint_file.exists():
        return checkpoint_file.read_text(encoding="utf-8").strip()
    return checkpoint_name(session_id)


def _run_or_raise(ip: str, switch: str, command: str, *, stage: str, completed: list[str] | None = None) -> None:
    print(f"[rollback] {switch} ({ip}): {command}")
    success, out, err = run_command(ip, command)

    if out:
        print(f"    stdout: {out}")
    if err:
        print(f"    stderr: {err}")

    if not success:
        raise RollbackError(
            "command failed",
            switch=switch,
            stage=stage,
            command=command,
            detail=err or out,
            completed=completed,
        )


def _is_missing_checkpoint_error(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "not found",
            "does not exist",
            "no such",
            "unknown checkpoint",
            "checkpoint missing",
        )
    )


def create_baseline(session_id: str, mgmt_ips: dict) -> dict:
    """Create the immutable baseline for a HITL session."""
    session = session_dir(session_id)
    actual_t1_dir = session / "actual_t1"
    delete_dir(actual_t1_dir)
    return _create_baseline(session_id, mgmt_ips)


def capture_round(session_id: str, round_name: str, mgmt_ips: dict) -> Path:
    """
    Capture a fresh session snapshot for a given round label.

    round_name is usually "t2" or "t3". The directory is overwritten on
    each call, which matches the user's requested behavior.
    """
    target_dir = session_dir(session_id) / round_name
    delete_dir(target_dir)
    capture(target_dir, mgmt_ips)
    return target_dir

def rollback_to_baseline(session_id: str, mgmt_ips: dict) -> dict:
    session = session_dir(session_id)
    checkpoint = _read_checkpoint(session_id)
    baseline_dir = session / "actual_t1"

    if not baseline_dir.exists():
        raise RollbackError(
            "baseline directory missing",
            stage="rollback",
            detail=str(baseline_dir),
        )

    per_switch = {}
    done: list[str] = []
    for name, ip in sorted(mgmt_ips.items()):
        rollback_cmd = f"sudo config rollback {checkpoint}"
        _run_or_raise(ip, name, rollback_cmd, stage="config_rollback", completed=done)

        baseline_frr_path = baseline_dir / "raw" / f"{name}.frr.txt"
        if not baseline_frr_path.exists():
            raise RollbackError(
                "missing baseline FRR capture",
                switch=name,
                stage="frr_fix",
                detail=str(baseline_frr_path),
                completed=done,
            )

        baseline_frr = baseline_frr_path.read_text(encoding="utf-8")
        current_frr = get_frr_config(ip)
        plan = compute_frr_plan(baseline_frr, current_frr)
        cmds = build_vtysh_commands(plan)

        if cmds:
            vtysh_args = " ".join(f'-c "{cmd}"' for cmd in cmds)
            docker_cmd = f"docker exec bgp vtysh {vtysh_args}"
            _run_or_raise(ip, name, docker_cmd, stage="frr_fix", completed=done)

        # `config rollback` only mutates the running ConfigDB (Redis).
        # Capture reads /etc/sonic/config_db.json, and the applied playbook
        # ran `config save -y`, so without persisting the rollback here the
        # on-disk file (and thus t3) still shows the reverted change.
        _run_or_raise(ip, name, "sudo config save -y", stage="config_save", completed=done)

        per_switch[name] = {"config_rolled_back": True, "frr_commands": cmds}
        done.append(name)

    return {"session_dir": session, "checkpoint": checkpoint, "per_switch": per_switch}

def verify_rollback(session_id: str, mgmt_ips: dict, all_switches: list[str] | None = None) -> dict:
    session = session_dir(session_id)
    baseline_dir = session / "actual_t1"
    if not baseline_dir.exists():
        raise RollbackError("baseline directory missing", stage="verify", detail=str(baseline_dir))

    t3_dir = capture_round(session_id, "t3", mgmt_ips)
    bundle = build_diff_bundle(baseline_dir=baseline_dir, current_dir=t3_dir, all_switches=all_switches)

    if bundle.facts:
        raise RollbackError(
            "live state still differs from the baseline",
            stage="verify",
            detail=bundle.human_diff or f"{len(bundle.facts)} fact(s) differ",
        )

    return {"verified": True, "t3_dir": t3_dir, "diff_bundle": bundle}


def delete_checkpoint(session_id: str, mgmt_ips: dict) -> dict:
    """
    Best-effort checkpoint cleanup. SONiC CLI spelling varies across images,
    so try a few likely delete forms before giving up.
    """
    checkpoint = _read_checkpoint(session_id)
    candidates = [
        f"sudo config delete-checkpoint {checkpoint}",
        f"sudo config checkpoint del {checkpoint}",
        f"sudo config checkpoint remove {checkpoint}",
        f"sudo config rollback delete {checkpoint}",
    ]
    # candidates = [
    #     f"sudo config delete-checkpoint {checkpoint}"
    # ]

    errors = {}
    for name, ip in sorted(mgmt_ips.items()):
        last_error = None
        for cmd in candidates:
            success, out, err = run_command(ip, cmd)
            if success:
                last_error = None
                break
            last_error = err or out
            errors[cmd] = last_error
            if _is_missing_checkpoint_error(last_error):
                last_error = None
                break

        if last_error:
            raise RollbackError(
                f"{name}: failed to delete checkpoint {checkpoint!r}. Last error: {last_error}"
            )

    checkpoint_file = _checkpoint_file(session_id)
    if checkpoint_file.exists():
        checkpoint_file.unlink()

    return {"checkpoint": checkpoint, "deleted": True, "errors": errors}