"""
sonic/agents/intent_spec.py
---------------------------
The IntentSpec foundation (design doc §3).

A structured, validated object describing what the operator wants, with the
parameters pulled into fields. The LLM produces this object ONCE, at the
start; after that everything downstream is plain Python. This is the
boundary: the model decides what the user meant, deterministic code does
everything else.

Pydantic validation guarantees one narrow thing — no malformed or
out-of-range intent reaches the generator — which kills a real class of
LLM error (hallucinated switches, out-of-range VLANs, reserved ASNs)
before it costs a switch round-trip. It says nothing about whether the
config is correct; that is what Ideas 1–3 are for.

A Plan is a flat list of atomic Statements — each targeting exactly ONE node
and ONE object (a VLAN, an SVI, a BGP neighbor, ...). Atomicity is what makes
an asymmetric request representable: "VLAN 220 on three switches, SVI only on
one" is three vlan statements plus one svi statement, not a single object
whose svi_ip field would otherwise have to apply to every switch in scope or
none of them.

Scope: VLAN, BGP, and interface (PORT) attributes only. Any other category is
returned as `None` (unverified) so the pipeline still runs but skips the
deterministic checks.

Switch-existence is validated against `known_switches` supplied through
pydantic's validation context, so callers pass `set(mgmt_ips.keys())` and
the models stay unit-testable with any switch set.
"""

from __future__ import annotations

import json
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from sonic.agents.base_agent import BaseAgent

INTERFACE_RE = r"^(Ethernet\d+|PortChannel\d+)$"

# AS_TRANS (23456) and the reserved 16-/32-bit sentinels.
_RESERVED_ASNS = {23456, 65535, 4294967295}


def _known_switches(info: ValidationInfo) -> set[str] | None:
    ctx = info.context or {}
    known = ctx.get("known_switches")
    return set(known) if known is not None else None


class _Statement(BaseModel):
    """Common shape: one verb, one object, one target node.

    Atomicity is the point — a statement that could target more than one node
    is a statement that cannot express a per-node asymmetry, which is what
    silently dropped an SVI before this model existed.
    """

    verb: Literal["create", "modify", "delete"]
    target_node: str

    @field_validator("target_node")
    @classmethod
    def node_must_exist(cls, v, info: ValidationInfo):
        known = _known_switches(info)
        if known is None:
            return v
        if v not in known:
            raise ValueError(f"unknown switch: {v!r}")
        return v


class _VlanScoped(_Statement):
    vlan_id: int

    @field_validator("vlan_id")
    @classmethod
    def vlan_id_in_range(cls, v):
        if not (2 <= v <= 4094):
            raise ValueError(f"vlan_id out of range [2,4094]: {v}")
        return v


class VlanStatement(_VlanScoped):
    object_type: Literal["vlan"] = "vlan"


class SviStatement(_VlanScoped):
    object_type: Literal["svi"] = "svi"
    ip: IPv4Interface | None = None

    @model_validator(mode="after")
    def ip_required_unless_deleting(self):
        if self.verb != "delete" and self.ip is None:
            raise ValueError("svi statement requires 'ip' unless verb is 'delete'")
        return self


class VlanMemberStatement(_VlanScoped):
    object_type: Literal["vlan_member"] = "vlan_member"
    interface: str = Field(pattern=INTERFACE_RE)
    tagging: Literal["tagged", "untagged"] | None = None

    @model_validator(mode="after")
    def tagging_required_unless_deleting(self):
        if self.verb != "delete" and self.tagging is None:
            raise ValueError("vlan_member statement requires 'tagging' unless verb is 'delete'")
        return self


class DhcpRelayStatement(_VlanScoped):
    object_type: Literal["vlan_dhcp_relay"] = "vlan_dhcp_relay"
    server: IPv4Address


class BgpStatement(_Statement):
    object_type: Literal["bgp"] = "bgp"
    local_as: int = Field(ge=1, le=4294967295)
    router_id: IPv4Address | None = None

    @field_validator("local_as")
    @classmethod
    def not_reserved(cls, v):
        if v in _RESERVED_ASNS:
            raise ValueError(f"reserved ASN: {v}")
        return v


class BgpNeighborStatement(_Statement):
    object_type: Literal["bgp_neighbor"] = "bgp_neighbor"
    peer_ip: IPv4Address
    remote_as: int = Field(ge=1, le=4294967295)

    @field_validator("remote_as")
    @classmethod
    def not_reserved(cls, v):
        if v in _RESERVED_ASNS:
            raise ValueError(f"reserved ASN: {v}")
        return v


class BgpNetworkStatement(_Statement):
    object_type: Literal["bgp_network"] = "bgp_network"
    prefix: IPv4Network

    @field_validator("prefix", mode="before")
    @classmethod
    def must_be_network_address(cls, v):
        # Reject host-bits-set prefixes (10.0.0.5/24 where a network is meant).
        return str(IPv4Network(v, strict=True))


class InterfaceStatement(_Statement):
    object_type: Literal["interface"] = "interface"
    verb: Literal["modify"] = "modify"
    interface: str = Field(pattern=INTERFACE_RE)
    admin_state: Literal["up", "down"] | None = None
    mtu: int | None = Field(default=None, ge=1500, le=9216)


Statement = Annotated[
    Union[
        VlanStatement, SviStatement, VlanMemberStatement, DhcpRelayStatement,
        BgpStatement, BgpNeighborStatement, BgpNetworkStatement, InterfaceStatement,
    ],
    Field(discriminator="object_type"),
]

# Which statement types are legal under which plan category. Mixing them would
# route the plan to the wrong fixed-check list in batfish/checks.py.
_CATEGORY_TYPES = {
    "vlan": {"vlan", "svi", "vlan_member", "vlan_dhcp_relay"},
    "bgp": {"bgp", "bgp_neighbor", "bgp_network"},
    "interface": {"interface"},
}


class Plan(BaseModel):
    """An approved change as a flat list of atomic statements."""

    category: Literal["vlan", "bgp", "interface"]
    statements: list[Statement] = Field(min_length=1)

    @model_validator(mode="after")
    def statements_match_category(self):
        allowed = _CATEGORY_TYPES[self.category]
        wrong = sorted({s.object_type for s in self.statements} - allowed)
        if wrong:
            raise ValueError(
                f"statement type(s) {wrong} are not valid under category {self.category!r}"
            )
        return self

    @property
    def switches(self) -> list[str]:
        """Distinct target nodes, first-seen order (stable for rendering)."""
        return list(dict.fromkeys(s.target_node for s in self.statements))

    @property
    def vlan_ids(self) -> list[int]:
        return sorted({s.vlan_id for s in self.statements if hasattr(s, "vlan_id")})

    @property
    def action(self) -> str:
        """One word for the whole plan, for display and for the guard's
        delete-allowlist. `delete` only when EVERY statement deletes — a mixed
        plan must not inherit a delete's permissions."""
        verbs = {s.verb for s in self.statements}
        if verbs == {"delete"}:
            return "delete"
        if "create" in verbs:
            return "create"
        return "modify"


def parse_plan(data: dict, known_switches=None) -> Plan:
    """Validate a raw dict into a Plan.

    Raises ValueError on any malformed / out-of-range / unknown-switch input,
    or on a category outside vlan/bgp/interface. Callers that want the pipeline
    to continue unverified should route through IntentSpecAgent.extract(),
    which returns None instead.
    """
    category = data.get("category")
    if category not in _CATEGORY_TYPES:
        raise ValueError(
            f"unsupported intent category {category!r} "
            f"(only 'vlan', 'bgp' and 'interface' are verified)"
        )
    ctx = {"known_switches": known_switches} if known_switches is not None else None
    return Plan.model_validate(data, context=ctx)


_SYSTEM_PROMPT = """You extract a STRUCTURED PLAN from a natural-language SONiC
network configuration request. You do not plan steps and you do not write config.

Return ONLY valid JSON — no markdown, no explanation, no code fences.

# CATEGORIES (choose exactly one)
- "vlan"      : creating/modifying/deleting a VLAN — members, SVI IP, DHCP relay
- "bgp"       : configuring/removing BGP (local AS, router-id, neighbors, advertised networks)
- "interface" : changing a port's admin state (up/down) or MTU
- "other"     : anything else (VRF, inter-VLAN, iptables, ...) — emit {{"category": "other"}}

# OUTPUT SHAPE
{{
  "category": "vlan" | "bgp" | "interface",
  "statements": [ <statement>, ... ]
}}

Every statement is ATOMIC: exactly one verb, one object, one target node.
If a change touches three switches, emit three statements. Never use a plural
target field — there isn't one.

# STATEMENT TYPES (all take "verb": "create"|"modify"|"delete" and
# "target_node": "<one switch name>")

{{"object_type": "vlan",             "vlan_id": <int 2..4094>}}
{{"object_type": "svi",              "vlan_id": <int>, "ip": "192.168.77.1/24"}}
{{"object_type": "vlan_member",      "vlan_id": <int>, "interface": "Ethernet16",
                                     "tagging": "tagged"|"untagged"}}
{{"object_type": "vlan_dhcp_relay",  "vlan_id": <int>, "server": "10.0.0.53"}}
{{"object_type": "bgp",              "local_as": <int>, "router_id": "10.255.0.1"|null}}
{{"object_type": "bgp_neighbor",     "peer_ip": "10.0.0.2", "remote_as": <int>}}
{{"object_type": "bgp_network",      "prefix": "10.0.0.0/24"}}
{{"object_type": "interface",        "verb": "modify", "interface": "Ethernet16",
                                     "admin_state": "up"|"down"|null, "mtu": <1500..9216>|null}}

# VERBS
- "create" : the object does not exist yet
- "modify" : the object exists; you are changing an attribute of it
- "delete" : remove the object
An SVI is a SEPARATE object from its VLAN. Creating a VLAN does not create an
SVI, and adding an SVI to an existing VLAN is one "svi" statement — it does not
require a "vlan" statement.

# RULES
- All statements in one response must share one category (see OUTPUT SHAPE).
- Interface names in long form: Ethernet16, PortChannel1.
- Use ONLY switch names from the list below. Never invent one.
- bgp_network prefixes must be network addresses (10.0.0.0/24, not 10.0.0.5/24).
- If the request is not clearly VLAN, BGP, or interface, emit {{"category": "other"}}.

# WORKED EXAMPLE
Request: "create vlan 220 on all switches, add SVI with IP 10.99.20.1/24 only on SONiC-VS-1"
{{
  "category": "vlan",
  "statements": [
    {{"object_type": "vlan", "verb": "create", "target_node": "SONiC-VS-1", "vlan_id": 220}},
    {{"object_type": "vlan", "verb": "create", "target_node": "SONiC-VS-2", "vlan_id": 220}},
    {{"object_type": "vlan", "verb": "create", "target_node": "SONiC-VS-3", "vlan_id": 220}},
    {{"object_type": "svi", "verb": "create", "target_node": "SONiC-VS-1",
      "vlan_id": 220, "ip": "10.99.20.1/24"}}
  ]
}}

# SWITCHES (the only valid names)
{switches}
"""


class IntentSpecAgent(BaseAgent):
    """query -> validated Plan (or None if out of scope)."""

    def __init__(self, switches, provider: str = None):
        self._known = set(switches)
        switches_block = "\n".join(f"  {s}" for s in sorted(self._known))
        super().__init__(_SYSTEM_PROMPT.format(switches=switches_block), provider)

    def extract(self, query: str) -> Plan | None:
        print(f"\n[IntentSpecAgent] Extracting intent: {query!r}")
        raw = self.chat(query)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"[IntentSpecAgent] Invalid JSON from LLM: {e}\nRaw:\n{raw}")

        if data.get("category") not in _CATEGORY_TYPES:
            print(
                f"[IntentSpecAgent] category={data.get('category')!r} is out of "
                f"verification scope — running UNVERIFIED (no deterministic checks)"
            )
            return None

        plan = parse_plan(data, known_switches=self._known)
        print(
            f"[IntentSpecAgent] category={plan.category!r} action={plan.action!r} "
            f"{len(plan.statements)} statement(s) across {len(plan.switches)} switch(es)"
        )
        return plan
