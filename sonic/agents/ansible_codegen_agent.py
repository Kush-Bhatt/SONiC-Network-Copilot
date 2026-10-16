"""
sonic/agents/ansible_codegen_agent.py
----------------------------------------
AnsibleCodeGenAgent — LLM/SLM agent that maps IR objects to Ansible tasks
AND assembles the full playbook. Self-contained: no orchestrator/codegen.py
dependency — playbook structure (header, per-device save tasks, YAML
assembly) lives here as agent methods, since that's now the agent's whole job.

Per-object mapping (not whole-IR-at-once) — see map_object() docstring for
why. Cross-object facts (device_local_as for bgp_neighbor/bgp_network) are
still resolved in plain Python before any LLM call, never left for the
model to infer — same discipline as prior_results in the VLAN/BGP IR
sub-agents.

Every response is validated against AnsibleTask before being trusted, and
every string field is scrubbed for stray backslash-escaped quotes — a real
JSON-escaping failure observed in testing (the model emitted \" as literal
characters inside a raw/when value instead of a real quote, which would
have broken the shell command and silently no-op'd the when: clause).
"""
from __future__ import annotations

import json

import yaml
from pydantic import BaseModel, Field, field_validator

from sonic.agents.base_agent import BaseAgent
from sonic.netfql.ir import IRObject


def _scrub(value: str) -> str:
    """Removes stray backslash-escaped quotes the model sometimes leaves
    in after JSON decoding (double-escaping artifact) — \" -> "."""
    return value.replace('\\"', '"').replace("\\'", "'")


class AnsibleTask(BaseModel):
    name: str
    raw: str
    when: str
    register_: str | None = Field(default=None, alias="register")
    failed_when: str | None = None
    changed_when: bool | None = None
    ignore_errors: bool | None = None

    model_config = {"populate_by_name": True}

    @field_validator("raw", "when", "failed_when", mode="before")
    @classmethod
    def _clean(cls, v):
        return _scrub(v) if isinstance(v, str) else v

    @field_validator("raw")
    @classmethod
    def _no_dellemc(cls, v: str) -> str:
        if "dellemc.enterprise_sonic" in v:
            raise ValueError("dellemc.enterprise_sonic modules are forbidden — raw CLI/vtysh only")
        return v

    def to_task_dict(self) -> dict:
        d = {"name": self.name, "raw": self.raw, "when": self.when}
        if self.register_ is not None:
            d["register"] = self.register_
        if self.failed_when is not None:
            d["failed_when"] = self.failed_when
        if self.changed_when is not None:
            d["changed_when"] = self.changed_when
        if self.ignore_errors is not None:
            d["ignore_errors"] = self.ignore_errors
        return d


class AnsibleTaskList(BaseModel):
    tasks: list[AnsibleTask] = Field(min_length=1)


class CodegenError(Exception):
    """Raised when the agent can't safely produce a task for an IR object —
    missing cross-object fact, unknown object_type/verb, or output that
    fails schema validation. Never silently guessed."""


_TEMPLATES: dict[tuple[str, str], str] = {
    ("vlan", "create"): """
Template — VLAN create:
  1. {{"name": "Add VLAN<id> on <device>", "raw": "config vlan add <id>", "when": "inventory_hostname == \\"<device>\\"", "ignore_errors": true}}
  2. {{"name": "Verify VLAN<id> exists", "raw": "show vlan brief | grep -w <id>", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_check", "changed_when": false, "failed_when": "vlan_check.rc != 0 or vlan_check.stdout == ''"}}
""",
    ("vlan", "delete"): """
Template — VLAN delete:
  1. {{"name": "Delete VLAN<id> on <device>", "raw": "config vlan del <id>", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify VLAN<id> removed", "raw": "show vlan brief | grep -w <id>", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_check", "changed_when": false, "failed_when": "vlan_check.rc == 0"}}
""",
    ("vlan_svi", "create"): """
Template — VLAN SVI IP create (expected_state.ip is REQUIRED, never omit or invent):
  1. {{"name": "Assign SVI IP on Vlan<id> (<device>)", "raw": "config interface ip add Vlan<id> <ip>", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify VLAN<id> SVI IP assignment", "raw": "show ip interfaces", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_svi_check", "changed_when": false, "failed_when": "'<ip>' not in vlan_svi_check.stdout"}}
""",
    ("vlan_member", "create"): """
Template — VLAN member create (expected_state.tagging is "tagged" or "untagged"):
  1. {{"name": "Clear stray INTERFACE table entry for <iface> on <device>", "raw": "sonic-db-cli CONFIG_DB del \\"INTERFACE|<iface>\\"", "when": "inventory_hostname == \\"<device>\\"", "ignore_errors": true, "changed_when": false}}
  2. {{"name": "Add <iface> as <tagging> member of VLAN<id> on <device>", "raw": "config vlan member add [--untagged if tagging==untagged, else nothing] <id> <iface>", "when": "inventory_hostname == \\"<device>\\"", "ignore_errors": true}}
  3. {{"name": "Verify <iface> is a member of VLAN<id>", "raw": "sonic-db-cli CONFIG_DB hget \\"VLAN_MEMBER|Vlan<id>|<iface>\\" tagging_mode", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_member_check", "changed_when": false, "failed_when": "vlan_member_check.stdout.strip() != '<tagging>'"}}
NOTE: do NOT use "show vlan brief | grep -w <id>" for the verify step — SONiC
only prints the VLAN ID on the FIRST port row of a multi-member VLAN; every
additional port's row has a blank VLAN ID column, so grep -w <id> silently
misses any port beyond the first member and this check would falsely pass
or falsely fail depending on ordering. The CONFIG_DB hget above reads the
exact field directly and also verifies the tagging mode is correct, not
just that the port name appears somewhere in output.
""",
    ("vlan_member", "delete"): """
Template — VLAN member delete:
  1. {{"name": "Remove <iface> from VLAN<id> on <device>", "raw": "config vlan member del <id> <iface>", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify <iface> removed from VLAN<id>", "raw": "sonic-db-cli CONFIG_DB hget \\"VLAN_MEMBER|Vlan<id>|<iface>\\" tagging_mode", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_member_check", "changed_when": false, "failed_when": "vlan_member_check.stdout.strip() != ''"}}
NOTE: do NOT use "show vlan brief | grep -w <id>" for the verify step — same
multi-row table issue as the create template above. sonic-db-cli hget on a
deleted member returns an empty string, which is what failed_when checks for.
""",
        ("vlan_dhcp_relay", "create"): """
Template — VLAN DHCP relay create. object_id is "<vlan_id>/<server_ip>" combined — split on "/" to get vlan_id and server_ip separately.
  1. {{"name": "Add DHCP relay <server_ip> to VLAN<vlan_id> on <device>", "raw": "config vlan dhcp_relay add <vlan_id> <server_ip>", "when": "inventory_hostname == \\"<device>\\"", "ignore_errors": true}}
  2. {{"name": "Verify DHCP relay <server_ip> on VLAN<vlan_id>", "raw": "sonic-db-cli CONFIG_DB hget \\"VLAN|Vlan<vlan_id>\\" \\"dhcp_servers@\\"", "when": "inventory_hostname == \\"<device>\\"", "register": "dhcp_relay_check", "changed_when": false, "failed_when": "'<server_ip>' not in dhcp_relay_check.stdout"}}
NOTE: DHCP relay servers live on the VLAN table itself, NOT a separate DHCP_RELAY table — key is "VLAN|Vlan<vlan_id>", field is "dhcp_servers@"
(note the trailing @, SONiC's CONFIG_DB convention for a comma-joined list field). Value is comma-joined when multiple servers exist on one VLAN
(e.g. "10.99.90.53,10.99.90.54") — the substring check above handles both single- and multi-server cases without parsing the list.
""",
        ("vlan_svi", "delete"): """
Template — VLAN SVI IP delete (expected_state is empty by design — a delete
carries no IP value, only the claim "this SVI is gone"):
  1. {{"name": "Remove SVI IP from Vlan<id> on <device>", "raw": "for ip in $(sonic-db-cli CONFIG_DB keys \\"VLAN_INTERFACE|Vlan<id>|*\\" | sed 's/.*|//'); do config interface ip remove Vlan<id> $ip; done", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify VLAN<id> SVI IP removed", "raw": "sonic-db-cli CONFIG_DB keys \\"VLAN_INTERFACE|Vlan<id>|*\\"", "when": "inventory_hostname == \\"<device>\\"", "register": "vlan_svi_check", "changed_when": false, "failed_when": "vlan_svi_check.stdout.strip() != ''"}}
NOTE: avoid awk '{print ...}' here — curly braces in a raw Ansible task get
parsed as Jinja templating before the shell ever sees them, and awk's own
{ } action syntax collides with that. Use sed 's/.*|//' instead, which
needs no braces, to strip "VLAN_INTERFACE|Vlan<id>|" and leave just the
IP/prefix from the CONFIG_DB key name itself. The for-loop handles the case
of multiple IPs on one SVI (rare but possible) without needing codegen to
know the IP value in advance.
""",
    ("bgp", "create"): """
Template — BGP process create (router_id optional — only add -c "bgp router-id <router_id>" if expected_state.router_id is present):
  1. {{"name": "Configure BGP AS <local_as> on <device>", "raw": "sudo vtysh -c \\"configure terminal\\" -c \\"router bgp <local_as>\\" [-c \\"bgp router-id <router_id>\\" if present] -c \\"end\\" -c \\"write memory\\"", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify BGP AS <local_as> applied", "raw": "sudo vtysh -c 'show running-config' | grep 'router bgp <local_as>'", "when": "inventory_hostname == \\"<device>\\"", "register": "bgp_check", "failed_when": "bgp_check.stdout == ''"}}
""",
    ("bgp_neighbor", "create"): """
Template — BGP neighbor create. THE local_as FOR THIS DEVICE IS GIVEN TO YOU
BELOW AS A FACT — never infer or invent it.
IF expected_state.interface is present, this is UNNUMBERED/interface-based
peering: use "neighbor <interface> interface remote-as <remote_as>".
OTHERWISE this is IP-based peering: use "neighbor <object_id> remote-as <remote_as>"
(object_id IS the neighbor IP in that case).
CRITICAL: emit each -c argument as ONE JSON string with real double-quote
characters, never backslash-escaped twice. E.g. the value for "raw" must
literally contain: -c "router bgp 65010" — a single backslash before each
quote inside the JSON string is correct; do not add extra backslashes.
  1. {{"name": "Add BGP neighbor <object_id> on <device>", "raw": "sudo vtysh -c \\"configure terminal\\" -c \\"router bgp <local_as>\\" -c \\"<neighbor clause per above>\\" -c \\"address-family ipv4 unicast\\" -c \\"no bgp ebgp-requires-policy\\" -c \\"end\\" -c \\"write memory\\"", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify BGP neighbor <object_id> applied", "raw": "sudo vtysh -c 'show running-config' | grep '<interface if present, else object_id>'", "when": "inventory_hostname == \\"<device>\\"", "register": "bgp_neighbor_check", "failed_when": "bgp_neighbor_check.stdout == ''"}}
""",
    ("bgp_network", "create"): """
Template — BGP network advertise. THE local_as FOR THIS DEVICE IS GIVEN TO
YOU BELOW AS A FACT — never infer or invent it. object_id IS the prefix.
  1. {{"name": "Advertise <object_id> in BGP on <device>", "raw": "sudo vtysh -c \\"configure terminal\\" -c \\"router bgp <local_as>\\" -c \\"address-family ipv4 unicast\\" -c \\"network <object_id>\\" -c \\"end\\" -c \\"write memory\\"", "when": "inventory_hostname == \\"<device>\\""}}
  2. {{"name": "Verify <object_id> advertised", "raw": "sudo vtysh -c 'show running-config' | grep 'network <object_id>'", "when": "inventory_hostname == \\"<device>\\"", "register": "bgp_network_check", "failed_when": "bgp_network_check.stdout == ''"}}
""",
}

_SYSTEM_PROMPT = """You are the Ansible codegen agent for a SONiC network automation system.

You receive exactly ONE IR object and its matching task template. Your ONLY
job is to substitute the real values from the IR object into the template
and return the resulting task list as JSON. Do NOT add tasks the template
doesn't call for. Do NOT omit tasks the template calls for. Do NOT reinterpret
the IR — every value you need is either in the IR object below or in the
extra facts (like local_as) provided.

{template}

# IR OBJECT (the only source of truth for this call)
{ir_object_json}

{extra_facts}

# OUTPUT
Return ONLY valid JSON matching: {{"tasks": [ {{"name": ..., "raw": ..., "when": ..., "register": ..., "failed_when": ..., "changed_when": ..., "ignore_errors": ...}}, ... ]}}
Omit any key not used by a given task (e.g. omit "register" if there's no verify step reading it). No markdown fences, no explanation.
"""


def _when(device: str) -> str:
    return f'inventory_hostname == "{device}"'


class AnsibleCodeGenAgent(BaseAgent):
    def __init__(self, provider: str = None):
        super().__init__(system_prompt="", provider=provider)

    def map_object(self, obj: IRObject, extra_facts: dict | None = None) -> list[dict]:
        """Maps ONE IR object to its Ansible task list via a narrowly-scoped
        LLM call. Raises CodegenError on no template or invalid output."""
        key = (obj.object_type, obj.verb)
        template = _TEMPLATES.get(key)
        if template is None:
            raise CodegenError(
                f"No template for (object_type={obj.object_type!r}, verb={obj.verb!r}) "
                f"— extend _TEMPLATES before this can be mapped."
            )

        ir_object_json = json.dumps({
            "device": obj.device, "object_type": obj.object_type,
            "object_id": obj.object_id, "verb": obj.verb,
            "expected_state": obj.expected_state,
        }, indent=2)

        extra_block = ""
        if extra_facts:
            extra_block = "# EXTRA FACTS (provided, never infer these yourself)\n" + json.dumps(extra_facts, indent=2)

        self.system_prompt = _SYSTEM_PROMPT.format(
            template=template, ir_object_json=ir_object_json, extra_facts=extra_block,
        )
        raw = self.chat("Map this IR object to its Ansible task(s) now.")

        try:
            data, _ = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError as e:
            raise CodegenError(f"Invalid JSON for {obj.object_type}/{obj.object_id}: {e}\nRaw:\n{raw}")

        try:
            result = AnsibleTaskList.model_validate(data)
        except Exception as e:
            raise CodegenError(f"Schema validation failed for {obj.object_type}/{obj.object_id}: {e}\nRaw:\n{raw}")

        return [t.to_task_dict() for t in result.tasks]

    def generate(self, combined_ir: list[IRObject]) -> str:
        """
        Full pipeline: combined, conflict-free IR -> complete Ansible
        playbook YAML. Maps each object via map_object(), resolves
        cross-object facts (device_local_as) in plain Python, appends
        per-device save tasks, assembles the single-play document.

        Raises CodegenError if any object can't be safely mapped.
        """
        device_local_as = {
            obj.device: obj.expected_state["local_as"]
            for obj in combined_ir
            if obj.object_type == "bgp" and "local_as" in obj.expected_state
        }

        tasks: list[dict] = []
        devices_touched: set[str] = set()

        for obj in combined_ir:
            extra_facts = None
            if obj.object_type in ("bgp_neighbor", "bgp_network"):
                local_as = device_local_as.get(obj.device)
                if not local_as:
                    raise CodegenError(
                        f"{obj.object_type}: no local_as known for {obj.device} — "
                        f"combined IR must include a 'bgp' object for this device first."
                    )
                extra_facts = {"local_as": local_as}
            tasks.extend(self.map_object(obj, extra_facts))
            devices_touched.add(obj.device)

        for device in sorted(devices_touched):
            tasks.append({
                "name": f"Save config on {device}",
                "raw": "sudo config save -y",
                "when": _when(device),
            })

        play = [{
            "name": "Agentic-SONiC generated configuration",
            "hosts": "sonic_switches",
            "gather_facts": False,
            "become": True,
            "become_method": "sudo",
            "tasks": tasks,
        }]
        return yaml.dump(play, default_flow_style=False, sort_keys=False, explicit_start=True)