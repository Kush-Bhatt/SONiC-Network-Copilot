"""
sonic/switch/snapshot_builder.py  (translator improved — see notes below)

CHANGES vs original, and why:

  FIXED (real correctness bugs):
    B1/B2 - admin_status is now actually read. Previously every interface/
            loopback/portchannel/VLAN block unconditionally emitted
            "no shutdown" regardless of PORT.admin_status. Now emits
            "shutdown" when admin_status == "down".
            NOTE: SONiC's VLAN table itself carries no per-VLAN admin_status
            field (only PORT does) — so VLAN interface blocks still default
            to "no shutdown" unless a future SONiC field is found for it.
            This is a genuine data-availability limit, not a translator bug.

  WIDENED (fields Batfish's IOS model genuinely understands, now extracted):
    G1  - PORT.description, PORT.speed now emitted (still only mtu before)
    G3  - VRF: VRF table + INTERFACE/*_INTERFACE single-key vrf_name bindings
          now emitted as "ip vrf <name>" definitions + "ip vrf forwarding
          <name>" under the owning interface
    G2  - ACL_TABLE/ACL_RULE now emitted as "ip access-list extended <name>"
          + rule lines, applied to member ports via "ip access-group ... in"
    G6  - STATIC_ROUTE.distance now emitted as the trailing AD on ip route
    G7  - PORTCHANNEL.min_links now emitted as "port-channel min-links <n>"

  NOT FIXED / OUT OF SCOPE (no Batfish/IOS equivalent exists — see prior
  bug list G4/G5/G9 — widening the translator further here would not help,
  Batfish's own model has no concept of these):
    MCLAG_DOMAIN/MCLAG_INTERFACE, MIRROR_SESSION, buffers/QoS/COPP/PFCWD,
    FEATURE/NTP/SYSLOG/CRM, most of DEVICE_METADATA

  STILL HEURISTIC (documented limitation, not silently "fixed"):
    B3  - channel-group number is still derived from the PortChannel name's
          digits (e.g. "PortChannel12" -> 12). config_db.json carries no
          separate explicit channel index field to read instead — this is
          the best available signal, not a guess dressed as certainty, but
          it will misbehave on non-standard portchannel naming.
"""
import os
import re
import json
from typing import Optional

from langchain_core.tools import tool

from sonic.gns3.api_client import get_nodes, get_links
from sonic.switch.ssh_client import get_sonic_config, get_frr_config
from sonic.gns3.inventory_builder import is_sonic_node
from sonic.config.settings import SNAPSHOT_SONIC_DIR, SNAPSHOT_CONFIGS_DIR, SNAPSHOT_BATFISH_DIR
from dotenv import load_dotenv

load_dotenv()


def _admin_state_line(admin_status: Optional[str]) -> str:
    """B1/B2 fix: real admin state, not an unconditional 'no shutdown'."""
    if admin_status and str(admin_status).lower() == "down":
        return "  shutdown"
    return "  no shutdown"


def _portchannel_ios_name(pc_name: str) -> str:
    """SONiC's config_db.json names port-channels 'PortChannelN' (no hyphen).
    Batfish's Cisco IOS parser only recognizes the hyphenated 'Port-channelN'
    prefix — 'PortChannelN' fails with "Invalid interface name prefix", which
    then leaves any 'channel-group N' on a member interface pointing at an
    undefined structure. Only the Batfish-facing .cfg needs this rename; the
    real SONiC config (Ansible/CLI) keeps using 'PortChannelN' as-is."""
    num = ''.join(filter(str.isdigit, pc_name))
    return f"Port-channel{num}"


def _vrf_lines(vrf_names: set) -> list:
    """G3: emit 'ip vrf <name>' definitions for every distinct VRF seen."""
    lines = []
    for vrf in sorted(vrf_names):
        lines.append(f"ip vrf {vrf}")
        lines.append("!")
    return lines


def _acl_lines(config_json: dict) -> tuple:
    """
    G2: ACL_TABLE + ACL_RULE -> IOS extended ACLs.
    Returns (acl_definition_lines, {port_name: [acl_name, ...]} for apply-under-interface).
    SONiC ACL priority: higher number = evaluated first (matches IOS
    top-to-bottom = highest-priority-first ordering when sorted descending).
    """
    acl_table = config_json.get("ACL_TABLE", {})
    acl_rule = config_json.get("ACL_RULE", {})

    lines = []
    port_to_acls: dict = {}

    for acl_name, acl_cfg in sorted(acl_table.items()):
        lines.append(f"ip access-list extended {acl_name}")

        rules = {
            key: val for key, val in acl_rule.items()
            if key.startswith(f"{acl_name}|")
        }
        def _prio(item):
            try:
                return -int(item[1].get("PRIORITY", 0))
            except (TypeError, ValueError):
                return 0

        for _, rule_cfg in sorted(rules.items(), key=_prio):
            action = "permit" if str(rule_cfg.get("PACKET_ACTION", "")).upper() == "FORWARD" else "deny"
            src = rule_cfg.get("SRC_IP", "any")
            dst = rule_cfg.get("DST_IP", "any")
            proto = rule_cfg.get("IP_PROTOCOL", "ip")
            lines.append(f"  {action} {proto} {src} {dst}")

        lines.append("!")

        for port in acl_cfg.get("ports", []):
            port_to_acls.setdefault(port, []).append(acl_name)

    return lines, port_to_acls


def translate_to_batfish_cfg(config_json: dict, hostname: str, frr_config: str = "") -> str:
    """
    Translates SONiC config_db.json + FRR running config into IOS-style .cfg
    for Batfish. Widened per the notes at the top of this file — still an
    inherently partial view (Batfish's own model has no concept of several
    SONiC tables), not a byte-for-byte replica. Use the raw config_db.json/
    FRR captures (Tier 1) for ground truth; this (Tier 2) is Batfish's view.
    """
    lines = []
    lines.append(f"hostname {hostname}")
    lines.append("!")

    port_section = config_json.get("PORT", {})
    vrf_names_seen = set()

    for vrf_name in config_json.get("VRF", {}):
        vrf_names_seen.add(vrf_name)

    acl_def_lines, port_to_acls = _acl_lines(config_json)

    # ── INTERFACE + LOOPBACK_INTERFACE ────────────────────────────────────────
    interface_section = config_json.get("INTERFACE", {})
    loopback_section = config_json.get("LOOPBACK_INTERFACE", {})

    iface_ips = {}
    iface_vrf = {}
    for key, val in interface_section.items():
        if "|" in key:
            ifname, ip_prefix = key.split("|", 1)
            iface_ips.setdefault(ifname, []).append(ip_prefix)
        elif isinstance(val, dict) and val.get("vrf_name"):
            iface_vrf[key] = val["vrf_name"]
            vrf_names_seen.add(val["vrf_name"])

    for key in loopback_section:
        if "|" in key:
            ifname, ip_prefix = key.split("|", 1)
            iface_ips.setdefault(ifname, []).append(ip_prefix)

    all_iface_names = set(iface_ips.keys()) | set(iface_vrf.keys())
    for ifname in sorted(all_iface_names):
        lines.append(f"interface {ifname}")
        for ip_prefix in iface_ips.get(ifname, []):
            lines.append(f"  ip address {ip_prefix}")
        if ifname in iface_vrf:
            lines.append(f"  ip vrf forwarding {iface_vrf[ifname]}")

        port_cfg = port_section.get(ifname, {})
        if port_cfg.get("mtu"):
            lines.append(f"  mtu {port_cfg['mtu']}")
        if port_cfg.get("description"):
            lines.append(f"  description {port_cfg['description']}")
        if port_cfg.get("speed"):
            lines.append(f"  speed {port_cfg['speed']}")

        for acl_name in port_to_acls.get(ifname, []):
            lines.append(f"  ip access-group {acl_name} in")

        lines.append(_admin_state_line(port_cfg.get("admin_status")))
        lines.append("!")

    # ── VLAN + VLAN_INTERFACE (merged to avoid duplicate blocks) ─────────────
    vlan_section = config_json.get("VLAN", {})
    vlan_iface_section = config_json.get("VLAN_INTERFACE", {})

    vlan_iface_ips = {}
    vlan_iface_vrf = {}
    for key, val in vlan_iface_section.items():
        if "|" in key:
            ifname, ip_prefix = key.split("|", 1)
            vlan_iface_ips.setdefault(ifname, []).append(ip_prefix)
        elif isinstance(val, dict) and val.get("vrf_name"):
            vlan_iface_vrf[key] = val["vrf_name"]
            vrf_names_seen.add(val["vrf_name"])

    all_vlan_ids = set()
    for vlan_key in vlan_section:
        vlan_id = vlan_key.replace("Vlan", "").replace("vlan", "").strip()
        if vlan_id.isdigit():
            all_vlan_ids.add(vlan_id)
    for ifname in set(vlan_iface_ips.keys()) | set(vlan_iface_vrf.keys()):
        vlan_id = ifname.replace("Vlan", "").replace("vlan", "").strip()
        if vlan_id.isdigit():
            all_vlan_ids.add(vlan_id)

    for vlan_id in sorted(all_vlan_ids, key=int):
        ifname = f"Vlan{vlan_id}"
        lines.append(f"interface {ifname}")
        for ip_prefix in vlan_iface_ips.get(ifname, []):
            lines.append(f"  ip address {ip_prefix}")
        if ifname in vlan_iface_vrf:
            lines.append(f"  ip vrf forwarding {vlan_iface_vrf[ifname]}")
        lines.append("  no shutdown")
        lines.append("!")

    # ── VLAN_MEMBER (switchport) ──────────────────────────────────────────────
    vlan_member_section = config_json.get("VLAN_MEMBER", {})
    member_map = {}
    for key, val in vlan_member_section.items():
        if "|" in key:
            vlan_name, iface = key.split("|", 1)
            vlan_id = vlan_name.replace("Vlan", "").replace("vlan", "").strip()
            mode = val.get("tagging_mode", "untagged")
            member_map.setdefault(iface, []).append((vlan_id, mode))

    for iface, vlans in sorted(member_map.items()):
        lines.append(f"interface {iface}")
        untagged = [v for v, m in vlans if m == "untagged"]
        tagged = [v for v, m in vlans if m != "untagged"]
        if tagged:
            all_vlans = sorted(set(untagged + tagged), key=int)
            lines.append(f"  switchport mode trunk")
            if untagged:
                lines.append(f"  switchport trunk native vlan {untagged[0]}")
            lines.append(f"  switchport trunk allowed vlan {','.join(all_vlans)}")
        else:
            lines.append(f"  switchport access vlan {untagged[0]}")
        lines.append("!")

    # ── PORTCHANNEL + PORTCHANNEL_INTERFACE (merged) ─────────────────────────
    portchannel_section = config_json.get("PORTCHANNEL", {})
    pc_iface_section = config_json.get("PORTCHANNEL_INTERFACE", {})

    pc_iface_ips = {}
    for key in pc_iface_section:
        if "|" in key:
            ifname, ip_prefix = key.split("|", 1)
            pc_iface_ips.setdefault(ifname, []).append(ip_prefix)

    for pc_name, pc_cfg in sorted(portchannel_section.items()):
        lines.append(f"interface {_portchannel_ios_name(pc_name)}")
        if pc_cfg.get("mtu"):
            lines.append(f"  mtu {pc_cfg['mtu']}")
        if pc_cfg.get("min_links"):
            lines.append(f"  port-channel min-links {pc_cfg['min_links']}")
        for ip_prefix in pc_iface_ips.get(pc_name, []):
            lines.append(f"  ip address {ip_prefix}")
        lines.append(_admin_state_line(pc_cfg.get("admin_status")))
        lines.append("!")

    # ── PORTCHANNEL_MEMBER (channel-group) ────────────────────────────────────
    pc_member_section = config_json.get("PORTCHANNEL_MEMBER", {})
    for key in sorted(pc_member_section.keys()):
        if "|" in key:
            pc_name, iface = key.split("|", 1)
            pc_num = ''.join(filter(str.isdigit, pc_name))
            lines.append(f"interface {iface}")
            lines.append(f"  channel-group {pc_num}")
            lines.append("!")

    # ── STATIC_ROUTE ──────────────────────────────────────────────────────────
    static_routes = config_json.get("STATIC_ROUTE", {})
    for key, val in static_routes.items():
        if "|" in key:
            _, prefix = key.split("|", 1)
            nexthop = val.get("nexthop", "")
            distance = val.get("distance", "")
            if nexthop:
                if distance:
                    lines.append(f"ip route {prefix} {nexthop} {distance}")
                else:
                    lines.append(f"ip route {prefix} {nexthop}")

    if vrf_names_seen:
        lines.append("!")
        lines.extend(_vrf_lines(vrf_names_seen))

    if acl_def_lines:
        lines.append("!")
        lines.extend(acl_def_lines)

    if frr_config.strip():
        lines.append("!")
        lines.append("! --- FRR / BGP config ---")
        lines.append(frr_config)

    return "\n".join(lines)


def gns3_port_to_sonic_interface(port_number: int) -> str:
    return f"Ethernet{port_number * 4}"


def build_layer1_topology(nodes, links, snapshot_dir, batfish_dir):
    id_to_node = {n["node_id"]: n for n in nodes}

    edges = []
    for link in links:
        try:
            a = link["nodes"][0]
            b = link["nodes"][1]

            node_a = id_to_node.get(a["node_id"], {})
            node_b = id_to_node.get(b["node_id"], {})

            if not is_sonic_node(node_a) or not is_sonic_node(node_b):
                continue

            port_a = gns3_port_to_sonic_interface(a.get("port_number", 1))
            port_b = gns3_port_to_sonic_interface(b.get("port_number", 1))

            edges.append({
                "node1": {"hostname": node_a["name"], "interfaceName": port_a},
                "node2": {"hostname": node_b["name"], "interfaceName": port_b},
            })

        except (KeyError, IndexError):
            continue

    topology = {"edges": edges}

    topo_path = batfish_dir / "layer1_topology.json"
    with open(topo_path, "w") as f:
        json.dump(topology, f, indent=2)
    print(f"[+] Saved layer1_topology.json → {topo_path}")
    print(f"    Edges included: {len(edges)}")


@tool
def build_snapshot(mgmt_ips: dict, parsed_context: dict = None, project_id: Optional[str] = None):
    """
    Builds Batfish-compatible .cfg snapshots for all SONiC nodes.

    T1 (pre-change): pass parsed_context (from gather_switch_data's output)
                     to use pre-fetched config_db + frr_config, no SSH needed.
    T2 (post-change): omit parsed_context to SSH fresh and get post-change state.

    Args:
      mgmt_ips       : {switch_name: mgmt_ip} — required, from build_inventory()
      parsed_context : optional — pre-fetched state dict from gather_switch_data(),
                       used for T1 snapshots to avoid a second SSH round-trip
      project_id     : optional GNS3 project ID override

    Returns:
      dict with keys:
        snapshot_dir : str — path where .cfg files were written
        nodes_written : list[str] — switch names successfully snapshotted
    """
    nodes = get_nodes(project_id=project_id)
    links = get_links(project_id=project_id)

    SNAPSHOT_BATFISH_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    current_nodes = set(mgmt_ips.keys())
    for existing_cfg in SNAPSHOT_CONFIGS_DIR.glob("*.cfg"):
        node_name = existing_cfg.stem
        if node_name not in current_nodes:
            existing_cfg.unlink()
            print(f"[*] Removed stale snapshot: {existing_cfg.name}")

    nodes_written = []
    for node in nodes:
        if not is_sonic_node(node):
            continue

        name = node["name"]

        if name not in mgmt_ips:
            print(f"[!] {name} not in mgmt_ips — skipping. Add to mgmt_ips.json.")
            continue

        mgmt_ip = mgmt_ips[name]

        try:
            node_state = (parsed_context or {}).get("nodes", {}).get(name, {})
            if node_state.get("config_db") and "error" not in node_state["config_db"]:
                print(f"[*] {name}: using pre-fetched config (T1, no SSH)")
                config_dict = node_state["config_db"]
                frr_cfg = node_state.get("frr_config", "")
            else:
                print(f"[*] {name}: fetching config via SSH at {mgmt_ip} (T2)")
                raw_json_str = get_sonic_config(mgmt_ip, hostname=name)
                config_dict = json.loads(raw_json_str)
                frr_cfg = get_frr_config(mgmt_ip)

            cfg_content = translate_to_batfish_cfg(
                config_dict,
                hostname=name,
                frr_config=frr_cfg,
            )

            cfg_path = SNAPSHOT_CONFIGS_DIR / f"{name}.cfg"
            with open(cfg_path, "w") as f:
                f.write(cfg_content)
            print(f"[+] Saved → {cfg_path}")
            nodes_written.append(name)

        except json.JSONDecodeError as e:
            print(f"[!] JSON parse error for {name}: {e}")
        except Exception as e:
            print(f"[!] Error processing {name}: {e}")

    build_layer1_topology(nodes, links, SNAPSHOT_SONIC_DIR, SNAPSHOT_BATFISH_DIR)
    return {"snapshot_dir": str(SNAPSHOT_CONFIGS_DIR), "nodes_written": nodes_written}