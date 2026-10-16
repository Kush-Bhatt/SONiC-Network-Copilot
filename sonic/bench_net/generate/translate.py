"""
sonic/bench_net/generate/translate.py
-----------------------------------------
Standalone copy of translate_to_batfish_cfg() from
sonic/switch/snapshot_builder.py. Only the translation function itself
is copied — build_snapshot()'s live-topology/GNS3 machinery is not
needed here since bench_net drives capture off mgmt_ips directly.
"""
from typing import Optional


def _admin_state_line(admin_status: Optional[str]) -> str:
    if admin_status and str(admin_status).lower() == "down":
        return "  shutdown"
    return "  no shutdown"


def _portchannel_ios_name(pc_name: str) -> str:
    num = ''.join(filter(str.isdigit, pc_name))
    return f"Port-channel{num}"


def _vrf_lines(vrf_names: set) -> list:
    lines = []
    for vrf in sorted(vrf_names):
        lines.append(f"ip vrf {vrf}")
        lines.append("!")
    return lines


def _acl_lines(config_json: dict) -> tuple:
    acl_table = config_json.get("ACL_TABLE", {})
    acl_rule = config_json.get("ACL_RULE", {})
    lines = []
    port_to_acls: dict = {}

    for acl_name, acl_cfg in sorted(acl_table.items()):
        lines.append(f"ip access-list extended {acl_name}")
        rules = {k: v for k, v in acl_rule.items() if k.startswith(f"{acl_name}|")}

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
    lines = [f"hostname {hostname}", "!"]

    port_section = config_json.get("PORT", {})
    vrf_names_seen = set(config_json.get("VRF", {}).keys())
    acl_def_lines, port_to_acls = _acl_lines(config_json)

    interface_section = config_json.get("INTERFACE", {})
    loopback_section = config_json.get("LOOPBACK_INTERFACE", {})

    iface_ips, iface_vrf = {}, {}
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

    vlan_section = config_json.get("VLAN", {})
    vlan_iface_section = config_json.get("VLAN_INTERFACE", {})
    vlan_iface_ips, vlan_iface_vrf = {}, {}
    for key, val in vlan_iface_section.items():
        if "|" in key:
            ifname, ip_prefix = key.split("|", 1)
            vlan_iface_ips.setdefault(ifname, []).append(ip_prefix)
        elif isinstance(val, dict) and val.get("vrf_name"):
            vlan_iface_vrf[key] = val["vrf_name"]
            vrf_names_seen.add(val["vrf_name"])

    all_vlan_ids = set()
    for vlan_key in vlan_section:
        vid = vlan_key.replace("Vlan", "").replace("vlan", "").strip()
        if vid.isdigit():
            all_vlan_ids.add(vid)
    for ifname in set(vlan_iface_ips.keys()) | set(vlan_iface_vrf.keys()):
        vid = ifname.replace("Vlan", "").replace("vlan", "").strip()
        if vid.isdigit():
            all_vlan_ids.add(vid)

    for vlan_id in sorted(all_vlan_ids, key=int):
        ifname = f"Vlan{vlan_id}"
        lines.append(f"interface {ifname}")
        for ip_prefix in vlan_iface_ips.get(ifname, []):
            lines.append(f"  ip address {ip_prefix}")
        if ifname in vlan_iface_vrf:
            lines.append(f"  ip vrf forwarding {vlan_iface_vrf[ifname]}")
        lines.append("  no shutdown")
        lines.append("!")

    vlan_member_section = config_json.get("VLAN_MEMBER", {})
    member_map = {}
    for key, val in vlan_member_section.items():
        if "|" in key:
            vlan_name, iface = key.split("|", 1)
            vid = vlan_name.replace("Vlan", "").replace("vlan", "").strip()
            mode = val.get("tagging_mode", "untagged")
            member_map.setdefault(iface, []).append((vid, mode))

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

    pc_member_section = config_json.get("PORTCHANNEL_MEMBER", {})
    for key in sorted(pc_member_section.keys()):
        if "|" in key:
            pc_name, iface = key.split("|", 1)
            pc_num = ''.join(filter(str.isdigit, pc_name))
            lines.append(f"interface {iface}")
            lines.append(f"  channel-group {pc_num}")
            lines.append("!")

    static_routes = config_json.get("STATIC_ROUTE", {})
    for key, val in static_routes.items():
        if "|" in key:
            _, prefix = key.split("|", 1)
            nexthop = val.get("nexthop", "")
            distance = val.get("distance", "")
            if nexthop:
                lines.append(f"ip route {prefix} {nexthop} {distance}" if distance else f"ip route {prefix} {nexthop}")

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
