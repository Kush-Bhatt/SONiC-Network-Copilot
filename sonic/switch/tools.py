"""
sonic/switch/tools.py
----------------------
LangChain tools for fetching live state from SONiC switches.
Each tool takes a single mgmt_ip argument.
All SSH is two-hop via jump host (handled inside ssh_client).

Tools:
  get_config_db(mgmt_ip)     — config_db.json (VLANs, portchannels, interfaces)
  get_frr_config(mgmt_ip)    — FRR vtysh running-config (BGP, EVPN, routes)
  get_show_command(mgmt_ip, command) — generic show command runner

Valid commands for get_show_command:
  show vlan brief
  show ip bgp summary
  show mclag brief
  show vrf
  show bgp l2vpn evpn summary
  show mirror_session
  show interfaces portchannel
  show ip route
  show interfaces status
"""

import json
import re
import socket
import paramiko
from netmiko import ConnectHandler
from langchain_core.tools import tool

from sonic.config.settings import (
    SSH_USERNAME, SSH_PASSWORD,
    GNS3_HOST, GNS3_JUMP_USER, GNS3_JUMP_PASS,
)


# ── internal SSH helpers ──────────────────────────────────────────────────────

def _open_jump_channel(target_ip: str, target_port: int = 22) -> paramiko.Channel:
    """Two-hop paramiko: laptop → jump host → switch."""
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


def _run_command(mgmt_ip: str, command: str) -> str:
    """Open two-hop SSH to switch, run command, return raw output."""
    channel = _open_jump_channel(mgmt_ip, 22)
    device = {
        "device_type":      "linux",
        "host":             mgmt_ip,
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
        raw = conn.send_command(command, read_timeout=30)
    # strip ANSI
    ansi = re.compile(r'\x1b\[[0-9;]*[mGKHF]')
    return ansi.sub('', raw)


# ── LangChain tools ───────────────────────────────────────────────────────────

@tool
def get_config_db(mgmt_ip: str) -> dict:
    """
    Fetches config_db.json from a SONiC switch via SSH.

    Returns parsed dict containing:
      VLAN            — configured VLANs and their IDs
      VLAN_MEMBER     — port-to-VLAN assignments with tagging mode
      VLAN_INTERFACE  — SVI IPs per VLAN
      INTERFACE       — physical interface IPs
      LOOPBACK_INTERFACE — loopback IPs
      PORTCHANNEL     — portchannel definitions
      PORTCHANNEL_INTERFACE — portchannel IPs
      PORTCHANNEL_MEMBER    — which ports belong to which portchannel
      STATIC_ROUTE    — static route table
      MCLAG_DOMAIN    — MCLAG domain config (if configured)
      MCLAG_INTERFACE — MCLAG interface bindings (if configured)
      VRF             — VRF table (if configured)
      MIRROR_SESSION  — ERSPAN/mirror session config (if configured)

    Use when: query involves VLANs, portchannels, SVIs, static routes,
              MCLAG, VRF, or ERSPAN mirror sessions.
    """
    raw = _run_command(mgmt_ip, "cat /etc/sonic/config_db.json")

    # fix Netmiko truncation (file is ~48KB, netmiko truncates at 48744 bytes)
    start = raw.find('{')
    end   = raw.rfind('}')
    if start == -1 or end == -1:
        return {"error": f"No JSON found in config_db output from {mgmt_ip}"}

    clean_json = raw[start:end+1].rstrip() + '\n}'
    try:
        return json.loads(clean_json)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error from {mgmt_ip}: {e}"}


@tool
def get_frr_config(mgmt_ip: str) -> str:
    """
    Fetches FRR running config via vtysh from a SONiC switch.

    Returns cleaned config string containing:
      router bgp <AS>     — BGP AS number and router-id
      neighbor <ip>       — BGP neighbor statements
      address-family      — address family config
      route-map           — route policy definitions
      prefix-list         — prefix filter lists
      vni <id>            — EVPN VNI config (if configured)
      address-family l2vpn evpn — EVPN address family (if configured)

    Use when: query involves BGP configuration, BGP neighbors,
              EVPN/VNI advertisement, route policies, or prefix filters.
    """
    _FRR_STRIP = {
        "frr version", "frr defaults", "log syslog", "log facility",
        "fpm address", "no fpm", "agentx", "no service integrated",
        "password zebra", "enable password", "zebra nexthop",
        "ip nht", "ipv6 nht", "building configuration",
        "current configuration", "no service", "hostname"
    }

    raw = _run_command(mgmt_ip, "sudo vtysh -c 'show running-config'")

    lines = []
    for line in raw.splitlines():
        stripped = line.strip().lower()
        if not stripped or stripped == "!":
            lines.append("!")
            continue
        if any(stripped.startswith(skip) for skip in _FRR_STRIP):
            continue
        lines.append(line)

    return "\n".join(lines)


@tool
def get_show_command(mgmt_ip: str, command: str) -> str:
    """
    Runs a show command on a SONiC switch and returns the output.

    Valid commands and what they return:

      "show vlan brief"
        — VLAN IDs, member ports, tagging mode (tagged/untagged) per VLAN
        — Use for: verifying VLAN existence and port membership

      "show ip bgp summary"
        — BGP neighbor IPs, remote AS, state (Established/Active/Idle), uptime
        — Use for: checking live BGP session state

      "show mclag brief"
        — MCLAG domain ID, peer IP, peer-link interface, member ports, role
        — Use for: MCLAG domain config and active/standby state

      "show vrf"
        — VRF names and bound interfaces
        — Use for: verifying VRF existence and interface bindings

      "show bgp l2vpn evpn summary"
        — EVPN BGP neighbors, VNI count, session state
        — Use for: verifying EVPN peering and VNI advertisement

      "show mirror_session"
        — Mirror session names, source port, destination, session type (ERSPAN/SPAN)
        — Use for: verifying ERSPAN or SPAN mirror sessions

      "show interfaces portchannel"
        — LAG members, admin/oper state, mode
        — Use for: verifying portchannel membership and status

      "show ip route"
        — Full routing table: protocol, prefix, next-hop, interface
        — Use for: verifying static routes, BGP routes, connected routes

      "show interfaces status"
        — Per-port admin state, oper state, speed, MTU
        — Use for: checking port availability before assigning to VLAN or LAG
    """
    VALID_COMMANDS = {
        "show vlan brief",
        "show ip bgp summary",
        "show mclag brief",
        "show vrf",
        "show bgp l2vpn evpn summary",
        "show mirror_session",
        "show interfaces portchannel",
        "show ip route",
        "show interfaces status",
    }

    if command not in VALID_COMMANDS:
        return f"ERROR: '{command}' is not a valid command. Valid: {sorted(VALID_COMMANDS)}"

    return _run_command(mgmt_ip, command)