"""
sonic/hitl/expected.py
----------------------
Idea 1 — Approved Spec vs. Annotated Diff (design doc §4).

Closes failure modes A (silent no-op) and B (collateral change): expand the
approved IntentSpec into the facts we expect to be true afterwards, extract
the facts that are ACTUALLY true, do set arithmetic, and label every row.

One vocabulary (§4.2): a Fact is a 4-tuple (device, kind, key, value). Both
the expected side and the actual side must produce identical tuples for
identical network state, so both pass through hitl/normalize first.

Actual facts are read from the raw snapshot tier (config_db.json + frr.txt),
not Batfish — the raw tier is the doc's "Tier 1" ground truth, needs no
Batfish server, and keeps these functions unit-testable. (Batfish is reserved
for Idea 2, where the modelled control plane is the point.)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from sonic.agents.intent_spec import Plan
from sonic.ansible.guard import BGP_PATTERNS
from sonic.hitl.normalize import (
    norm_asn,
    norm_device,
    norm_intf,
    norm_ip,
    norm_ip_iface,
    norm_prefix,
    norm_svi,
)
from sonic.netfql.ir import lower


class Fact(NamedTuple):
    device: str
    kind: str
    key: str
    value: str
    # This fact's polarity within one expected-facts set: "present" (should
    # exist afterwards) or "absent" (should not). Identity/equality below
    # ignores it deliberately — actual-side facts are always "present"
    # (they describe reality, not intent), so an expected "absent" fact
    # must still compare equal to its "present" twin to be checked against
    # actual state; only annotate() reads .expect to decide polarity.
    expect: str = "present"

    def __eq__(self, other):
        if not isinstance(other, Fact):
            return NotImplemented
        return (self.device, self.kind, self.key, self.value) == (
            other.device, other.kind, other.key, other.value,
        )

    def __hash__(self):
        return hash((self.device, self.kind, self.key, self.value))


# ── Expected side: derived from the Canonical IR (§4.4) ───────────────────────

# (object_type, attribute) -> (Fact.kind, value formatter). The one place the
# object-centric IR meets the flat fact vocabulary the observed side emits.
# Adding an object type means adding rows here, not a new expansion function.
_ATTR_KIND = {
    ("vlan_member", "tagging"):    ("vlan.member", lambda v: v),
    ("vlan_svi", "ip"):            ("vlan.svi_ip", lambda v: v),
    ("bgp", "local_as"):           ("bgp.local_as", lambda v: v),
    ("bgp", "router_id"):          ("bgp.router_id", lambda v: v),
    ("bgp_neighbor", "remote_as"): ("bgp.neighbor", lambda v: f"remote_as={v}"),
    ("interface", "admin_state"):  ("interface.admin_state", lambda v: v),
    ("interface", "mtu"):          ("interface.mtu", lambda v: v),
}

# Object types whose existence is the whole assertion, with the sentinel value
# the observed side uses for them.
_PRESENCE_KIND = {
    "vlan": ("vlan.exists", "present"),
    "vlan_dhcp_relay": ("vlan.dhcp_relay", "present"),
    "bgp_network": ("bgp.network", "advertised"),
}


def facts_from_ir(objects) -> set[Fact]:
    """Flatten Canonical IR objects into the 4-tuple fact vocabulary. An
    object's verb becomes its facts' polarity: `delete` -> expect="absent"."""
    out: set[Fact] = set()
    for o in objects:
        expect = "absent" if o.verb == "delete" else "present"
        presence = _PRESENCE_KIND.get(o.object_type)
        if presence:
            kind, value = presence
            out.add(Fact(o.device, kind, o.object_id, value, expect=expect))
        for attr, val in o.expected_state.items():
            kind, fmt = _ATTR_KIND[(o.object_type, attr)]
            out.add(Fact(o.device, kind, o.object_id, fmt(val), expect=expect))
    return out


def expected_facts(spec) -> set[Fact]:
    """The facts the intent asserts, via the Canonical IR. Each fact carries its
    own present/absent polarity (Fact.expect) inherited from its IR object's
    verb, which is what makes a `modify` containing both an add and a remove
    verifiable in one pass — see netfql.ir.lower()."""
    return facts_from_ir(lower(spec))


# ── Actual side: read the raw snapshot tier, absolute (single snapshot) ────────

def _vid(vlan_key: str) -> str | None:
    v = vlan_key.replace("Vlan", "").replace("vlan", "").strip()
    return v if v.isdigit() else None


def _vlan_facts_from_configdb(device: str, cfg: dict) -> set[Fact]:
    out: set[Fact] = set()
    for key, val in cfg.get("VLAN", {}).items():
        vid = _vid(key)
        if not vid:
            continue
        out.add(Fact(device, "vlan.exists", vid, "present"))
        servers = (val or {}).get("dhcp_servers@", "")
        for server in filter(None, (s.strip() for s in servers.split(","))):
            try:
                out.add(Fact(device, "vlan.dhcp_relay", f"{vid}/{norm_ip(server)}", "present"))
            except ValueError:
                continue  # malformed address in the config_db field
    for key, val in cfg.get("VLAN_MEMBER", {}).items():
        if "|" not in key:
            continue
        vlan_name, iface = key.split("|", 1)
        vid = _vid(vlan_name)
        if not vid:
            continue
        mode = (val or {}).get("tagging_mode", "untagged")
        out.add(Fact(device, "vlan.member", f"{vid}/{norm_intf(iface)}", mode))
    for key in cfg.get("VLAN_INTERFACE", {}):
        if "|" not in key:
            continue
        ifname, ip = key.split("|", 1)
        vid = _vid(ifname)
        if not vid:
            continue
        try:
            out.add(Fact(device, "vlan.svi_ip", norm_svi(vid), norm_ip_iface(ip)))
        except ValueError:
            continue  # bare Vlan entry or non-address key
    return out


def _bgp_facts_from_frr(device: str, frr_text: str) -> set[Fact]:
    out: set[Fact] = set()
    local_as = None
    for asn in re.findall(BGP_PATTERNS[0], frr_text):   # router bgp <asn>
        local_as = asn
    if local_as is not None:
        out.add(Fact(device, "bgp.local_as", "-", norm_asn(local_as)))
    for rid in re.findall(BGP_PATTERNS[3], frr_text):   # bgp router-id <ip>
        try:
            out.add(Fact(device, "bgp.router_id", "-", norm_ip(rid)))
        except ValueError:
            continue
    for peer, rasn in re.findall(BGP_PATTERNS[1], frr_text):  # neighbor <ip> remote-as <asn>
        try:
            out.add(Fact(device, "bgp.neighbor", norm_ip(peer), f"remote_as={norm_asn(rasn)}"))
        except ValueError:
            continue
    for net in re.findall(BGP_PATTERNS[2], frr_text):   # network <prefix>
        try:
            out.add(Fact(device, "bgp.network", norm_prefix(net), "advertised"))
        except ValueError:
            continue
    return out


def _interface_facts_from_configdb(device: str, cfg: dict) -> set[Fact]:
    out: set[Fact] = set()
    for iface, val in cfg.get("PORT", {}).items():
        val = val or {}
        admin = val.get("admin_status")
        if admin:
            out.add(Fact(device, "interface.admin_state", norm_intf(iface), admin))
        mtu = val.get("mtu")
        if mtu:
            out.add(Fact(device, "interface.mtu", norm_intf(iface), str(mtu)))
    return out


_CONFIG_DB_SUFFIX = ".config_db.json"
_FRR_SUFFIX = ".frr.txt"


def actual_facts(snapshot_dir, category: str) -> set[Fact]:
    """Absolute facts of one category across EVERY switch captured in the
    snapshot (not just the approved ones) so collateral changes on unapproved
    switches surface as NOT-IN-SPEC."""
    raw = Path(snapshot_dir) / "raw"
    out: set[Fact] = set()
    if category == "vlan":
        for p in raw.glob(f"*{_CONFIG_DB_SUFFIX}"):
            device = norm_device(p.name[: -len(_CONFIG_DB_SUFFIX)])
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out |= _vlan_facts_from_configdb(device, cfg)
    elif category == "bgp":
        for p in raw.glob(f"*{_FRR_SUFFIX}"):
            device = norm_device(p.name[: -len(_FRR_SUFFIX)])
            out |= _bgp_facts_from_frr(device, p.read_text(encoding="utf-8"))
    elif category == "interface":
        for p in raw.glob(f"*{_CONFIG_DB_SUFFIX}"):
            device = norm_device(p.name[: -len(_CONFIG_DB_SUFFIX)])
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out |= _interface_facts_from_configdb(device, cfg)
    return out


# ── Current-state reader for the render_intent_spec() approval preview ─────────

def current_vlan_view(plan: Plan, baseline_dir) -> dict[int, dict]:
    """Pure reader of the pre-change state of every VLAN the plan targets,
    scoped to plan.switches, built from the baseline raw snapshot tier via
    _vlan_facts_from_configdb (no second config_db parser). This is display
    data only for the §3.3 approval block — enforcement lives in guard.py and
    expected.annotate(), not here.

    Returns {vlan_id: {"exists": bool, "members": [(iface, tagging), ...],
    "svi_ip": str | None, "dhcp_relay": [server, ...]}}.
    """
    raw = Path(baseline_dir) / "raw"
    target_devices = {norm_device(s) for s in plan.switches}
    facts: set[Fact] = set()
    for p in raw.glob(f"*{_CONFIG_DB_SUFFIX}"):
        device = norm_device(p.name[: -len(_CONFIG_DB_SUFFIX)])
        if device not in target_devices:
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        facts |= _vlan_facts_from_configdb(device, cfg)

    views: dict[int, dict] = {}
    for vid in plan.vlan_ids:
        vid_s = str(vid)
        views[vid] = {
            "exists": any(f.kind == "vlan.exists" and f.key == vid_s for f in facts),
            "members": sorted(
                (f.key.split("/", 1)[1], f.value)
                for f in facts if f.kind == "vlan.member" and f.key.startswith(f"{vid_s}/")
            ),
            "svi_ip": next(
                (f.value for f in facts if f.kind == "vlan.svi_ip" and f.key == norm_svi(vid)), None,
            ),
            "dhcp_relay": sorted(
                f.key.split("/", 1)[1]
                for f in facts if f.kind == "vlan.dhcp_relay" and f.key.startswith(f"{vid_s}/")
            ),
        }
    return views


# ── The comparison (§4.5): two reference sets ──────────────────────────────────

@dataclass
class AnnotatedResult:
    action: str
    matches: list[Fact] = field(default_factory=list)      # approved change is in effect
    missing: list[Fact] = field(default_factory=list)      # approved but not applied (mode A)
    unexpected: list[Fact] = field(default_factory=list)   # not in approved spec (mode B)
    ok: bool = True            # nothing missing AND nothing extra (strict)
    satisfied: bool = True     # p ∈ [[s]] — every approved fact is in effect


def annotate(plan: Plan, baseline_dir, t2_dir) -> AnnotatedResult:
    """Presence of what we promised is checked against the absolute post-state
    (pre-existing state is still a satisfied promise). Absence of anything extra
    is checked against the delta (baseline -> t2), because unrelated pre-existing
    config is not our doing. Each expected fact carries its own present/absent
    polarity (Fact.expect) so one comparison handles a plan with both a create
    and a delete statement in the same pass — see expected_facts()."""
    expected = expected_facts(plan)
    post = actual_facts(t2_dir, plan.category)
    pre = actual_facts(baseline_dir, plan.category)
    delta = post - pre  # facts newly true at t2

    matches: list[Fact] = []
    missing: list[Fact] = []
    for e in sorted(expected):
        present = e in post
        applied = present if e.expect == "present" else not present
        (matches if applied else missing).append(e)

    unexpected = [d for d in sorted(delta) if d not in expected]

    # Two distinct verdicts. `satisfied` is the automation gate: an approved
    # change that did not happen is a defect. `ok` additionally requires no
    # residual, which is a question for the human, not a hard failure.
    satisfied = not missing
    ok = satisfied and not unexpected
    return AnnotatedResult(
        action=plan.action, matches=matches, missing=missing, unexpected=unexpected,
        ok=ok, satisfied=satisfied,
    )
