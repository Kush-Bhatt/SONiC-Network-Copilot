from sonic.hitl.facts import extract_facts, extract_raw_diff
from sonic.rollback.capture import capture
from sonic.rollback.errors import RollbackError
from sonic.rollback.frr_delta import build_vtysh_commands, compute_frr_plan
from sonic.rollback.paths import session_dir
from sonic.rollback.capture import delete_dir
from sonic.switch.ssh_client import (
    get_frr_config,
    run_command,
)

def _run_or_raise(ip: str, switch: str, command: str):
    print(f"[rollback] {switch} ({ip}): {command}")

    success, out, err = run_command(ip, command)

    if out:
        print(f"    stdout: {out}")

    if err:
        print(f"    stderr: {err}")

    if not success:
        raise RollbackError(
            f"{switch}: command failed: {command}\n{err or out}"
        )


def _rollback_frr(name: str, ip: str, baseline_frr: str):

    current_frr = get_frr_config(ip)

    plan = compute_frr_plan(
        baseline_frr,
        current_frr,
    )

    cmds = build_vtysh_commands(plan)

    if not cmds:
        return

    vtysh_args = " ".join(f'-c "{c}"' for c in cmds)

    docker_cmd = f"docker exec bgp vtysh {vtysh_args}"

    _run_or_raise(ip, name, docker_cmd)


def recover(session_id: str, mgmt_ips: dict) -> dict:

    session = session_dir(session_id)

    actual_t1 = session / "actual_t1"

    t3 = session / "t3"

    checkpoint_file = session / "checkpoint_name.txt"

    if not checkpoint_file.exists():
        raise RollbackError(
            f"Missing checkpoint file: {checkpoint_file}"
        )

    checkpoint = checkpoint_file.read_text(encoding="utf-8").strip()

    for name, ip in sorted(mgmt_ips.items()):
        _run_or_raise(
            ip,
            name,
            f"sudo config rollback {checkpoint}",
        )


    for name, ip in sorted(mgmt_ips.items()):
        baseline_frr = (
            actual_t1
            / "raw"
            / f"{name}.frr.txt"
        ).read_text(encoding="utf-8")

        _rollback_frr(
            name,
            ip,
            baseline_frr,
        )


    for name, ip in sorted(mgmt_ips.items()):
        _run_or_raise(
            ip,
            name,
            "sudo config save -y",
        )



    delete_dir(t3)
    capture(t3, mgmt_ips)

    facts = extract_facts(
        actual_t1,
        t3,
    )

    raw_diff = extract_raw_diff(
        actual_t1,
        t3,
    )

    verified = not facts

    return {
        "verified": verified,
        "facts": facts,
        "raw_diff": raw_diff,
        "t3_dir": t3,
    }