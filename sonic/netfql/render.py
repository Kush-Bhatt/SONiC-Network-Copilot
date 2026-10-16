"""
sonic/netfql/render.py
----------------------
NetFQL: the human-facing surface syntax for the Canonical IR.

One job: deterministically print IR objects as reviewable NetFQL statements of
the form `verb object "on" target "with" params`. Render-only — nothing parses
NetFQL, so the text the human approves can never disagree with the IR that is
actually verified.
"""

from __future__ import annotations

# The parameter name each object type uses for its identity, so a statement
# reads the way a network engineer names the thing rather than as a bare `id`.
_ID_LABEL = {
    "vlan": "vlan_id",
    "vlan_member": "member",
    "vlan_svi": "svi",
    "vlan_dhcp_relay": "relay",
    "bgp": "process",
    "bgp_neighbor": "peer_ip",
    "bgp_network": "prefix",
    "interface": "name",
}


def _statement(obj) -> str:
    device = obj.device_display or obj.device
    params = [f'{_ID_LABEL.get(obj.object_type, "id")}="{obj.object_id}"']
    params += [f'{k}="{v}"' for k, v in sorted(obj.expected_state.items())]
    return f"{obj.verb} {obj.object_type} on {device} with\n    " + ",\n    ".join(params) + ";"


def render_netfql(objects) -> str:
    """Print Canonical IR objects as NetFQL statements, one per object."""
    return "\n".join(_statement(o) for o in objects)
