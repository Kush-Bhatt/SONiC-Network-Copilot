import os
import re

import paramiko

from langchain_core.tools import tool

from sonic.config.settings import (
    ANSIBLE_DIR,
    GNS3_HOST, GNS3_JUMP_USER, GNS3_JUMP_PASS,
    SSH_USERNAME, SSH_PASSWORD
)

REMOTE_WORK_DIR = "/tmp/ans-gns-bat"

# UserKnownHostsFile=/dev/null is load-bearing, not belt-and-braces.
# StrictHostKeyChecking=no only auto-accepts *unknown* hosts; when a host key has
# *changed* OpenSSH still refuses password and keyboard-interactive auth ("Password
# authentication is disabled to avoid man-in-the-middle attacks"), which breaks the
# sshpass auth this runner depends on. GNS3 switches get rebuilt and recycle their
# IPs, so a changed key is normal here. Pointing known_hosts at /dev/null means
# every switch is always "unknown" rather than "changed", keeping password auth alive.
REMOTE_ANSIBLE_CFG = """[defaults]
host_key_checking = False
interpreter_python = /usr/bin/python3

[ssh_connection]
ssh_args = -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
"""

# _UNREACHABLE_RE = re.compile(r"fatal: \[([^\]]+)\]: UNREACHABLE!")

_UNREACHABLE_RE = re.compile(r"fatal: \[([^\]]+)\]: UNREACHABLE!")

_RECAP_LINE_RE = re.compile(
    r"^(\S+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+"
    r"failed=(\d+)\s+skipped=(\d+)\s+rescued=(\d+)\s+ignored=(\d+)",
    re.MULTILINE,
)


def _real_failure_occurred(stdout: str) -> bool:
    """True only if some host has a failure NOT covered by ignore_errors.

    Ansible's process exit code is non-zero whenever ANY task fails on ANY
    host, even tasks marked ignore_errors: true (ignoring an error changes
    whether the play continues, not the final exit code). Relying on rc
    alone would make every benign 'already exists' / expected warning look
    like a real failure. The PLAY RECAP line is the authoritative per-host
    summary: if failed <= ignored for every host, every failure that
    happened was one we explicitly chose to ignore, so this is not a real
    failure.
    """
    matches = _RECAP_LINE_RE.findall(stdout)
    if not matches:
        # No recap at all (e.g. a crash before the play ran) — treat
        # conservatively as a real failure, do not guess.
        return True
    for host, ok, changed, unreachable, failed, skipped, rescued, ignored in matches:
        if int(unreachable) > 0:
            return True
        if int(failed) > int(ignored):
            return True
    return False


def infrastructure_failure_reason(result: dict | None) -> str | None:
    """Distinguish 'the lab is broken' from 'the playbook is wrong'.

    A host Ansible could not even connect to is an infrastructure fault: no
    amount of regenerating YAML will fix an SSH/auth/network problem, so it must
    not be fed to the LLM repair loop on the same footing as a real config
    defect. Returns a human-readable reason, or None if this looks like a
    genuine playbook/config failure that IS worth retrying.
    """
    if not result or result.get("success"):
        return None
    # A scope-guard rejection is a real defect in the generated playbook — the
    # model can and should fix that one.
    if result.get("guard_rejected"):
        return None

    blob = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    if "UNREACHABLE!" not in blob and '"unreachable": true' not in blob:
        return None

    hosts = sorted(set(_UNREACHABLE_RE.findall(blob)))
    reason = f"Ansible could not reach {', '.join(hosts) if hosts else 'one or more hosts'}."

    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in blob:
        reason += (
            "\nThe switch's SSH host key changed (rebuilt VM / recycled IP) and the "
            "jump host still trusts the old one, so OpenSSH disabled password auth.\n"
            "Fix on the jump host:  ssh-keygen -f ~/.ssh/known_hosts -R <switch_ip>"
        )
    elif "Permission denied" in blob:
        reason += "\nSSH authentication was refused — check NODE_SSH_USER / NODE_SSH_PASS."
    else:
        reason += "\nCheck that the switch is powered on and reachable from the jump host."
    return reason


def _get_remote_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(GNS3_HOST, username=GNS3_JUMP_USER, password=GNS3_JUMP_PASS)
    return client

def _build_remote_hosts_ini(local_hosts_ini_path):
    content = local_hosts_ini_path.read_text()
    lines = [l for l in content.splitlines()
             if "ProxyCommand" not in l and "ssh_common_args" not in l]
    return "\n".join(lines) + "\n"

@tool
def run_ansible_playbook(playbook_name: str = "ansible_playbook.yaml", inventory_name: str = "hosts.ini") -> dict:
    """
    Uploads the playbook and inventory to the remote GNS3 host, runs
    ansible-playbook there over SSH, and returns the result.

    Args:
      playbook_name  : filename of the playbook to run, relative to ANSIBLE_DIR
                       (default "ansible_playbook.yaml" — the file AnsibleAgent writes to)
      inventory_name : filename of the inventory to use, relative to ANSIBLE_DIR
                       (default "hosts.ini" — written by build_inventory())

    Returns:
      dict with keys:
        success : bool — True if ansible-playbook exited 0
        stdout  : str  — captured standard output
        stderr  : str  — captured standard error
    """

    playbook_path  = ANSIBLE_DIR / playbook_name
    inventory_path = ANSIBLE_DIR / inventory_name

    if not os.path.exists(playbook_path):
        raise FileNotFoundError(f"Playbook missing at {playbook_path}")
    if not os.path.exists(inventory_path):
        raise FileNotFoundError(f"Inventory missing at {inventory_path}")

    remote_playbook  = f"{REMOTE_WORK_DIR}/ansible_playbook.yaml"
    remote_inventory = f"{REMOTE_WORK_DIR}/hosts.ini"
    remote_cfg       = f"{REMOTE_WORK_DIR}/ansible.cfg"

    print(f"[*] Connecting to remote server {GNS3_HOST}...")
    client = _get_remote_client()

    try:
        # ensure work dir exists. exec_command is async, so block on the exit
        # status before uploading — otherwise the SFTP put below can race ahead
        # of mkdir and fail with a remote ENOENT (no such file/directory).
        _, mkdir_out, _ = client.exec_command(f"mkdir -p {REMOTE_WORK_DIR}")
        mkdir_out.channel.recv_exit_status()

        sftp = client.open_sftp()

        # upload playbook
        sftp.put(str(playbook_path), remote_playbook)
        print(f"[*] Uploaded playbook → {remote_playbook}")

        # upload hosts.ini (no ProxyCommand — remote server reaches switches directly)
        remote_hosts_content = _build_remote_hosts_ini(inventory_path)
        with sftp.open(remote_inventory, "w") as f:
            f.write(remote_hosts_content)
        print(f"[*] Uploaded hosts.ini → {remote_inventory}")

        # upload ansible.cfg
        with sftp.open(remote_cfg, "w") as f:
            f.write(REMOTE_ANSIBLE_CFG)
        print(f"[*] Uploaded ansible.cfg → {remote_cfg}")

        sftp.close()

        # run ansible-playbook on remote server with sshpass for switch password
        cmd = (
            f"cd {REMOTE_WORK_DIR} && "
            f"LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 "
            f"PYTHONWARNINGS=ignore::DeprecationWarning "
            f"ANSIBLE_CONFIG={remote_cfg} "
            f"sshpass -p '{SSH_PASSWORD}' "
            f"ansible-playbook -i {remote_inventory} {remote_playbook}"
        )

        print(f"[*] Executing ansible-playbook on remote server {GNS3_HOST}...")
        stdin, stdout, stderr = client.exec_command(cmd)
        out = stdout.read().decode()
        err = stderr.read().decode()
        rc  = stdout.channel.recv_exit_status()

        print("\n--- Ansible Standard Output ---")
        print(out)
        if err:
            print("\n--- Ansible Error Output ---")
            print(err)

        success = (rc == 0) or (not _real_failure_occurred(out))
        return {"success": success, "stdout": out, "stderr": err}

    finally:
        client.exec_command(f"rm -rf {REMOTE_WORK_DIR}")
        client.close()
        print(f"[*] Cleaned up {REMOTE_WORK_DIR} on remote server")