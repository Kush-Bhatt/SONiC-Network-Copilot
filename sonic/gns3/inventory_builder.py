"""
sonic/gns3/inventory_builder.py
--------------------------------
Builds Ansible inventory (hosts.ini) for SONiC switches.

mgmt IPs are loaded from sonic/config/mgmt_ips.json keyed by GNS3_PROJECT_ID.
No console/telnet discovery — IPs are static per topology.

GNS3 API is still called to get node names and confirm which nodes exist
in the active project (used by topology_builder for links/topology string).
"""
import os
import json

from langchain_core.tools import tool

from sonic.gns3.api_client import get_nodes
from sonic.config.settings import (
    GNS3_PROJECT_ID,
    SSH_USERNAME,
    SSH_PASSWORD,
    ANSIBLE_DIR,
    MGMT_IPS_FILE,
)

SONIC_NAME_HINTS = {"sonic", "leaf", "spine", "acc", "agg", "core"}


def is_sonic_node(node: dict) -> bool:
    """Single source of truth for identifying SONiC nodes by name."""
    return any(hint in node["name"].lower() for hint in SONIC_NAME_HINTS)


def _load_mgmt_ips() -> dict:
    """
    Load mgmt IPs for the current project from mgmt_ips.json.
    Returns {node_name: ip} for GNS3_PROJECT_ID.
    Raises KeyError if project ID not found — add it to mgmt_ips.json first.
    """
    with open(MGMT_IPS_FILE, "r") as f:
        all_ips = json.load(f)

    if GNS3_PROJECT_ID not in all_ips:
        raise KeyError(
            f"Project ID '{GNS3_PROJECT_ID}' not found in {MGMT_IPS_FILE}. "
            f"Add it with the switch mgmt IPs before running the pipeline."
        )

    # Strip metadata keys (prefixed with _)
    return {k: v for k, v in all_ips[GNS3_PROJECT_ID].items() if not k.startswith("_")}


@tool
def build_inventory() -> dict:
    """
    Builds the Ansible inventory (hosts.ini) for all SONiC nodes in the
    active GNS3 project, using mgmt IPs from sonic/config/mgmt_ips.json.

    Takes no arguments — reads GNS3_PROJECT_ID from settings and static IPs
    from mgmt_ips.json automatically.

    Returns a dict with two keys:
      inventory_str : str  — full hosts.ini content
      mgmt_ips      : dict — {switch_name: mgmt_ip}
    """
    # GNS3 API: get node names that actually exist in this project
    nodes = get_nodes()
    sonic_node_names = {node["name"] for node in nodes if is_sonic_node(node)}

    if not sonic_node_names:
        raise RuntimeError("No SONiC nodes found in GNS3 project via API.")

    # Static IPs from config
    static_ips = _load_mgmt_ips()

    mgmt_ips = {}
    host_lines = []
    missing = []

    for name in sorted(sonic_node_names):
        if name not in static_ips:
            missing.append(name)
            continue

        ip = static_ips[name]
        mgmt_ips[name] = ip
        host_lines.append(
            f"{name} ansible_host={ip} "
            f"ansible_user={SSH_USERNAME} "
            f"ansible_password={SSH_PASSWORD} "
            f"ansible_connection=ssh "
            f"ansible_network_os=sonic"
        )
        print(f"[+] {name} → {ip}")

    if missing:
        print(
            f"[!] These GNS3 nodes have no entry in mgmt_ips.json: {missing}. "
            f"Add them to {MGMT_IPS_FILE} under project '{GNS3_PROJECT_ID}'."
        )

    if not mgmt_ips:
        raise RuntimeError(
            "No mgmt IPs resolved. Check mgmt_ips.json for project "
            f"'{GNS3_PROJECT_ID}'."
        )

    inventory_str = "[sonic_switches]\n" + "\n".join(host_lines)

    os.makedirs(ANSIBLE_DIR, exist_ok=True)
    inventory_path = os.path.join(ANSIBLE_DIR, "hosts.ini")
    with open(inventory_path, "w") as f:
        f.write(inventory_str)
    print(f"[+] Inventory written to {inventory_path}")

    return {"inventory_str": inventory_str, "mgmt_ips": mgmt_ips}