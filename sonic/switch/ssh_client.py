"""
src/gns3/ssh_client.py
-----------------------
SSH into SONiC nodes via jump host using native two-hop paramiko.

Hop 1: paramiko transport  →  GNS3_JUMP_USER@GNS3_HOST (password)
Hop 2: open_channel        →  NODE_SSH_USER@192.168.200.x:22 (password)
Netmiko rides the channel socket from hop 2.

Replicates: ssh -J SONICtestbed@10.68.0.36 admin@192.168.200.x
"""
import re
import json
import socket
import paramiko
from netmiko import ConnectHandler
from sonic.config.settings import (
    SSH_USERNAME, SSH_PASSWORD,
    GNS3_HOST, GNS3_JUMP_USER, GNS3_JUMP_PASS,
)


def _open_jump_channel(target_ip: str, target_port: int = 22) -> paramiko.Channel:
    """
    Opens a paramiko transport to the jump host, then opens a direct-tcpip
    channel from the jump host to target_ip:target_port.
    Returns the channel (acts as a socket for the second paramiko session).
    """
    jump_sock = socket.create_connection((GNS3_HOST, 22), timeout=15)

    jump_transport = paramiko.Transport(jump_sock)
    jump_transport.start_client(timeout=15)
    jump_transport.auth_password(GNS3_JUMP_USER, GNS3_JUMP_PASS)

    if not jump_transport.is_authenticated():
        raise paramiko.AuthenticationException(
            f"Jump host auth failed: {GNS3_JUMP_USER}@{GNS3_HOST}"
        )

    channel = jump_transport.open_channel(
        "direct-tcpip",
        dest_addr=(target_ip, target_port),
        src_addr=("127.0.0.1", 0),
        timeout=15,
    )
    return channel


def get_sonic_config(ip_address: str, hostname: str = None) -> str:
    """
    SSH into a SONiC node via jump host and retrieve config_db.json.
    """
    channel = _open_jump_channel(ip_address, 22)

    device = {
        "device_type":      "linux",
        "host":             ip_address,
        "username":         SSH_USERNAME,
        "password":         SSH_PASSWORD,
        "sock":             channel,
        "timeout":          30,
        "session_timeout":  60,
        "conn_timeout":     30,
        "banner_timeout":   20,
        "auth_timeout":     20,
        "ssh_strict":       False,
        "system_host_keys": False,
        "alt_host_keys":    False,
        "use_keys":         False,
        "allow_agent":      False,
    }

    with ConnectHandler(**device) as conn:
        raw = conn.send_command("cat /etc/sonic/config_db.json", read_timeout=30)

    return _parse_config(raw, hostname)

def get_frr_config(ip_address: str) -> str:
    """
    SSH into a SONiC node via jump host and retrieve FRR running config via vtysh.
    Returns cleaned IOS-style config string ready for Batfish.
    """
    channel = _open_jump_channel(ip_address, 22)

    device = {
        "device_type":      "linux",
        "host":             ip_address,
        "username":         SSH_USERNAME,
        "password":         SSH_PASSWORD,
        "sock":             channel,
        "timeout":          30,
        "session_timeout":  60,
        "conn_timeout":     30,
        "banner_timeout":   20,
        "auth_timeout":     20,
        "ssh_strict":       False,
        "system_host_keys": False,
        "alt_host_keys":    False,
        "use_keys":         False,
        "allow_agent":      False,
    }

    with ConnectHandler(**device) as conn:
        raw = conn.send_command(
            "sudo vtysh -c 'show running-config'",
            read_timeout=30
        )

    return _clean_frr_config(raw)


# Lines to strip from FRR output — not valid IOS syntax for Batfish
_FRR_STRIP = {
    "frr version", "frr defaults", "log syslog", "log facility",
    "fpm address", "no fpm", "agentx", "no service integrated",
    "password zebra", "enable password", "zebra nexthop",
    "ip nht", "ipv6 nht", "building configuration",
    "current configuration", "no service", "hostname"
}

def _clean_frr_config(raw: str) -> str:
    """Strip ANSI codes and FRR-specific lines, return IOS-compatible config."""
    ansi = re.compile(r'\x1b\[[0-9;]*[mGKHF]')
    clean = ansi.sub('', raw)

    lines = []
    for line in clean.splitlines():
        stripped = line.strip().lower()
        if not stripped or stripped == "!":
            lines.append("!")
            continue
        if any(stripped.startswith(skip) for skip in _FRR_STRIP):
            continue
        lines.append(line)

    return "\n".join(lines)


def _parse_config(raw: str, hostname: str = None) -> str:
    """Strip ANSI, fix Netmiko truncation, parse JSON, inject DEVICE_METADATA."""
    ansi = re.compile(r'\x1b\[[0-9;]*[mGKHF]')
    clean = ansi.sub('', raw)

    start = clean.find('{')
    end   = clean.rfind('}')
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in SSH output")

    clean_json = clean[start:end+1].rstrip() + '\n}'

    data = json.loads(clean_json)

    if hostname:
        data.setdefault("DEVICE_METADATA", {}).setdefault("localhost", {})
        data["DEVICE_METADATA"]["localhost"].update({
            "hostname": hostname,
            "hwsku":    "Mellanox-SN2700",
            "mac":      "00:11:22:33:44:55",
        })

    return json.dumps(data, indent=4)


def run_command(ip_address: str, command: str) -> tuple[bool, str, str]:
    """
    Execute arbitrary command on SONiC switch via jump host.

    Returns:
        (success, stdout, stderr)
    """
    try:
        channel = _open_jump_channel(ip_address, 22)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy()
        )

        client.connect(
            hostname=ip_address,
            username=SSH_USERNAME,
            password=SSH_PASSWORD,
            sock=channel,
            look_for_keys=False,
            allow_agent=False,
            timeout=30,
        )

        stdin, stdout, stderr = client.exec_command(command)

        out = stdout.read().decode()
        err = stderr.read().decode()

        rc = stdout.channel.recv_exit_status()

        client.close()

        return rc == 0, out.strip(), err.strip()

    except Exception as e:
        return False, "", str(e)