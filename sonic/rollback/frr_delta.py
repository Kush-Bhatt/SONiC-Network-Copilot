"""
sonic/rollback/frr_delta.py
--------------------------
FRR command-planning: given a baseline FRR capture and the current live
FRR config, compute the vtysh commands needed to correct current -> baseline.

This is NOT a diff/compare mechanism (that's sonic.hitl.facts — reused
as-is by verify.py, never duplicated here). This is specifically "what
commands do I run to fix this," scoped to what AnsibleAgent actually
generates for BGP: router bgp <AS>, bgp router-id <ip>,
neighbor <ip> remote-as <AS>, network <prefix>. NOT a general FRR parser.

DELIBERATELY DUPLICATED (not imported) from
sonic/tests/manual_test_frr_rollback.py — that script remains a
standalone experimentation harness; this module is the real pipeline's
source of truth going forward.
"""
import re

_AS_RE = re.compile(r"^router bgp (\d+)\s*$")
_ROUTER_ID_RE = re.compile(r"^\s*bgp router-id (\S+)\s*$")
_NEIGHBOR_RE = re.compile(r"^\s*neighbor \S+ remote-as \d+\s*$")
_NETWORK_RE = re.compile(r"^\s*network \S+\s*$")


def parse_bgp_block(frr_text: str) -> dict:
    """
    Returns {"as": int|None, "router_id": str|None, "lines": set[str]}.

    KNOWN GAP (carried over from the test script, unfixed): does not
    track nesting — a "network <prefix>" line inside an
    "address-family ipv4 unicast" sub-block is still captured, and would
    be re-added as a bare top-level command if a future case needed to
    re-add one. Untested against that scenario.
    """
    as_number = None
    router_id = None
    lines = set()

    in_bgp_block = False
    for raw_line in frr_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        as_match = _AS_RE.match(stripped)
        if as_match:
            as_number = int(as_match.group(1))
            in_bgp_block = True
            continue

        if in_bgp_block:
            if stripped in ("!", "exit", "end") or stripped.startswith("router "):
                if stripped.startswith("router ") and not as_match:
                    in_bgp_block = False
                elif stripped in ("!", "exit", "end"):
                    in_bgp_block = False
                continue

            rid_match = _ROUTER_ID_RE.match(line)
            if rid_match:
                router_id = rid_match.group(1)
                continue

            if _NEIGHBOR_RE.match(line) or _NETWORK_RE.match(line):
                lines.add(stripped)
                continue

    return {"as": as_number, "router_id": router_id, "lines": lines}


def build_vtysh_commands(plan: dict) -> list[str]:
    """
    Turns one switch's plan dict into the vtysh -c command sequence.

    Three distinct cases, now handled explicitly:
      1. whole_as_remove: baseline had no AS, current has one ->
         "no router bgp <AS>" as a standalone top-level command.
      2. whole_as_create: baseline had an AS, current has none ->
         enter "router bgp <AS>", set router-id + add all baseline lines.
      3. within_as_delta: same AS on both sides -> existing add/remove logic.
    """
    if plan["no_op"] or plan["as"] is None:
        return []

    action = plan.get("action")

    if action == "whole_as_remove":
        return ["configure terminal", f"no router bgp {plan['current_as']}", "end"]

    if action == "whole_as_create":
        cmds = ["configure terminal", f"router bgp {plan['as']}"]
        if plan["router_id_fix"]:
            cmds.append(f"bgp router-id {plan['router_id_fix']}")
        for line in plan["to_add"]:
            cmds.append(line)
        cmds.append("end")
        return cmds

    # within_as_delta (default) — same AS present on both sides
    cmds = ["configure terminal", f"router bgp {plan['as']}"]
    for line in plan["to_remove"]:
        cmds.append(f"no {line}")
    if plan["router_id_fix"]:
        cmds.append(f"bgp router-id {plan['router_id_fix']}")
    for line in plan["to_add"]:
        cmds.append(line)
    cmds.append("end")
    return cmds


def compute_frr_plan(baseline_frr: str, current_frr: str) -> dict:
    """
    Handles three cases explicitly via the "action" field:
      - "whole_as_remove": baseline has NO AS, current HAS one -> tear
        down with "no router bgp <AS>".
      - "whole_as_create": baseline HAS an AS, current has NONE -> build
        it fresh with router-id + all baseline lines.
      - "within_as_delta": same AS on both sides -> add/remove neighbor
        and network lines, fix router-id if changed. (Behavior unchanged
        from before — this was already proven correct.)
    """
    t1_parsed = parse_bgp_block(baseline_frr)
    cur_parsed = parse_bgp_block(current_frr)

    t1_as = t1_parsed["as"]
    cur_as = cur_parsed["as"]

    to_remove = sorted(cur_parsed["lines"] - t1_parsed["lines"])
    to_add = sorted(t1_parsed["lines"] - cur_parsed["lines"])

    router_id_fix = None
    if t1_parsed["router_id"] and t1_parsed["router_id"] != cur_parsed["router_id"]:
        router_id_fix = t1_parsed["router_id"]

    if t1_as is None and cur_as is not None:
        action = "whole_as_remove"
        as_number = cur_as
        no_op = False
    elif t1_as is not None and cur_as is None:
        action = "whole_as_create"
        as_number = t1_as
        to_remove = []  # nothing to remove, AS doesn't exist yet
        no_op = False
    elif t1_as is not None and cur_as is not None and t1_as == cur_as:
        action = "within_as_delta"
        as_number = t1_as
        no_op = (not to_remove and not to_add and router_id_fix is None)
    else:
        # t1_as is None and cur_as is None (nothing on either side), or
        # t1_as != cur_as (genuine AS-number change — not yet a supported
        # scenario per the project's design rules, treat as no-op rather
        # than guessing).
        action = "within_as_delta"
        as_number = t1_as or cur_as
        no_op = True

    return {
        "action": action,
        "as": as_number, "t1_as": t1_as, "current_as": cur_as,
        "router_id_fix": router_id_fix, "to_remove": to_remove, "to_add": to_add,
        "no_op": no_op,
    }
