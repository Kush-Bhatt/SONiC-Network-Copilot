"""
sonic/agents/vlan_ir_agent.py
-------------------------------
VlanIRAgent — ReAct sub-agent for the "vlan" category.

Given the full original query (verbatim) and prior sub-agents' structured
results, this agent decides for itself which read-only switch-state tools
to call (via a LangGraph create_react_agent loop), then emits a structured
IR fragment: list[IRObject] + a free-text `notes` field for anything the
codegen agent needs that doesn't fit IRObject's typed fields.

Unlike BaseAgent-derived agents (single-shot chat()), this is a genuine
multi-turn tool-calling agent — hence a separate ChatOpenAI client here
(same pattern switch_data_agent.py already uses), not BaseAgent.

Read-only tools only. Never binds get_config_db/get_frr_config/
get_show_command's write-capable siblings (none exist yet, but if any
write-capable tool is ever added to switch/tools.py, it must NOT be bound
here — writes only happen in the codegen+apply step, gated by HITL).
"""
from __future__ import annotations
import  json

from pydantic import BaseModel, Field
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from openai import LengthFinishReasonError

from sonic.switch.tools import get_config_db, get_show_command
from sonic.netfql.ir import IRObject
from sonic.config.settings import LMSTUDIO_BASE_URL, LMSTUDIO_MODEL



from pydantic import field_validator

# Mirrors netfql/ir.py::_lower_one()'s VLAN-relevant branches exactly.
# object_type -> allowed expected_state keys. Keep this in lockstep with
# ir.py by hand; it is intentionally NOT imported from there, since ir.py
# has no notion of "what an LLM is allowed to say" — that's a prompt/agent
# concern, not core IR concern.
_ALLOWED_STATE_KEYS = {
    "vlan": set(),
    "vlan_svi": {"ip"},
    "vlan_member": {"tagging"},
    "vlan_dhcp_relay": set(),   # presence-only, like vlan — server lives in object_id
}

# VLAN sub-agent only needs config_db (VLAN/VLAN_MEMBER/VLAN_INTERFACE) and
# targeted show commands. No FRR — that's BGP's tool, not VLAN's.
VLAN_TOOLS = [get_config_db, get_show_command]

MAX_STEPS = 8  # recursion_limit guard — VLAN fact-gathering is a handful of
               # show/config_db calls, not open-ended investigation. See
               # sre-agent's ToolCallLimitMiddleware precedent.

class VlanIRFragment(BaseModel):
    """What the LLM actually produces — no device_display, that's Python's job."""
    device: str
    object_type: str
    object_id: str
    verb: str
    expected_state: dict[str, str] = Field(default_factory=dict)


class VlanIRResult(BaseModel):
    ir_objects: list[VlanIRFragment] = Field(
        description=(
            "Canonical IR fragments for every VLAN-category change this "
            "sub_plan requires. object_type must be one of: 'vlan' "
            "(expected_state: {}), 'vlan_svi' (expected_state: {'ip': "
            "'<ip/prefix>'} or {} if IP is unknown), 'vlan_member' "
            "'vlan_member' (expected_state: {'tagging': 'tagged'|'untagged'}), "
            "'vlan_dhcp_relay' (expected_state: {}, object_id is "
            "'<vlan_id>/<server_ip>' combined — one object PER relay server "
            "if multiple are given). "
        )
    )
    notes: str = Field(
        default="",
        description=(
            "One or two short sentences: anything missing that a human must "
            "supply (e.g. no SVI IP given), or context the codegen agent needs "
            "that doesn't fit the IR fields. Empty string if nothing to note."
        )
    )

    @field_validator("ir_objects")
    @classmethod
    def _check_known_vocabulary(cls, objs: list[VlanIRFragment]) -> list[VlanIRFragment]:
        for o in objs:
            if o.object_type not in _ALLOWED_STATE_KEYS:
                raise ValueError(f"Unknown object_type {o.object_type!r} — must be one of {sorted(_ALLOWED_STATE_KEYS)}")
            allowed = _ALLOWED_STATE_KEYS[o.object_type]
            bad_keys = set(o.expected_state) - allowed
            if bad_keys:
                raise ValueError(f"object_type={o.object_type!r} has invalid expected_state keys {bad_keys} — allowed: {allowed or '(none)'}")
        return objs

    @field_validator("notes")
    @classmethod
    def _normalize_empty_notes(cls, v: str) -> str:
        if v is None:
            return ""
        if v.strip().lower() in {"none", "n/a", "na", "null", "none."}:
            return ""
        return v


_SYSTEM_PROMPT = """You are the VLAN specialist for a multi-agent SONiC network automation system.

You receive ONLY a `focus` directive — a short pointer describing exactly what this sub_plan's task is for ONE device (e.g. VLAN IDs, SVI IPs, port/interface membership, tagging mode).
You do NOT see the full original user query, and you do NOT see any other sub_plan's focus — only your own.
Never assume another device or category is involved beyond what your focus
states.

You have tools to read LIVE switch state (config_db.json contents, show vlan
brief, show interfaces status). Use them to:
  - confirm the target switch(es) actually exist and which ports are free
  - check whether a referenced VLAN already exists (reuse vs create)
  - resolve ambiguous interface references against real port state

Prefer get_show_command over get_config_db whenever a show command answers
the question — "show vlan brief" and "show interfaces status" return only
the relevant fields. get_config_db returns the ENTIRE config_db.json
(tens of KB) and consumes a large amount of context; only call it if you
need a field no show command exposes (e.g. exact SVI IP in VLAN_INTERFACE).

Before adding a port as a VLAN member, check its Oper status from show interfaces status:
  - Oper 'down' with no existing VLAN/SVI config is the NORMAL state for a free
    lab port. Do NOT add caution language about physical connectivity for this
    case — treat it as simply free and proceed.
  - Oper 'up' with no existing VLAN/SVI config means something may be physically
    connected to it outside this system's knowledge (e.g. an inter-switch link
    not captured in the topology). Do NOT silently proceed in this case — state
    this explicitly in notes as a caution the human must confirm before applying.
Only the Oper 'up' + no-config combination warrants a caution note; Oper 'down'
never does.

For a VLAN creation query, you typically need ALL of the following before
you can finish — do not stop after one tool call:
  1. "show vlan brief"        — does the VLAN already exist?
  2. "show interfaces status" — is the target interface free?
Then emit one IR object per atomic change: the vlan itself, each vlan_member
being added, and the vlan_svi if an SVI is mentioned in the query.

Do not guess at live state — call a tool if the query's correctness depends
on it. Do not call tools you don't need; each call costs a turn.

CRITICAL: There is no human available to answer questions during this step.
You MUST NOT ask the user for clarification and MUST NOT stop without
producing output. For every fact the query and live switch state make clear,
emit the corresponding IR object. For any single fact that is genuinely
missing and cannot be inferred (e.g. no SVI IP address was given anywhere in
the query), do NOT block on it and do NOT invent a placeholder value —
instead, emit IR objects for everything else you were able to determine,
and describe exactly what is missing in the `notes` field so a human can
supply it at the review gate.

IR OBJECT SHAPE — every item in ir_objects must look exactly like this:
  device          : the switch name, e.g. "SONiC-VS-1"
  object_type     : "vlan" | "vlan_svi" | "vlan_member"
  object_id       : the object's identifier as a STRING — for "vlan" this is
                     the VLAN ID itself, e.g. "230" (NOT put in expected_state)
  verb            : "create" | "modify" | "delete"
  expected_state  : ONLY the keys listed above for that object_type — never
                     put object_id, device, or verb-like info in here

Example — "create vlan 230 on SONiC-VS-1, untagged member Ethernet16":
  {{"device": "SONiC-VS-1", "object_type": "vlan", "object_id": "230",
    "verb": "create", "expected_state": {{}}}}
  {{"device": "SONiC-VS-1", "object_type": "vlan_member",
    "object_id": "230/Ethernet16", "verb": "create",
    "expected_state": {{"tagging": "untagged"}}}}


Example — "create vlan 230 on SONiC-VS-1, untagged member Ethernet16":
  {{"device": "SONiC-VS-1", "object_type": "vlan", "object_id": "230",
    "verb": "create", "expected_state": {{}}}}
  {{"device": "SONiC-VS-1", "object_type": "vlan_member",
    "object_id": "230/Ethernet16", "verb": "create",
    "expected_state": {{"tagging": "untagged"}}}}

Example — "create vlan 300 on SONiC-VS-1 with an SVI IP of 10.30.0.1/24":
  {{"device": "SONiC-VS-1", "object_type": "vlan", "object_id": "300",
    "verb": "create", "expected_state": {{}}}}
  {{"device": "SONiC-VS-1", "object_type": "vlan_svi", "object_id": "300",
    "verb": "create", "expected_state": {{"ip": "10.30.0.1/24"}}}}
    <- NOTE: the key is "ip", NEVER "ip_address", "svi_ip", or "address"

Example — "remove vlan 210 from SONiC-VS-1":
  {{"device": "SONiC-VS-1", "object_type": "vlan", "object_id": "210",
    "verb": "delete", "expected_state": {{}}}}

Example — "create vlan 390 on SONiC-VS-1 with SVI 10.99.90.1/24 and DHCP relay server 10.99.90.53":
  {{"device": "SONiC-VS-1", "object_type": "vlan", "object_id": "390",
    "verb": "create", "expected_state": {{}}}}
  {{"device": "SONiC-VS-1", "object_type": "vlan_svi", "object_id": "390",
    "verb": "create", "expected_state": {{"ip": "10.99.90.1/24"}}}}
  {{"device": "SONiC-VS-1", "object_type": "vlan_dhcp_relay",
    "object_id": "390/10.99.90.53", "verb": "create", "expected_state": {{}}}}
    <- NOTE: object_id is "<vlan_id>/<server_ip>" combined, like vlan_member's
       "<vlan_id>/<interface>" pattern. The server IP goes in object_id, NOT
       in expected_state — expected_state stays empty, since existence of
       this relay target IS the whole assertion (same as a bare vlan create).
       If multiple relay servers are given, emit one vlan_dhcp_relay object
       per server, each with its own "<vlan_id>/<server_ip>" object_id.

Once you have everything you need, produce your final structured answer:
a list of IRObject fragments (one per atomic VLAN-category change) plus a
short notes string for anything the codegen agent should know that doesn't
fit the IR fields, including any missing information.

PRIOR SUB-AGENT RESULTS (structured facts from sub_plans this one depends
on — use these instead of re-deriving them; do not contradict them):
{prior_results}
"""


def _build_llm():
    return ChatOpenAI(
        base_url=LMSTUDIO_BASE_URL,
        api_key="lm-studio",
        model=LMSTUDIO_MODEL,
        temperature=0,
        max_tokens=6000 ,
        # extra_body={"reasoning_effort": "low"},
    )


def run_vlan_ir_agent(prior_results: dict, context: dict, focus: str):
    llm = _build_llm()
    agent = create_react_agent(
        llm,
        tools=VLAN_TOOLS,
        # response_format removed — LMStudio's strict json_schema re-ask
        # degenerates into repetition garbage for gpt-oss-20b. The model
        # already answers correctly in the ReAct loop's final message when
        # just asked in the system prompt to emit JSON; parse that instead.
    )

    system_prompt = _SYSTEM_PROMPT.format(prior_results=prior_results or "(none — no dependencies)") + (
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
    # Strip markdown fences if the model added them despite instructions
    cleaned = final_content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"[VlanIRAgent] Invalid JSON in final message: {e}\nRaw:\n{final_content}")

    fragments = VlanIRResult.model_validate(data)  # reuses your existing validator

    ir_objects = [
        IRObject(
            device=f.device, object_type=f.object_type, object_id=f.object_id,
            verb=f.verb, expected_state=f.expected_state, device_display=f.device,
        )
        for f in fragments.ir_objects
    ]
    print(f"[VlanIRAgent] Done — {len(ir_objects)} IR object(s), notes={fragments.notes[:80] or '(none)'}")
    return ir_objects, fragments.notes