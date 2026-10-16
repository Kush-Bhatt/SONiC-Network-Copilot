"""
sonic/batfish/checks.py
-----------------------
Idea 2 — Fixed Batfish Assertion Library (design doc §5).

A hardcoded `intent category -> list of checks` table. The LLM's only role is
choosing the category, which is already a validated enum. No model ever writes
verification code — this replaces the legacy agents/batfish_agent.py, which
generated assertion code per run and produced most of our false alarms.

Batfish reasons about configuration as written plus a modelled control plane
(§5.2): it can tell you a BGP session is compatibly configured and *should*
come up; it cannot tell you it is Established on the box. So:
  - incompatible / undefined-reference -> FAIL (blocks acceptance)
  - unestablished BGP session          -> WARN (advisory; normal mid-change)

The pure classify_* functions hold the verdict logic and are unit-tested with
hand-built DataFrames. The check_* wrappers fetch frames from a live Batfish
session and delegate to them.
"""

from __future__ import annotations

from dataclasses import dataclass

from pybatfish.client.session import Session

from sonic.agents.intent_spec import Plan
from sonic.config.settings import BATFISH_HOST
from sonic.hitl.normalize import norm_asn, norm_device, norm_intf, norm_ip

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"

# Configured_Status values that mean the peering is sanely configured (§5.5).
GOOD_BGP_STATUS = {"UNIQUE_MATCH", "DYNAMIC_MATCH"}


@dataclass
class CheckResult:
    name: str
    status: str          # PASS | FAIL | WARN
    detail: str = ""


# ── Pure classifiers (unit-tested with hand-built DataFrames) ──────────────────

def classify_parse_status(df) -> CheckResult:
    if "File_Status" in df.columns:
        col = "File_Status"
    elif "Status" in df.columns:
        col = "Status"
    else:
        return CheckResult("files_parsed", WARN, "fileParseStatus frame has no status column")
    bad = df[df[col] != "PASSED"]
    if len(bad):
        return CheckResult("files_parsed", FAIL, f"{len(bad)} file(s) not PASSED")
    return CheckResult("files_parsed", PASS)


def classify_init_issues(df) -> CheckResult:
    if len(df):
        return CheckResult("no_init_issues", FAIL, f"{len(df)} init issue(s)")
    return CheckResult("no_init_issues", PASS)


def classify_undefined_refs(df) -> CheckResult:
    if len(df):
        return CheckResult("no_undefined_refs", FAIL, f"{len(df)} undefined reference(s)")
    return CheckResult("no_undefined_refs", PASS)


def classify_bgp_compatibility(df) -> CheckResult:
    if "Configured_Status" not in df.columns:
        return CheckResult("bgp_compatible", WARN, "no Configured_Status column")
    if df.empty:
        # No sessions modelled; conformance (neighbor present) is a separate check.
        return CheckResult("bgp_compatible", PASS, "no BGP sessions modelled")
    bad = df[~df["Configured_Status"].isin(GOOD_BGP_STATUS)]
    if len(bad):
        statuses = sorted(set(bad["Configured_Status"].astype(str)))
        return CheckResult("bgp_compatible", FAIL, f"incompatible session status(es): {statuses}")
    return CheckResult("bgp_compatible", PASS)


def classify_bgp_established(df) -> CheckResult:
    col = "Established_Status" if "Established_Status" in df.columns else None
    if col is None:
        return CheckResult("bgp_established", WARN, "no Established_Status column")
    not_up = df[df[col] != "ESTABLISHED"]
    if len(not_up):
        return CheckResult("bgp_established", WARN, f"{len(not_up)} session(s) not ESTABLISHED (may be a normal mid-change state)")
    return CheckResult("bgp_established", PASS)


def classify_neighbor_present(df, plan: Plan) -> CheckResult:
    neighbors = [s for s in plan.statements if s.object_type == "bgp_neighbor"]
    if not neighbors:
        return CheckResult("neighbor_present", PASS, "no neighbors in spec")
    if "Remote_IP" not in df.columns or "Remote_AS" not in df.columns:
        return CheckResult("neighbor_present", WARN, "bgpPeerConfiguration frame missing columns")
    have = {
        (norm_ip(str(ip)), norm_asn(asn))
        for ip, asn in zip(df["Remote_IP"], df["Remote_AS"])
        if str(ip) not in ("", "None")
    }
    missing = [
        f"{n.peer_ip} remote-as {n.remote_as}"
        for n in neighbors
        if (norm_ip(str(n.peer_ip)), norm_asn(n.remote_as)) not in have
    ]
    if missing:
        return CheckResult("neighbor_present", FAIL, f"neighbor(s) not configured: {missing}")
    return CheckResult("neighbor_present", PASS)


def classify_vlan_defined(df, plan: Plan) -> CheckResult:
    if "VLAN_ID" not in df.columns:
        return CheckResult("vlan_defined", WARN, "switchedVlanProperties frame missing VLAN_ID")
    present = {int(v) for v in df["VLAN_ID"]}
    missing = [vid for vid in plan.vlan_ids if vid not in present]
    if missing:
        return CheckResult("vlan_defined", FAIL, f"VLAN(s) {missing} not defined")
    return CheckResult("vlan_defined", PASS)


def classify_members_exist(df, plan: Plan) -> CheckResult:
    """Every member interface in the plan must actually exist on a device.
    Catches the classic hallucination: Ethernet16 on a switch with Ethernet0-7."""
    members = [s for s in plan.statements if s.object_type == "vlan_member"]
    if not members:
        return CheckResult("members_exist", PASS, "no members in spec")
    if "Interface" not in df.columns:
        return CheckResult("members_exist", WARN, "interfaceProperties frame missing Interface")
    existing = {norm_intf(getattr(i, "interface", str(i))) for i in df["Interface"]}
    missing = [m.interface for m in members if norm_intf(m.interface) not in existing]
    if missing:
        return CheckResult("members_exist", FAIL, f"member interface(s) do not exist: {missing}")
    return CheckResult("members_exist", PASS)


# ── Live-session fetch wrappers (delegate to classifiers) ──────────────────────

def _frame(question, snapshot):
    return question.answer(snapshot=snapshot).frame()


def _node_spec(plan: Plan) -> str:
    return "/(" + "|".join(norm_device(n) for n in plan.switches) + ")/"


def check_files_parsed(bf, snap, spec):
    try:
        return classify_parse_status(_frame(bf.q.fileParseStatus(), snap))
    except Exception as e:
        return CheckResult("files_parsed", WARN, f"query failed: {e}")


def check_no_init_issues(bf, snap, spec):
    try:
        return classify_init_issues(_frame(bf.q.initIssues(), snap))
    except Exception as e:
        return CheckResult("no_init_issues", WARN, f"query failed: {e}")


def check_no_undefined_refs(bf, snap, spec):
    try:
        return classify_undefined_refs(_frame(bf.q.undefinedReferences(), snap))
    except Exception as e:
        return CheckResult("no_undefined_refs", WARN, f"query failed: {e}")


def check_bgp_compatible(bf, snap, spec):
    try:
        return classify_bgp_compatibility(_frame(bf.q.bgpSessionCompatibility(nodes=_node_spec(spec)), snap))
    except Exception as e:
        return CheckResult("bgp_compatible", WARN, f"query failed: {e}")


def check_bgp_established(bf, snap, spec):
    try:
        return classify_bgp_established(_frame(bf.q.bgpSessionStatus(nodes=_node_spec(spec)), snap))
    except Exception as e:
        return CheckResult("bgp_established", WARN, f"query failed: {e}")


def check_neighbor_present(bf, snap, spec):
    try:
        return classify_neighbor_present(_frame(bf.q.bgpPeerConfiguration(nodes=_node_spec(spec)), snap), spec)
    except Exception as e:
        return CheckResult("neighbor_present", WARN, f"query failed: {e}")


def check_vlan_defined(bf, snap, spec):
    try:
        return classify_vlan_defined(_frame(bf.q.switchedVlanProperties(nodes=_node_spec(spec)), snap), spec)
    except Exception as e:
        return CheckResult("vlan_defined", WARN, f"query failed: {e}")


def check_members_exist(bf, snap, spec):
    try:
        return classify_members_exist(_frame(bf.q.interfaceProperties(nodes=_node_spec(spec)), snap), spec)
    except Exception as e:
        return CheckResult("members_exist", WARN, f"query failed: {e}")


CHECKS = {
    "_universal": [check_files_parsed, check_no_init_issues, check_no_undefined_refs],
    "vlan": [check_vlan_defined, check_members_exist],
    "bgp": [check_bgp_compatible, check_bgp_established, check_neighbor_present],
}


def run_checks(spec, snapshot_dir) -> list[CheckResult]:
    """Build a Batfish session on the single post-change snapshot and run the
    universal + category checks for the spec. Never raises — a Batfish failure
    degrades a check to WARN rather than crashing the pipeline."""
    try:
        bf = Session(host=BATFISH_HOST)
        bf.set_network("sonic-network-checks")
        bf.init_snapshot(str(snapshot_dir), name="checks", overwrite=True)
    except Exception as e:
        return [CheckResult("batfish_session", WARN, f"could not init snapshot: {e}")]

    results: list[CheckResult] = []
    for fn in CHECKS["_universal"] + CHECKS.get(spec.category, []):
        results.append(fn(bf, "checks", spec))
    return results


def has_blocking_failure(results: list[CheckResult]) -> bool:
    return any(r.status == FAIL for r in results)
