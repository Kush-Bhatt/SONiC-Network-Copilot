"""
sonic/ansible/guard.py
----------------------
Idea 3 — Pre-Apply Scope Guard (design doc §6).

After the AnsibleAgent writes the playbook and BEFORE it runs, read the
playbook and refuse to run it if it goes outside the approved Plan.
Prevention is cheaper than detection plus recovery — rollback on this SONiC
image is not free (no frr-reload), so we catch collateral scope before it
reaches the switches.

This is SYNTACTIC checking of shell-command strings, not a proof (§6.5). A
command can be in-scope and still wrong (right VLAN, wrong tagging) — that is
what Ideas 1 and 2 catch. Three cheap imperfect layers beat one expensive
perfect one we cannot build.

The regex vocabulary (VLAN_PATTERNS / BGP_PATTERNS) is exported and reused by
hitl/expected.py to read BGP facts back off the FRR result — one command
syntax, one source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from sonic.agents.intent_spec import Plan
from sonic.hitl.normalize import norm_asn, norm_device, norm_intf, norm_ip, norm_prefix

# ── Object-extraction patterns (design doc §6.3) ───────────────────────────────

VLAN_PATTERNS = [
    r"config vlan (?:add|del)\s+(\d+)",
    r"config vlan member (add|del)\s+(-u\s+|--untagged\s+)?(\d+)\s+(\S+)",
    r"config interface ip (?:add|remove)\s+Vlan(\d+)\s+(\S+)",
    r"config vlan dhcp_relay (?:add|del)\s+(\d+)\s+(\S+)",
]

INTERFACE_PATTERNS = [
    r"config interface (?:startup|shutdown)\s+(\S+)",
    r"config interface mtu\s+(\S+)\s+(\d+)",
]

BGP_PATTERNS = [
    r"router bgp\s+(\d+)",
    r'neighbor\s+"?([^"\s]+)"?\s+remote-as\s+(\d+)',
    r'network\s+"?(\d+\.\d+\.\d+\.\d+(?:/\d+)?)"?',
    r'bgp router-id\s+"?([^"\s]+)"?',
]

# Destructive commands blocked outright, regardless of scope. `config vlan del`
# and `config interface shutdown <iface>` are handled separately because each
# is legal under a narrow, spec-approved condition.
DENY_ALWAYS = [
    r"\bconfig reload\b",
    r"\bconfig erase\b",
    r"\breboot\b",
    r"\bno router bgp\b",
    r"\bsudo rm\b",
    r"\bshutdown\b",
]

_OBJECT_PATTERNS = VLAN_PATTERNS + BGP_PATTERNS + INTERFACE_PATTERNS

_CMD_KEYS = (
    "raw", "shell", "command", "cmd",
    "ansible.builtin.raw", "ansible.builtin.shell", "ansible.builtin.command",
)


@dataclass
class GuardResult:
    ok: bool
    violations: list[str] = field(default_factory=list)


# ── Playbook / inventory parsing ───────────────────────────────────────────────

def _parse_hosts_ini(text: str) -> dict[str, set[str]]:
    """Return {group_name: {hostname, ...}} plus an "all" group of every host."""
    groups: dict[str, set[str]] = {}
    all_hosts: set[str] = set()
    current: str | None = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            groups.setdefault(current, set())
            continue
        host = line.split()[0]
        all_hosts.add(host)
        if current:
            groups.setdefault(current, set()).add(host)
    groups["all"] = all_hosts
    return groups


def _expand_hosts(hosts_value, groups: dict[str, set[str]]) -> set[str]:
    if hosts_value is None:
        return set()
    out: set[str] = set()
    for token in re.split(r"[,:]\s*", str(hosts_value).strip()):
        token = token.strip()
        if not token:
            continue
        if token == "all":
            out |= groups.get("all", set())
        elif token in groups:
            out |= groups[token]
        else:
            out.add(token)  # a literal hostname
    return out


def _task_command(task: dict) -> str:
    for key in _CMD_KEYS:
        val = task.get(key)
        if isinstance(val, str):
            return val
    return ""


def _when_hosts(task: dict) -> set[str] | None:
    """Hosts a task is guarded to via `when: inventory_hostname == "X"` (or
    `inventory_hostname in [...]`). None means the task is not host-guarded and
    runs across the whole play host group."""
    w = task.get("when")
    if w is None:
        return None
    text = " ".join(str(x) for x in w) if isinstance(w, list) else str(w)
    hosts = set(re.findall(r'inventory_hostname\s*==\s*["\']([^"\']+)["\']', text))
    for grp in re.findall(r"inventory_hostname\s+in\s+\[([^\]]+)\]", text):
        hosts |= set(re.findall(r"""["']([^"']+)["']""", grp))
    return hosts or None


def _iter_tasks(plays):
    for play in plays:
        if not isinstance(play, dict):
            continue
        hosts = play.get("hosts")
        tasks = play.get("tasks") or []
        for task in tasks:
            if isinstance(task, dict):
                yield hosts, task


def _touches_object(cmd: str) -> bool:
    return any(re.search(p, cmd) for p in _OBJECT_PATTERNS)


# ── The three checks ───────────────────────────────────────────────────────────

def _check_host_scope(plays, groups, plan: Plan) -> list[str]:
    """Every switch a mutating (object-touching) task can run on must be
    approved. A task with a `when: inventory_hostname` guard is scoped to those
    hosts; an unguarded mutating task runs across the whole play host group."""
    approved = {norm_device(s) for s in plan.switches}
    touched: set[str] = set()
    for hosts, task in _iter_tasks(plays):
        cmd = _task_command(task)
        if not _touches_object(cmd):
            continue
        base = {norm_device(h) for h in _expand_hosts(hosts, groups)}
        wh = _when_hosts(task)
        if wh:
            wh_n = {norm_device(h) for h in wh}
            # A guarded task runs only where the group and the when overlap; if
            # the inventory was not supplied, fall back to the when hosts.
            task_hosts = (wh_n & base) if base else wh_n
        else:
            task_hosts = base
        touched |= task_hosts

    out_of_scope = sorted(touched - approved)
    if out_of_scope:
        return [
            f"host scope: playbook can modify {out_of_scope} which are not in the "
            f"approved switches {sorted(approved)}"
        ]
    return []


def _extract(cmd: str, pattern: str):
    return re.findall(pattern, cmd)


def _check_object_scope(cmds, plan: Plan) -> list[str]:
    violations: list[str] = []

    if plan.category == "vlan":
        allowed_vlan_ids = set(plan.vlan_ids)
        allowed_members = {
            norm_intf(s.interface): s for s in plan.statements
            if s.object_type == "vlan_member"
        }
        allowed_dhcp_servers = {
            str(s.server) for s in plan.statements if s.object_type == "vlan_dhcp_relay"
        }
        for cmd in cmds:
            for vid in _extract(cmd, VLAN_PATTERNS[0]):
                if int(vid) not in allowed_vlan_ids:
                    violations.append(f"object scope: VLAN {vid} not in approved spec (approved VLANs {sorted(allowed_vlan_ids)})")
            for op, untagged_flag, vid, iface in _extract(cmd, VLAN_PATTERNS[1]):
                if int(vid) not in allowed_vlan_ids:
                    violations.append(f"object scope: VLAN {vid} not in approved spec (approved VLANs {sorted(allowed_vlan_ids)})")
                norm_iface = norm_intf(iface)
                if allowed_members and norm_iface not in allowed_members:
                    violations.append(
                        f"object scope: member {norm_iface} not in approved members {sorted(allowed_members)}"
                    )
                elif op == "add" and norm_iface in allowed_members:
                    cmd_tagging = "untagged" if untagged_flag else "tagged"
                    approved_tagging = allowed_members[norm_iface].tagging
                    if approved_tagging != cmd_tagging:
                        violations.append(
                            f"object scope: member {norm_iface} tagging {cmd_tagging!r} "
                            f"does not match approved {approved_tagging!r}"
                        )
            for vid, _ip in _extract(cmd, VLAN_PATTERNS[2]):
                if int(vid) not in allowed_vlan_ids:
                    violations.append(f"object scope: SVI Vlan{vid} not in approved spec (approved VLANs {sorted(allowed_vlan_ids)})")
            for vid, server in _extract(cmd, VLAN_PATTERNS[3]):
                if int(vid) not in allowed_vlan_ids:
                    violations.append(f"object scope: dhcp_relay VLAN {vid} not in approved spec (approved VLANs {sorted(allowed_vlan_ids)})")
                if server not in allowed_dhcp_servers:
                    violations.append(f"object scope: dhcp_relay server {server} not in approved servers {sorted(allowed_dhcp_servers)}")
        return violations

    if plan.category == "interface":
        allowed_ifaces = {
            norm_intf(s.interface) for s in plan.statements if s.object_type == "interface"
        }
        for cmd in cmds:
            for iface in _extract(cmd, INTERFACE_PATTERNS[0]):
                if norm_intf(iface) not in allowed_ifaces:
                    violations.append(f"object scope: interface {iface} not in approved interfaces {sorted(allowed_ifaces)}")
            for iface, _mtu in _extract(cmd, INTERFACE_PATTERNS[1]):
                if norm_intf(iface) not in allowed_ifaces:
                    violations.append(f"object scope: interface {iface} not in approved interfaces {sorted(allowed_ifaces)}")
        return violations

    # BGP
    bgp = [s for s in plan.statements if s.object_type == "bgp"]
    neighbors = [s for s in plan.statements if s.object_type == "bgp_neighbor"]
    allowed_asns = {norm_asn(s.local_as) for s in bgp} | {norm_asn(n.remote_as) for n in neighbors}
    allowed_peers = {norm_ip(str(n.peer_ip)) for n in neighbors}
    allowed_nets = {
        norm_prefix(str(s.prefix)) for s in plan.statements if s.object_type == "bgp_network"
    }
    rids = {norm_ip(str(s.router_id)) for s in bgp if s.router_id}
    allowed_rid = rids or None

    for cmd in cmds:
        for asn in _extract(cmd, BGP_PATTERNS[0]):
            if norm_asn(asn) not in allowed_asns:
                violations.append(f"object scope: local AS {asn} not in approved spec (approved {sorted(allowed_asns)})")
        for peer, rasn in _extract(cmd, BGP_PATTERNS[1]):
            try:
                peer_n = norm_ip(peer)
            except ValueError:
                peer_n = peer
            if peer_n not in allowed_peers:
                violations.append(f"object scope: neighbor {peer} not in approved peers {sorted(allowed_peers)}")
            if norm_asn(rasn) not in allowed_asns:
                violations.append(f"object scope: neighbor remote-as {rasn} not in approved {sorted(allowed_asns)}")
        for net in _extract(cmd, BGP_PATTERNS[2]):
            try:
                net_n = norm_prefix(net)
            except ValueError:
                net_n = net
            if net_n not in allowed_nets:
                violations.append(f"object scope: advertised network {net} not in approved networks {sorted(allowed_nets)}")
        if allowed_rid is not None:
            for rid in _extract(cmd, BGP_PATTERNS[3]):
                try:
                    rid_n = norm_ip(rid)
                except ValueError:
                    rid_n = rid
                if rid_n not in allowed_rid:
                    violations.append(f"object scope: router-id {rid} not in approved spec (approved {sorted(allowed_rid)})")
    return violations


def _check_deny(cmds, plan: Plan) -> list[str]:
    violations: list[str] = []
    # Deliberately NOT plan.action == "delete": that aggregates the verb across
    # every statement type, so a plan that only deletes a member (not the VLAN
    # itself) would incorrectly authorize `config vlan del`. The exemption is
    # scoped to an actual "vlan" statement with verb="delete".
    vlan_delete_ok = any(
        s.object_type == "vlan" and s.verb == "delete" for s in plan.statements
    )
    shutdown_ok_ifaces = {
        norm_intf(s.interface) for s in plan.statements
        if s.object_type == "interface" and s.admin_state == "down"
    }
    for cmd in cmds:
        m = re.search(r"\bconfig interface shutdown\s+(\S+)", cmd)
        shutdown_exempt = bool(m and norm_intf(m.group(1)) in shutdown_ok_ifaces)
        for pat in DENY_ALWAYS:
            if pat == r"\bshutdown\b" and shutdown_exempt:
                continue
            if re.search(pat, cmd):
                violations.append(f"destructive command blocked: {cmd.strip()!r} (matched {pat})")
        if re.search(r"\bconfig vlan del\b", cmd) and not vlan_delete_ok:
            violations.append(f"destructive command blocked: {cmd.strip()!r} (config vlan del, spec.action is not delete)")
    return violations


# ── Public entry point ─────────────────────────────────────────────────────────

def scope_guard(playbook_text: str, plan: Plan, hosts_text: str = "") -> GuardResult:
    """Refuse to run a playbook that goes outside the approved Plan.

    Returns GuardResult(ok=True) when clean, else ok=False with a list of
    specific, local violation messages suitable for feeding straight into the
    regeneration prompt.
    """
    try:
        plays = yaml.safe_load(playbook_text)
    except yaml.YAMLError as e:
        return GuardResult(ok=False, violations=[f"playbook is not valid YAML: {e}"])

    if isinstance(plays, dict):
        plays = [plays]
    if not isinstance(plays, list):
        return GuardResult(ok=False, violations=["playbook did not parse into a list of plays"])

    groups = _parse_hosts_ini(hosts_text)
    cmds = [_task_command(task) for _hosts, task in _iter_tasks(plays)]
    cmds = [c for c in cmds if c]

    violations: list[str] = []
    violations += _check_host_scope(plays, groups, plan)
    violations += _check_object_scope(cmds, plan)
    violations += _check_deny(cmds, plan)

    return GuardResult(ok=not violations, violations=violations)


def format_guard_failure(spec, result: GuardResult) -> str:
    """Compact report for the operator and the retry prompt (§6.4)."""
    lines = ["PRE-APPLY CHECK FAILED — playbook not executed", ""]
    lines += [f"  - {v}" for v in result.violations]
    lines += ["", "Nothing was applied. Regenerating."]
    return "\n".join(lines)
