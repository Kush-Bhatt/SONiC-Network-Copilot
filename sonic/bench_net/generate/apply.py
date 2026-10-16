"""
sonic/bench_net/apply.py
-------------------------
Standalone Ansible-playbook apply for bench_net dataset creation.
Deliberately independent of sonic/ansible/runner.py (own remote work
dir, no langchain @tool wrapper, takes playbook text directly) so
dataset authoring can never collide with a real pipeline run sharing
the same lab box, and has no import-time dependency on the pipeline.
"""
import re
import paramiko

from sonic.bench_net.config import GNS3_HOST, GNS3_JUMP_USER, GNS3_JUMP_PASS, SSH_PASSWORD

REMOTE_WORK_DIR = "/tmp/bench-net-apply"

REMOTE_ANSIBLE_CFG = """[defaults]
host_key_checking = False
interpreter_python = /usr/bin/python3

[ssh_connection]
ssh_args = -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
"""

_RECAP_LINE_RE = re.compile(
    r"^(\S+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+"
    r"failed=(\d+)\s+skipped=(\d+)\s+rescued=(\d+)\s+ignored=(\d+)",
    re.MULTILINE,
)


def _real_failure_occurred(stdout: str) -> bool:
    """Same PLAY-RECAP-aware success logic as the pipeline's runner —
    exit code alone is unreliable because ignore_errors tasks still
    flip rc non-zero. See sonic/ansible/runner.py for the original;
    duplicated here on purpose, not imported (bench_net stays
    independent)."""
    matches = _RECAP_LINE_RE.findall(stdout)
    if not matches:
        return True  # no recap at all — crash before the play ran, treat as real failure
    for host, ok, changed, unreachable, failed, skipped, rescued, ignored in matches:
        if int(unreachable) > 0:
            return True
        if int(failed) > int(ignored):
            return True
    return False


def apply_playbook(playbook_yaml: str, inventory_text: str) -> dict:
    """
    Uploads playbook_yaml + inventory_text to the remote GNS3 host and
    runs ansible-playbook there over SSH.

    Args:
      playbook_yaml   : full playbook YAML as a string (pasted by intern/Claude)
      inventory_text  : full hosts.ini content as a string

    Returns: {"success": bool, "stdout": str, "stderr": str}
    """
    remote_playbook  = f"{REMOTE_WORK_DIR}/ansible_playbook.yaml"
    remote_inventory = f"{REMOTE_WORK_DIR}/hosts.ini"
    remote_cfg       = f"{REMOTE_WORK_DIR}/ansible.cfg"

    print(f"[apply] Connecting to remote server {GNS3_HOST}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(GNS3_HOST, username=GNS3_JUMP_USER, password=GNS3_JUMP_PASS)

    try:
        _, mkdir_out, _ = client.exec_command(f"mkdir -p {REMOTE_WORK_DIR}")
        mkdir_out.channel.recv_exit_status()  # block until mkdir completes before sftp

        sftp = client.open_sftp()
        with sftp.open(remote_playbook, "w") as f:
            f.write(playbook_yaml)
        print(f"[apply] Uploaded playbook -> {remote_playbook}")
        with sftp.open(remote_inventory, "w") as f:
            f.write(inventory_text)
        print(f"[apply] Uploaded hosts.ini -> {remote_inventory}")
        with sftp.open(remote_cfg, "w") as f:
            f.write(REMOTE_ANSIBLE_CFG)
        print(f"[apply] Uploaded ansible.cfg -> {remote_cfg}")
        sftp.close()

        cmd = (
            f"cd {REMOTE_WORK_DIR} && "
            f"LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 "
            f"PYTHONWARNINGS=ignore::DeprecationWarning "
            f"ANSIBLE_CONFIG={remote_cfg} "
            f"sshpass -p '{SSH_PASSWORD}' "
            f"ansible-playbook -i {remote_inventory} {remote_playbook}"
        )
        print(f"[apply] Executing ansible-playbook on {GNS3_HOST}...")
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
        print(f"[apply] Cleaned up {REMOTE_WORK_DIR} on remote server")
