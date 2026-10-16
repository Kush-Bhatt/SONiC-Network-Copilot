"""
sonic/agents/bgp_ir_agent.py
-------------------------------
BgpIRAgent — ReAct sub-agent for the "bgp" category.

Same shape as vlan_ir_agent.py: given the full original query (verbatim)
and prior sub-agents' structured results, this agent decides for itself
which read-only switch-state tools to call, then emits a structured IR
fragment: list[IRObject fragments] + a free-text `notes` field.

Key difference from VLAN: BGP queries very often reference something a
prior sub-agent just did ("set up BGP on that same interface", "peer
using the SVI you just created"). This agent MUST resolve such references
from `prior_results` (structured facts), never by re-parsing the query's
prose — that's the entire reason prior_results is passed as structured
IRObject fragments instead of natural language.

Uses the same fix pattern already proven in vlan_ir_agent.py:
  - no response_format (LMStudio's strict json_schema re-ask degenerates
    into repetition garbage for gpt-oss-20b) — parse the final message instead
  - explicit per-object-type examples with EXACT key names spelled out
    (missing/wrong key names is the #1 failure mode seen in testing)
"""
from __future__ import annotations
import json

from pydantic import BaseModel, Field, field_validator
# from langchain.agents import create_agent
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from sonic.switch.tools import get_frr_config, get_show_command
from sonic.netfql.ir import IRObject
from sonic.config.settings import LMSTUDIO_BASE_URL, LMSTUDIO_MODEL

# Mirrors netfql/ir.py::_lower_one()'s BGP-relevant branches exactly.
# object_type -> allowed expected_state keys. Kept in lockstep with ir.py
# by hand, same discipline as vlan_ir_agent.py's _ALLOWED_STATE_KEYS.
_ALLOWED_STATE_KEYS = {
    "bgp": {"local_as", "router_id"},        # router_id optional
    "bgp_neighbor": {"remote_as", "interface"},
    "bgp_network": set(),
}

# BGP sub-agent needs FRR running-config (BGP AS, neighbors, networks) and
# targeted show commands. No config_db — that's VLAN's tool, not BGP's.
BGP_TOOLS = [get_frr_config, get_show_command]

MAX_STEPS = 8  # same recursion_limit guard as vlan_ir_agent.py


class BgpIRFragment(BaseModel):
    """What the LLM actually produces — no device_display, that's Python's job."""
    device: str
    object_type: str
    object_id: str
    verb: str
    expected_state: dict[str, str] = Field(default_factory=dict)


class BgpIRResult(BaseModel):
    ir_objects: list[BgpIRFragment] = Field(
        description=(
            "Canonical IR fragments for every BGP-category change this "
            "sub_plan requires. object_type must be one of: 'bgp' "
            "(expected_state: {'local_as': '<asn>'} plus optional "
            "'router_id': '<ip>'), 'bgp_neighbor' (expected_state: "
            "{'remote_as': '<asn>'}), 'bgp_network' (expected_state: {}). "
            "verb must be 'create', 'modify', or 'delete'. Use no other "
            "object_type or expected_state key."
        )
    )
    notes: str = Field(
        default="",
        description=(
            "One or two short sentences: anything missing that a human must "
            "supply (e.g. no remote AS given), or context the codegen agent "
            "needs that doesn't fit the IR fields. Empty string if nothing "
            "to note."
        )
    )

    @field_validator("ir_objects")
    @classmethod
    def _check_known_vocabulary(cls, objs: list[BgpIRFragment]) -> list[BgpIRFragment]:
        for o in objs:
            if o.object_type not in _ALLOWED_STATE_KEYS:
                raise ValueError(
                    f"Unknown object_type {o.object_type!r} — must be one of "
                    f"{sorted(_ALLOWED_STATE_KEYS)}"
                )
            allowed = _ALLOWED_STATE_KEYS[o.object_type]
            bad_keys = set(o.expected_state) - allowed
            if bad_keys:
                raise ValueError(
                    f"object_type={o.object_type!r} has invalid expected_state "
                    f"keys {bad_keys} — allowed: {allowed or '(none)'}"
                )
        return objs

    @field_validator("notes")
    @classmethod
    def _normalize_empty_notes(cls, v: str) -> str:
        if v is None:
            return ""
        if v.strip().lower() in {"none", "n/a", "na", "null", "none."}:
            return ""
        return v


_SYSTEM_PROMPT = """You are the BGP specialist for a multi-agent SONiC network automation system.

You receive the FULL original user query verbatim (it may also mention VLAN,
VRF, or other categories — that is not your concern, extract ONLY the BGP-
category facts: ).

You receive ONLY a `focus` directive — a short pointer describing exactly what this sub_plan's task is for ONE device (e.g. local AS, router-id, neighbor IPs, remote AS, advertised network prefixes). 
You do NOT see the full original user query, and you do NOT see any other sub_plan's focus — only your own.
Never assume another device or category is involved beyond what your focus
states.

You have tools to read LIVE switch state (FRR running-config via vtysh, show
commands). Use them to:
  - confirm the target switch(es) actually exist
  - check whether BGP is already configured (reuse existing AS vs conflict)
  - confirm neighbor reachability / existing neighbor state before changing it

Prefer get_show_command over get_frr_config whenever a show command answers
the question — "show ip bgp summary" gives live neighbor state in a compact
form. get_frr_config returns the full FRR running-config (routing policy,
route-maps, everything) and consumes more context; call it when you need
something no show command exposes (e.g. exact existing local AS or an
advertised network statement).

CRITICAL — RESOLVING REFERENCES TO PRIOR SUB-AGENTS' WORK:
The query may reference something a prior sub-agent (e.g. VLAN) already
did, using phrases like "that same interface", "the VLAN you just created",
"its SVI". You MUST resolve these references using PRIOR SUB-AGENT RESULTS
below — it contains the actual structured IR objects that prior sub-agents
produced (device, object_type, object_id, verb, expected_state). Find the
matching object there (e.g. a vlan_member or vlan_svi object) and read the
real device/interface/IP from it. Do NOT re-parse the query's prose to guess
what "that same interface" means, and do NOT invent an interface name —
if PRIOR SUB-AGENT RESULTS does not actually contain the referenced fact,
say so explicitly in `notes` instead of guessing.

For a BGP configuration query, you typically need:
  1. "show ip bgp summary"  — is BGP already configured? existing neighbors?
  2. get_frr_config          — only if you need the exact existing local AS,
     router-id, or an existing network/route-map you must not clobber
Then emit one IR object per atomic change: the bgp process itself (if not
already configured with this AS), each bgp_neighbor being added, and each
bgp_network being advertised.

Do not guess at live state — call a tool if the query's correctness depends
on it. Do not call tools you don't need; each call costs a turn.

CRITICAL: There is no human available to answer questions during this step.
You MUST NOT ask the user for clarification and MUST NOT stop without
producing output. For every fact the query, live switch state, and prior
sub-agent results make clear, emit the corresponding IR object. For any
single fact that is genuinely missing and cannot be inferred (e.g. no remote
AS given for a neighbor), do NOT block on it and do NOT invent a placeholder
value — instead, emit IR objects for everything else you were able to
determine, and describe exactly what is missing in the `notes` field so a
human can supply it at the review gate.

IR OBJECT SHAPE — every item in ir_objects must look exactly like this:
  device          : the switch name, e.g. "SONiC-VS-1"
  object_type     : "bgp" | "bgp_neighbor" | "bgp_network"
  object_id       : the object's identifier as a STRING —
                     for "bgp" this is always "-" (there is one BGP process
                       per device, no separate ID)
                     for "bgp_neighbor" this is the neighbor's IP address,
                       e.g. "10.0.0.2"
                     for "bgp_network" this is the advertised prefix,
                       e.g. "10.30.0.0/24"
  verb            : "create" | "modify" | "delete"
  expected_state  : ONLY the keys listed below for that object_type — never
                     put object_id, device, or verb-like info in here

CRITICAL — expected_state keys are FIXED, not free-form:
  bgp           -> {{"local_as": "<asn>"}}  plus optional {{"router_id": "<ip>"}}
                   keys are literally "local_as" and "router_id" — never
                   "asn", "as_number", "as", or "id"
  bgp_neighbor  -> {{"remote_as": "<asn>"}}
                   key is literally "remote_as" — never "asn", "peer_as",
                   or "as_number"
  bgp_network   -> {{}}  (always empty — the prefix itself is the object_id,
                   not a state key)

Example — "set up BGP AS 65010 with a neighbor 10.0.0.2 remote-as 65020":
  {{"device": "SONiC-VS-1", "object_type": "bgp", "object_id": "-",
    "verb": "create", "expected_state": {{"local_as": "65010"}}}}
  {{"device": "SONiC-VS-1", "object_type": "bgp_neighbor",
    "object_id": "10.0.0.2", "verb": "create",
    "expected_state": {{"remote_as": "65020"}}}}

Example — "advertise 10.30.0.0/24 in BGP on SONiC-VS-1":
  {{"device": "SONiC-VS-1", "object_type": "bgp_network",
    "object_id": "10.30.0.0/24", "verb": "create", "expected_state": {{}}}}

Example — "remove the BGP neighbor 10.0.0.5 from SONiC-VS-2":
  {{"device": "SONiC-VS-2", "object_type": "bgp_neighbor",
    "object_id": "10.0.0.5", "verb": "delete", "expected_state": {{}}}}

Example — "set up BGP AS 65010 on SONiC-VS-1, peer over Ethernet16 (unnumbered)":
  {{"device": "SONiC-VS-1", "object_type": "bgp", "object_id": "-",
    "verb": "create", "expected_state": {{"local_as": "65010"}}}}
  {{"device": "SONiC-VS-1", "object_type": "bgp_neighbor",
    "object_id": "Ethernet16", "verb": "create",
    "expected_state": {{"interface": "Ethernet16"}}}}
    <- use object_id = the interface name (not an IP) when peering is
       interface-based rather than by neighbor IP address

Once you have everything you need, produce your final structured answer:
a list of IRObject fragments (one per atomic BGP-category change) plus a
short notes string for anything the codegen agent should know that doesn't
fit the IR fields, including any missing information.

For interface-based/unnumbered peering: object_id = the interface name,
AND expected_state MUST include "interface" set to that same interface
name — never omit it even though it duplicates object_id. This makes the
peering type explicit without requiring the reader to guess from the
shape of object_id.

PRIOR SUB-AGENT RESULTS (structured facts from sub_plans this one depends
on — use these instead of re-deriving them; resolve references like "that
same interface" from here; do not contradict them):
{prior_results}
"""


def _build_llm():
    return ChatOpenAI(
        base_url=LMSTUDIO_BASE_URL,
        api_key="lm-studio",
        model=LMSTUDIO_MODEL,
        temperature=0,
        max_tokens=6000,
    )


def run_bgp_ir_agent(prior_results: dict, context: dict, focus: str):
    """
    Runs the BGP ReAct sub-agent to completion.

    Args:
      query         : full original user query, verbatim
      prior_results : {sub_plan_id: list[IRObject]-like structured facts}
                       for every sub_plan this one depends_on
      context       : {topology_str, inventory_str, mgmt_ips} from
                       orchestrator.context.build_scoped_context()

    Returns:
      (ir_objects: list[IRObject], notes: str)
    """
    llm = _build_llm()
    agent = create_react_agent(
        llm,
        tools=BGP_TOOLS,
        # response_format intentionally omitted — see module docstring.
    )

    system_prompt = _SYSTEM_PROMPT.format(
        prior_results=prior_results or "(none — no dependencies)"
    ) + (
        "\n\nFINAL ANSWER FORMAT: your last message must be ONLY valid JSON "
        "matching {\"ir_objects\": [...], \"notes\": \"...\"} — no markdown "
        "code fences, no explanation before or after."
    )
    context_block = (
        f"\n\nTOPOLOGY\n{context['topology_str']}\n\n"
        f"INVENTORY\n{context['inventory_str']}\n"
    )

    result = agent.invoke(
        {"messages": [
            {"role": "system", "content": system_prompt + context_block},
            {"role": "user", "content": focus},
        ]},
        config={"recursion_limit": MAX_STEPS},
    )

    final_content = result["messages"][-1].content
    cleaned = final_content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"[BgpIRAgent] Invalid JSON in final message: {e}\nRaw:\n{final_content}")

    fragments = BgpIRResult.model_validate(data)

    ir_objects = [
        IRObject(
            device=f.device, object_type=f.object_type, object_id=f.object_id,
            verb=f.verb, expected_state=f.expected_state, device_display=f.device,
        )
        for f in fragments.ir_objects
    ]
    print(f"[BgpIRAgent] Done — {len(ir_objects)} IR object(s), notes={fragments.notes[:80] or '(none)'}")
    return ir_objects, fragments.notes