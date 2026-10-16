"""
sonic/hitl/render.py
--------------------------
Consumes the SNAPSHOT differential diff (facts.py's extract_facts()) only.
The raw config_db.json/frr.txt diff (extract_raw_diff()) has no renderer
yet — deferred to when it's actually needed downstream.

  render_for_human(facts)   -> entity-correlated, SONiC-CLI-shaped text
                                (one block per VLAN / BGP neighbor,
                                mirroring `show vlan brief` /
                                `show ip bgp summary`).
  render_for_machine(facts) -> full structured per-switch dict, uncorrelated,
                                nothing filtered — for downstream agent use.

No LLM in either — deterministic formatting only.
"""
from sonic.hitl.facts import Fact
# from sonic.hitl.normalize import norm_intf

import re

_VLAN_RE = re.compile(r"Vlan(\d+)")


# ═══════════════════════════════════════════════════════════════════════
# IntentSpec + verification renderers (Ideas 1 & 2)
# ═══════════════════════════════════════════════════════════════════════
# These consume the IntentSpec (agents/intent_spec), the annotation
# (hitl/expected.AnnotatedResult) and the fixed checks
# (batfish/checks.CheckResult). Template-only, no LLM — the text the operator
# reads is provably derived from the object the system acts on.


def _render_vlan_plan(plan) -> list[str]:
    """The VLAN branch of render_intent_spec(), grouped by target node.

    One block per node, because a plan can now say different things about
    different nodes (an SVI scoped to a single switch, distinct addresses per
    switch, ...) — a flat all-switches block would re-hide exactly the
    asymmetry this refactor exists to expose."""
    vlan_label = ", ".join(str(v) for v in plan.vlan_ids)
    lines = [f"  VLAN {vlan_label} — {plan.action.upper()}", ""]

    for node in plan.switches:
        stmts = [s for s in plan.statements if s.target_node == node]
        lines.append(f"  {node}")
        mark = {"create": "+", "modify": "~", "delete": "-"}
        # touched_ifaces = set()
        for s in stmts:
            if s.object_type == "vlan":
                lines.append(f"    {mark[s.verb]} vlan {s.vlan_id}")
            elif s.object_type == "vlan_member":
                lines.append(f"    {mark[s.verb]} member Vlan{s.vlan_id}  {s.interface} ({s.tagging})")
                # touched_ifaces.add(norm_intf(s.interface))
            elif s.object_type == "svi":
                ip = "removed" if s.ip is None else str(s.ip)
                lines.append(f"    {mark[s.verb]} svi  Vlan{s.vlan_id}  {ip}")
            elif s.object_type == "vlan_dhcp_relay":
                lines.append(f"    {mark[s.verb]} dhcp_relay Vlan{s.vlan_id}  {s.server}")

        # # Only diffable against a baseline when the plan touches one VLAN.
        # view = current.get(plan.vlan_ids[0]) if (current and len(plan.vlan_ids) == 1) else None
        # if view and view.get("exists"):
        #     other = [(i, t) for i, t in view["members"] if norm_intf(i) not in touched_ifaces]
        #     note = f"{len(other)} other member(s)" if other else "no other members"
        #     lines.append(f"    Unchanged: {note}")
        
        lines.append("")

    return lines


def render_intent_spec(spec) -> str:
    """
    The §3.3 pre-apply approval block.

    This renderer displays only the interpreted intent that the operator is
    being asked to approve. It performs no network lookups and never depends on
    live state or snapshots.
    """
    plan = spec
    lines = ["I understood your request as:", ""]
    if plan.category == "vlan":
        lines += _render_vlan_plan(plan)
    elif plan.category == "bgp":
        bgp = [s for s in plan.statements if s.object_type == "bgp"]
        neighbors_s = [s for s in plan.statements if s.object_type == "bgp_neighbor"]
        networks_s = [s for s in plan.statements if s.object_type == "bgp_network"]
        neighbors = ", ".join(f"{n.peer_ip} remote-as {n.remote_as}" for n in neighbors_s) or "none"
        networks = ", ".join(str(n.prefix) for n in networks_s) or "none"
        lines += [
            "  Category:  BGP",
            f"  Action:    {plan.action}",
            f"  Switches:  {', '.join(plan.switches)}",
            f"  Local AS:  {', '.join(str(s.local_as) for s in bgp) or 'none'}",
            f"  Router-ID: {', '.join(str(s.router_id) for s in bgp if s.router_id) or 'none'}",
            f"  Neighbors: {neighbors}",
            f"  Networks:  {networks}",
        ]
    elif plan.category == "interface":
        ifaces = [s for s in plan.statements if s.object_type == "interface"]
        lines += [
            "  Category:    INTERFACE",
            f"  Switches:    {', '.join(plan.switches)}",
            f"  Interfaces:  {', '.join(s.interface for s in ifaces)}",
            f"  Admin state: {', '.join(sorted({s.admin_state for s in ifaces if s.admin_state})) or 'unchanged'}",
            f"  MTU:         {', '.join(sorted({str(s.mtu) for s in ifaces if s.mtu is not None})) or 'unchanged'}",
        ]
    else:
        lines.append(f"  {plan!r}")
        return "\n".join(lines)

    # The IR-derived view. Indented under a header rather than replacing the
    # prose above, which carries the current-vs-proposed delta NetFQL cannot.
    from sonic.netfql.ir import lower
    from sonic.netfql.render import render_netfql

    netfql = render_netfql(lower(plan))
    if netfql:
        lines += ["", "NetFQL:", ""]
        lines += ["  " + line for line in netfql.splitlines()]
    return "\n".join(lines)


def _fact_row(fact, label: str) -> str:
    return f"  {fact.device:12s} {fact.kind:14s} {fact.key:24s} {fact.value:14s} {label}"


def render_annotated_diff(annotated, checks=None) -> str:
    """The §4.6 / §5 verdict block: the annotated spec-vs-actual table plus the
    fixed Batfish check results."""
    lines = ["APPROVED SPEC VERIFICATION", ""]
    for f in annotated.matches:
        lines.append(_fact_row(f, "MATCHES APPROVED SPEC"))
    for f in annotated.missing:
        label = "APPROVED-DELETION NOT APPLIED" if f.expect == "absent" else "APPROVED BUT NOT APPLIED"
        lines.append(_fact_row(f, label))
    for f in annotated.unexpected:
        lines.append(_fact_row(f, "ASSUMED BY SYSTEM — NOT REQUESTED"))

    verdict = "satisfied" if annotated.satisfied else "NOT SATISFIED"
    summary = f"{len(annotated.missing)} missing, {len(annotated.unexpected)} assumed"
    lines += ["", f"  approved spec: {verdict}  ({summary})"]
    if annotated.unexpected:
        lines += ["", "  The system also changed things you did not request (above).",
                  "  Approve only if these assumptions are acceptable."]

    if checks:
        lines += ["", "Batfish checks:"]
        for c in checks:
            detail = f" — {c.detail}" if c.detail else ""
            lines.append(f"  [{c.status}] {c.name}{detail}")
    return "\n".join(lines)



# ═══════════════════════════════════════════════════════════════════════
# Human-facing renderer
# ═══════════════════════════════════════════════════════════════════════



def _vlan_key(f: Fact) -> str:
    #
    # switchedVlanProperties
    #
    if f.table == "vlan":
        return f.id.split(":")[-1]

    #
    # interfaceProperties
    #
    if f.table == "interface":

        #
        # Preferred: Batfish's declared interface name
        #
        names = f.detail.get("Declared_Names")
        if names:
            m = _VLAN_RE.search(names[0])
            if m:
                return m.group(1)

        #
        # Fallback: Interface encoded in Fact.id
        #
        m = _VLAN_RE.search(f.id)
        if m:
            return m.group(1)

    return "?"


def _bgp_key(f: Fact) -> str:
    parts = f.id.split(":")
    return parts[-1] if len(parts) >= 2 else "?"

def _merge_details(facts: list[Fact]) -> dict:
    merged = {}

    for fact in facts:
        for key, value in fact.detail.items():
            if key in merged and merged[key] != value:
                raise ValueError(
                    f"Conflicting value for '{key}' while merging "
                    f"{fact.id}: {merged[key]!r} vs {value!r}"
                )
            merged[key] = value

    return merged

def _render_vlan_block(vlan_id: str, node_facts: list[Fact]) -> str:
    change = node_facts[0].change

    if change == "modified":
        merged = {}
        for f in node_facts:
            merged.update(f.detail)  # already {field: {"old","new"}}, no conflict risk — each row is one entity
        lines = [f"  ~ VLAN {vlan_id} modified"]
        field_order = [
            ("Admin_Up", "admin_up"), ("Active", "active"),
            ("Auto_State_VLAN", "auto_state_vlan"), ("All_Prefixes", "ip_assigned"),
            ("Channel_Group", "portchannel"), ("Switchport", "switchport"),
            ("Switchport_Mode", "switchport_mode"), ("VRF", "vrf"),
            ("Interfaces", "interfaces"),
        ]
        for src_key, label in field_order:
            if src_key in merged:
                old, new = merged[src_key]["old"], merged[src_key]["new"]
                lines.append(f"      {label}: {old} -> {new}")
        return "\n".join(lines)

    merged = {}
    for f in node_facts:
        for key, value in f.detail.items():
            if key in merged:
                if merged[key] != value:
                    raise ValueError(
                        f"Conflicting value for '{key}' while rendering "
                        f"{f.table} ({f.id}): "
                        f"{merged[key]!r} vs {value!r}"
                    )
            else:
                merged[key] = value

    icon = "+" if change == "added" else "-"
    lines = [f"  {icon} VLAN {vlan_id} {change}"]

    field_order = [
        ("Admin_Up", "admin_up"),
        ("Active", "active"),
        ("Auto_State_VLAN", "auto_state_vlan"),
        ("All_Prefixes", "ip_assigned"),
        ("Channel_Group", "portchannel"),
        ("Switchport", "switchport"),
        ("Switchport_Mode", "switchport_mode"),
        ("VRF", "vrf"),
        ("Interfaces", "interfaces"),
    ]
    for src_key, label in field_order:
        if src_key in merged:
            lines.append(f"      {label}: {merged[src_key]}")
    return "\n".join(lines)


def _render_bgp_block(remote_ip: str, node_facts: list[Fact]) -> str:
    change = node_facts[0].change

    if change == "modified":
        merged = {}
        for f in node_facts:
            merged.update(f.detail)
        lines = [f"  ~ BGP neighbor {remote_ip} modified"]
        field_order = [
            ("Local_AS", "local_as"),
            ("Remote_AS", "remote_as"),
            ("Local_IP", "local_ip"),
            ("Session_Type", "session_type"),
            ("Established_Status", "state"),
            ("Configured_Status", "configured_status"),
            ("Route_Reflector_Client", "route_reflector_client"),
            ("Send_Community", "send_community"),
            ("Is_Passive", "is_passive"),
        ]
        for src_key, label in field_order:
            if src_key in merged:
                old, new = merged[src_key]["old"], merged[src_key]["new"]
                lines.append(f"      {label}: {old} -> {new}")
        return "\n".join(lines)

    merged = {}

    for f in node_facts:
        for key, value in f.detail.items():
            if key in merged:
                if merged[key] != value:
                    raise ValueError(
                        f"Conflicting value for '{key}' while rendering "
                        f"{f.table} ({f.id}): "
                        f"{merged[key]!r} vs {value!r}"
                    )
            else:
                merged[key] = value

    icon = "+" if change == "added" else "-"
    lines = [f"  {icon} BGP neighbor {remote_ip} {change}"]

    field_order = [
        ("Local_AS", "local_as"),
        ("Remote_AS", "remote_as"),
        ("Local_IP", "local_ip"),
        ("Session_Type", "session_type"),
        ("Established_Status", "state"),
        ("Configured_Status", "configured_status"),
        ("Route_Reflector_Client", "route_reflector_client"),
        ("Send_Community", "send_community"),
        ("Is_Passive", "is_passive"),
    ]
    for src_key, label in field_order:
        if src_key in merged:
            lines.append(f"      {label}: {merged[src_key]}")
    return "\n".join(lines)


def _summary_token(change: str, kind: str, count: int) -> str:
    sign = {"added": "+", "removed": "-", "modified": "~"}.get(change, "?")
    noun = kind if count == 1 else f"{kind}s"
    return f"{sign}{count} {noun}"


def _node_summary_line(label: str, blocks: list[tuple[str, str, str]]) -> str:
    """One deterministic summary line per switch, e.g.
    'SONiC-VS-3 — +1 VLAN, -1 BGP neighbor' (added first, then kind A→Z)."""
    if not blocks:
        return f"  {label} — no changes"

    counts: dict[tuple[str, str], int] = {}
    for kind, change, _ in blocks:
        counts[(change, kind)] = counts.get((change, kind), 0) + 1

    ordered = sorted(counts, key=lambda k: (
        {"added": 0, "modified": 1, "removed": 2}.get(k[0], 3), k[1]
    ))
    tokens = [_summary_token(change, kind, counts[(change, kind)]) for change, kind in ordered]
    return f"  {label} — {', '.join(tokens)}"


def _node_blocks(node_facts: list[Fact]) -> list[tuple[str, str, str]]:
    """Group a switch's facts into rendered (kind, change, text) blocks.

    Single source of truth for both the summary counts and the detail
    section, so the two can never disagree. Deterministic: VLANs then BGP
    neighbors (each sorted by id), then remaining facts in fact order.
    """
    vlan_facts = [f for f in node_facts if f.table in ("vlan", "interface")]
    bgp_facts = [f for f in node_facts if f.table in ("bgp_neighbor", "bgp_session_status", "bgp_session_compat")]
    other_facts = [f for f in node_facts if f not in vlan_facts and f not in bgp_facts]

    blocks: list[tuple[str, str, str]] = []

    vlan_groups: dict[str, list[Fact]] = {}
    for f in vlan_facts:
        vlan_groups.setdefault(_vlan_key(f), []).append(f)
    for vlan_id, group in sorted(vlan_groups.items()):
        blocks.append(("VLAN", group[0].change, _render_vlan_block(vlan_id, group)))

    bgp_groups: dict[str, list[Fact]] = {}
    for f in bgp_facts:
        bgp_groups.setdefault(_bgp_key(f), []).append(f)
    for remote_ip, group in sorted(bgp_groups.items()):
        blocks.append(("BGP neighbor", group[0].change, _render_bgp_block(remote_ip, group)))

    for f in other_facts:
        icon = {"added": "+", "removed": "-", "modified": "~"}.get(f.change, "?")
        detail_str = ", ".join(f"{k}={v}" for k, v in f.detail.items())
        text = (f"  {icon} [{f.table}] {f.change}  ({detail_str})" if detail_str
                else f"  {icon} [{f.table}] {f.change}")
        blocks.append((f.table, f.change, text))

    return blocks


def render_for_human(facts: list[Fact], all_switches: list[str] = None) -> str:
    by_node: dict[str, list[Fact]] = {}
    for f in facts:
        by_node.setdefault(f.node.lower(), []).append(f)

    display_name = {name.lower(): name for name in (all_switches or [])}
    nodes = sorted(set(by_node.keys()) | set(display_name.keys()))

    per_node = [
        (display_name.get(node_key, node_key), _node_blocks(by_node.get(node_key, [])))
        for node_key in nodes
    ]

    total_added = sum(1 for _, blocks in per_node for _, change, _ in blocks if change == "added")
    total_removed = sum(1 for _, blocks in per_node for _, change, _ in blocks if change == "removed")
    total_modified = sum(1 for _, blocks in per_node for _, change, _ in blocks if change == "modified")
    total = total_added + total_removed + total_modified
    changed_switches = sum(1 for _, blocks in per_node if blocks)

    # ── Summary ──
    if total == 0:
        lines = ["Summary: no differences detected"]
    else:
        chg = "change" if total == 1 else "changes"
        sw = "switch" if changed_switches == 1 else "switches"
        lines = [
        f"Summary: {total} {chg} across {changed_switches} {sw} "
        f"({total_added} added, {total_modified} modified, {total_removed} removed)"
    ]
    for label, blocks in per_node:
        lines.append(_node_summary_line(label, blocks))

    # ── Details ──
    for label, blocks in per_node:
        lines.append("")
        lines.append(f"Switch: {label}")
        if blocks:
            lines.extend(text for _, _, text in blocks)
        else:
            lines.append("  (no changes detected)")

    return "\n".join(lines).rstrip()


# ═══════════════════════════════════════════════════════════════════════
# Machine-facing renderer — flat, full detail, uncorrelated
# ═══════════════════════════════════════════════════════════════════════

def render_for_machine(facts: list[Fact], all_switches: list[str] = None) -> dict:
    by_node: dict[str, list[dict]] = {}
    for f in facts:
        by_node.setdefault(f.node.lower(), []).append({
            "id": f.id, "table": f.table, "change": f.change, "detail": f.detail,
        })

    display_name = {name.lower(): name for name in (all_switches or [])}
    nodes = sorted(set(by_node.keys()) | set(display_name.keys()))

    return {display_name.get(n, n): by_node.get(n, []) for n in nodes}


if __name__ == "__main__":
    from sonic.hitl.facts import extract_facts
    from sonic.config.settings import BASE_DIR
    import json

    t1 = BASE_DIR / "snapshots" / "diff_t1"
    t2 = BASE_DIR / "snapshots" / "diff_t2"
    facts = extract_facts(t1, t2)
    switches = ["SONiC-VS-1", "SONiC-VS-2", "SONiC-VS-3"]

    print("=== FOR HUMAN ===\n")
    print(render_for_human(facts, all_switches=switches))

    print("\n\n=== FOR MACHINE ===\n")
    print(json.dumps(render_for_machine(facts, all_switches=switches), indent=2, default=str))
