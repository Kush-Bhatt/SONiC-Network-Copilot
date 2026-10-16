"""
sonic/netfql/ir.py
------------------
The Canonical IR: the object-centric denotation [[s]] of an approved Plan.

One job: turn a Plan's atomic statements into a flat list of real network
objects (device, object_type, object_id, expected_state), each carrying its own
CRUD verb. The NetFQL text the human approves and the Fact set the verifier
checks are both derived from this single lowering, so they cannot disagree.
"""

from __future__ import annotations

from pydantic.dataclasses import dataclass
from dataclasses import field

from sonic.hitl.normalize import (
    norm_asn,
    norm_device,
    norm_intf,
    norm_ip,
    norm_ip_iface,
    norm_prefix,
    norm_svi,
)


@dataclass(frozen=True)
class IRObject:
    """One network object and the state the approved intent asserts about it.

    `expected_state` is empty for presence-only objects (a VLAN, a DHCP relay
    target, an advertised prefix) whose existence *is* the whole assertion —
    there, the verb carries the claim. Never hashed; expected_state is a dict.
    """

    device: str                  # norm_device'd — this is the fact-matching key
    object_type: str
    object_id: str
    verb: str                    # "create" | "modify" | "delete"
    expected_state: dict[str, str] = field(default_factory=dict)
    # The device name as the user wrote it, for the NetFQL render only. Excluded
    # from equality so casing can never split two otherwise-identical objects.
    device_display: str = field(default="", compare=False)


def _obj(stmt, object_type: str, object_id: str, state: dict[str, str]) -> IRObject:
    return IRObject(
        norm_device(stmt.target_node), object_type, object_id, stmt.verb, state,
        stmt.target_node,
    )


def _lower_one(stmt) -> list[IRObject]:
    t = stmt.object_type
    if t == "vlan":
        return [_obj(stmt, "vlan", str(stmt.vlan_id), {})]
    if t == "svi":
        # A delete carries no address: the claim is "this SVI is gone", and
        # asserting an IP on an object that must not exist is unverifiable.
        state = {} if stmt.ip is None else {"ip": norm_ip_iface(str(stmt.ip))}
        return [_obj(stmt, "vlan_svi", norm_svi(stmt.vlan_id), state)]
    if t == "vlan_member":
        return [_obj(stmt, "vlan_member",
                     f"{stmt.vlan_id}/{norm_intf(stmt.interface)}",
                     {"tagging": stmt.tagging})]
    if t == "vlan_dhcp_relay":
        return [_obj(stmt, "vlan_dhcp_relay",
                     f"{stmt.vlan_id}/{norm_ip(str(stmt.server))}", {})]
    if t == "bgp":
        state = {"local_as": norm_asn(stmt.local_as)}
        if stmt.router_id:
            state["router_id"] = norm_ip(str(stmt.router_id))
        return [_obj(stmt, "bgp", "-", state)]
    if t == "bgp_neighbor":
        return [_obj(stmt, "bgp_neighbor", norm_ip(str(stmt.peer_ip)),
                     {"remote_as": norm_asn(stmt.remote_as)})]
    if t == "bgp_network":
        return [_obj(stmt, "bgp_network", norm_prefix(str(stmt.prefix)), {})]
    if t == "interface":
        state: dict[str, str] = {}
        if stmt.admin_state is not None:
            state["admin_state"] = stmt.admin_state
        if stmt.mtu is not None:
            state["mtu"] = str(stmt.mtu)
        # No attributes means the statement asserts nothing about this
        # interface; a bare presence object would claim more than was approved.
        return [_obj(stmt, "interface", norm_intf(stmt.interface), state)] if state else []
    raise TypeError(f"lower: unsupported statement type {t!r}")


def lower(plan) -> list[IRObject]:
    """Denotational lowering: Plan -> the objects it asserts about.

    Close to an identity map — statements are already atomic and
    single-target. The expansion this used to do (one intent fanned across a
    switch list) is exactly what made a per-node SVI inexpressible.
    """
    statements = getattr(plan, "statements", None)
    if statements is None:
        raise TypeError(f"lower: expected a Plan, got {type(plan).__name__}")
    return [o for s in statements for o in _lower_one(s)]
