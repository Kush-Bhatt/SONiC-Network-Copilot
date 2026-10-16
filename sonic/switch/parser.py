"""
sonic/switch/parser.py
-----------------------
Parses raw tool call results into structured context for:
  - snapshot_builder  (needs config_db + frr_config per node)
  - ansible_agent     (needs current state: vlans, bgp, portchannels, locked ports)
  - batfish_agent     (needs same current state + batfish schema)

Input:  raw results from run_tool_calls() + mgmt_ips + query_type
Output: parsed_context dict (see structure below)
"""

import re


def parse(raw_results: dict, mgmt_ips: dict, query_type: str) -> dict:
    """
    Args:
      raw_results : output of run_tool_calls()
                    {
                      "get_config_db":   {"192.168.200.11": {...}, ...},
                      "get_frr_config":  {"192.168.200.11": "...", ...},
                      "get_show_command": {"192.168.200.11:show mclag brief": "...", ...},
                    }
      mgmt_ips    : {node_name: mgmt_ip} from build_inventory()
      query_type  : string from _get_query_type()

    Returns:
      {
        "query_type": "vlan",
        "nodes": {
          "SONiC-VS-1": {
            "mgmt_ip":        "192.168.200.11",
            "config_db":      {...},
            "frr_config":     "...",
            "vlans":          [10, 100, 200],
            "vlan_members":   {"Ethernet12": {"vlan": 200, "mode": "tagged"}},
            "svi_ips":        {"Vlan200": "10.0.2.1/24"},
            "portchannels":   {"PortChannel12": {"ip": "10.0.12.1/30", "members": ["Ethernet4"]}},
            "locked_ports":   ["Ethernet4", "Ethernet8"],  # portchannel members + routed ifaces
            "bgp_as":         "65001",
            "bgp_router_id":  "192.168.200.11",
            "bgp_neighbors":  [{"ip": "10.0.12.2", "remote_as": "65001"}],
            "show_outputs":   {"show mclag brief": "..."}
          },
          ...
        }
      }
    """
    ip_to_name = {ip: name for name, ip in mgmt_ips.items()}

    config_db_results = raw_results.get("get_config_db", {})
    frr_results       = raw_results.get("get_frr_config", {})
    show_results      = raw_results.get("get_show_command", {})

    nodes = {}

    for node_name, mgmt_ip in mgmt_ips.items():
        config_db  = config_db_results.get(mgmt_ip, {})
        frr_config = frr_results.get(mgmt_ip, "")

        show_outputs = {}
        for key, output in show_results.items():
            if key.startswith(mgmt_ip + ":"):
                command = key[len(mgmt_ip) + 1:]
                show_outputs[command] = output

        node = {
            "mgmt_ip":    mgmt_ip,
            "config_db":  config_db,
            "frr_config": frr_config,
            "show_outputs": show_outputs,
        }

        if config_db and "error" not in config_db:
            node["vlans"]        = _parse_vlans(config_db)
            node["vlan_members"] = _parse_vlan_members(config_db)
            node["svi_ips"]      = _parse_svi_ips(config_db)
            node["portchannels"] = _parse_portchannels(config_db)
            node["locked_ports"] = _parse_locked_ports(config_db)
        else:
            node["vlans"]        = []
            node["vlan_members"] = {}
            node["svi_ips"]      = {}
            node["portchannels"] = {}
            node["locked_ports"] = []

        if frr_config:
            bgp = _parse_bgp(frr_config)
            node["bgp_as"]        = bgp["as"]
            node["bgp_router_id"] = bgp["router_id"]
            node["bgp_neighbors"] = bgp["neighbors"]
        else:
            node["bgp_as"]        = None
            node["bgp_router_id"] = None
            node["bgp_neighbors"] = []

        nodes[node_name] = node

    return {
        "query_type": query_type,
        "nodes": nodes,
    }


# ── config_db parsers ─────────────────────────────────────────────────────────

def _parse_vlans(cfg: dict) -> list:
    """Returns sorted list of VLAN IDs (int)."""
    vlan_section = cfg.get("VLAN", {})
    vlan_ids = []
    for key in vlan_section:
        vid = key.replace("Vlan", "").replace("vlan", "").strip()
        if vid.isdigit():
            vlan_ids.append(int(vid))
    return sorted(vlan_ids)


def _parse_vlan_members(cfg: dict) -> dict:
    """
    Returns {interface: {"vlan": int, "mode": "tagged"|"untagged"}}
    For trunk ports (multiple VLANs), returns list format:
      {interface: [{"vlan": int, "mode": str}, ...]}
    """
    vlan_member_section = cfg.get("VLAN_MEMBER", {})
    members = {}
    for key, val in vlan_member_section.items():
        if "|" in key:
            vlan_name, iface = key.split("|", 1)
            vid = vlan_name.replace("Vlan", "").replace("vlan", "").strip()
            if not vid.isdigit():
                continue
            mode = val.get("tagging_mode", "untagged")
            entry = {"vlan": int(vid), "mode": mode}
            if iface not in members:
                members[iface] = entry
            else:
                existing = members[iface]
                if isinstance(existing, list):
                    existing.append(entry)
                else:
                    members[iface] = [existing, entry]
    return members


def _parse_svi_ips(cfg: dict) -> dict:
    """Returns {"Vlan200": "10.0.2.1/24", ...}"""
    vlan_iface = cfg.get("VLAN_INTERFACE", {})
    svi_ips = {}
    for key in vlan_iface:
        if "|" in key:
            ifname, ip = key.split("|", 1)
            svi_ips[ifname] = ip
    return svi_ips


def _parse_portchannels(cfg: dict) -> dict:
    """
    Returns {
      "PortChannel12": {"ip": "10.0.12.1/30", "members": ["Ethernet4"]},
      ...
    }
    """
    pc_section = cfg.get("PORTCHANNEL", {})
    pc_iface   = cfg.get("PORTCHANNEL_INTERFACE", {})
    pc_member  = cfg.get("PORTCHANNEL_MEMBER", {})

    pc_ips = {}
    for key in pc_iface:
        if "|" in key:
            pc_name, ip = key.split("|", 1)
            pc_ips[pc_name] = ip

    pc_members = {}
    for key in pc_member:
        if "|" in key:
            pc_name, iface = key.split("|", 1)
            pc_members.setdefault(pc_name, []).append(iface)

    result = {}
    for pc_name in pc_section:
        result[pc_name] = {
            "ip":      pc_ips.get(pc_name, None),
            "members": pc_members.get(pc_name, []),
        }
    return result


def _parse_locked_ports(cfg: dict) -> list:
    """
    Returns sorted list of ports unavailable for VLAN membership:
      - Ports that are members of a PortChannel
      - Ports that have a routed IP assigned (INTERFACE section)

    Both categories make a port unavailable for 'config vlan member add'.
    SONiC error: "Error: EthernetX is a router interface!" or
                 "Error: EthernetX is already a LAG member"
    """
    locked = set()

    # portchannel members
    pc_member = cfg.get("PORTCHANNEL_MEMBER", {})
    for key in pc_member:
        if "|" in key:
            _, iface = key.split("|", 1)
            locked.add(iface)

    # routed interfaces — only lock ports that have an actual IP assigned.
    # INTERFACE section has two key forms:
    #   "EthernetX"             — bare entry, SONiC adds this by default, NOT a routed port
    #   "EthernetX|10.0.x.x/30" — IP assigned, this IS a routed port → lock it
    # Only the "|" form means the port is truly routed and unavailable for VLAN.
    interface_section = cfg.get("INTERFACE", {})
    for key in interface_section:
        if "|" in key:
            iface = key.split("|")[0].strip()
            if iface.startswith("Ethernet"):
                locked.add(iface)

    return sorted(locked)


# ── FRR parsers ───────────────────────────────────────────────────────────────

def _parse_bgp(frr: str) -> dict:
    """
    Parses FRR running config string.
    Returns {"as": "65001", "router_id": "192.168.200.11", "neighbors": [...]}
    """
    bgp_as        = None
    bgp_router_id = None
    bgp_neighbors = []

    for line in frr.splitlines():
        line = line.strip()

        m = re.match(r'^router bgp (\d+)', line)
        if m:
            bgp_as = m.group(1)

        m = re.match(r'^bgp router-id ([\d.]+)', line)
        if m:
            bgp_router_id = m.group(1)

        m = re.match(r'^neighbor ([\d.]+) remote-as (\d+)', line)
        if m:
            bgp_neighbors.append({
                "ip":        m.group(1),
                "remote_as": m.group(2),
            })

    return {
        "as":        bgp_as,
        "router_id": bgp_router_id,
        "neighbors": bgp_neighbors,
    }


# ── context formatters (for agent prompts) ────────────────────────────────────

def format_for_agents(parsed_context: dict) -> str:
    """
    Converts parsed_context into a human-readable string for injection
    into ansible_agent and batfish_agent system prompts.

    Shows: VLANs, SVI IPs, PortChannels, locked ports, VLAN member
    assignments, and BGP state per node.
    """
    lines = ["# CURRENT SWITCH STATE"]

    for node_name, node in sorted(parsed_context["nodes"].items()):
        lines.append(f"\n## {node_name} (mgmt_ip={node['mgmt_ip']})")

        # VLANs
        vlans = node.get("vlans", [])
        lines.append(f"  VLANs: {','.join(str(v) for v in vlans) if vlans else 'none'}")

        # SVI IPs
        for iface, ip in sorted(node.get("svi_ips", {}).items()):
            lines.append(f"  SVI: {iface} = {ip}")

        # PortChannels
        for pc_name, pc_info in sorted(node.get("portchannels", {}).items()):
            ip  = pc_info.get("ip", "no-ip")
            mbs = ",".join(pc_info.get("members", []))
            lines.append(f"  PortChannel: {pc_name} ip={ip} members=[{mbs}]")

        # Locked ports (portchannel members + routed interfaces)
        locked = node.get("locked_ports", [])
        if locked:
            lines.append(f"  LOCKED PORTS (cannot assign to VLAN): {','.join(locked)}")

        # VLAN member assignments — shows which ports are already in use
        # This prevents AnsibleAgent from trying to re-assign occupied ports
        vlan_members = node.get("vlan_members", {})
        if vlan_members:
            lines.append(f"  VLAN MEMBER ASSIGNMENTS (ports already in use):")
            for iface, assignment in sorted(vlan_members.items()):
                if isinstance(assignment, list):
                    parts = ", ".join(
                        f"VLAN{e['vlan']}({e['mode']})" for e in assignment
                    )
                    lines.append(f"    {iface}: {parts}")
                else:
                    lines.append(
                        f"    {iface}: VLAN{assignment['vlan']}({assignment['mode']})"
                    )

        # BGP
        bgp_as = node.get("bgp_as")
        if bgp_as:
            rid = node.get("bgp_router_id", "unknown")
            lines.append(f"  BGP: AS={bgp_as} router-id={rid}")
            for nbr in node.get("bgp_neighbors", []):
                lines.append(f"    neighbor {nbr['ip']} remote-as {nbr['remote_as']}")
        else:
            lines.append(f"  BGP: not configured")

        # Show outputs (capped at 10 lines each)
        for cmd, output in node.get("show_outputs", {}).items():
            lines.append(f"  [{cmd}]:")
            for ol in output.splitlines()[:10]:
                lines.append(f"    {ol}")

    return "\n".join(lines)



def build_full_context(topology_str: str, parsed_context: dict) -> str:
    """
    Combines topology text + formatted switch state into the single
    context string BatfishAgent/AnsibleAgent expect as their 'topology'
    constructor arg.

    This replaces the inline `full_context = topology_str + "\n\n" + state_str`
    line that currently lives directly inside run.py's Step 4. Extracted here
    so the new orchestrator's gather_state node can call one function instead
    of duplicating that concatenation logic itself.

    Does NOT change format_for_agents() — that function is untouched and
    still used exactly as-is by both run.py (old pipeline) and this function.
    """
    state_str = format_for_agents(parsed_context)
    return topology_str + "\n\n" + state_str