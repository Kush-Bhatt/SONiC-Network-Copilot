import json
import re
from sonic.gns3.api_client import get_nodes, get_links
from sonic.gns3.inventory_builder import is_sonic_node


def build_topology(mgmt_ips: dict, fetch_state: bool = False) -> str:
    """
    Builds topology string for LLM system prompt.

    fetch_state is kept as a parameter for compatibility but always treated as False.
    Switch state is now fetched exclusively by SwitchDataAgent (switch_data_agent.py).

    Sections:
      # NODES  — node names + mgmt IPs
      # LINKS  — physical inter-switch connections
    """
    nodes = get_nodes()
    links = get_links()

    id_to_node = {n["node_id"]: n for n in nodes}
    sonic_nodes = [n for n in nodes if is_sonic_node(n)]

    # ── NODES ─────────────────────────────────────────────────────────────────
    node_lines = ["# NODES"]
    for node in sonic_nodes:
        name    = node["name"]
        ntype   = node.get("node_type", "unknown")
        mgmt_ip = mgmt_ips.get(name, "unknown")
        node_lines.append(f"  {name}  type={ntype}  mgmt_ip={mgmt_ip}")

    # ── LINKS ─────────────────────────────────────────────────────────────────
    link_lines = ["# LINKS"]
    for link in links:
        try:
            a = link["nodes"][0]
            b = link["nodes"][1]
            node_a = id_to_node.get(a["node_id"], {})
            node_b = id_to_node.get(b["node_id"], {})
            if not is_sonic_node(node_a) or not is_sonic_node(node_b):
                continue
            port_a = f"Ethernet{a.get('port_number', 0) * 4}"
            port_b = f"Ethernet{b.get('port_number', 0) * 4}"
            link_lines.append(
                f"  {node_a['name']} {port_a} <-> {node_b['name']} {port_b}"
            )
        except (KeyError, IndexError):
            continue

    return "\n\n".join([
        "\n".join(node_lines),
        "\n".join(link_lines),
    ])