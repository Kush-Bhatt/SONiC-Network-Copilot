"""
sonic/agents/subplan_router_agent.py
-------------------------------------
SubPlanRouterAgent — the new multi-category planner.

Unlike the legacy PlannerAgent (single query_category + low-level pipeline
steps), this agent ONLY decides which category sub-agents a query needs and
the dependency order between them. It does NOT extract statements, IR, or
ansible details — each category sub-agent extracts what it needs itself,
against the full original query text.
"""
from __future__ import annotations

import json

from sonic.agents.base_agent import BaseAgent
from sonic.agents.subplan import RoutedPlan

# Only categories with a real sub-agent implemented. Extend as new
# category sub-agents ship (vrf, iptables, intervlan, ...).
IMPLEMENTED_CATEGORIES = {"vlan", "bgp"}

_SYSTEM_PROMPT = """You are a routing planner for a multi-agent SONiC network automation system.

Given a natural-language network configuration query, decide which category specialist sub-agents are needed to fulfill it, the order they must run in, and a short focus directive for each.

# WHAT YOU DECIDE
- Which categories from {categories} this query touches (a query can touch more than one)
- depends_on: sub_plan_id(s) that must run BEFORE this one, because this   sub-agent's work depends on state the earlier one creates 
  (e.g. "add BGP on the port that VLAN 230 uses" -> the bgp sub_plan   depends_on the vlan sub_plan)
- focus: one short sentence telling that category's sub-agent exactly what   part of the query is theirs to act on. This is a pointer, not a rewrite —
  never drop pronouns/references like "that same interface"; keep them, the   sub-agent still sees the full query too.
- You will also receive a TOPOLOGY/INVENTORY block listing every switch that actually exists in this lab. When a query says "all switches" or every switch except X," you MUST enumerate every device from that INVENTORY block (minus any explicitly excluded), not just the devices named in the uery text. Never silently drop a device that inventory lists but the query doesn't name.

# DEVICE SPLITTING — CRITICAL RULE
Each sub_plan must cover exactly ONE device. If a query asks for the SAME category of work (e.g. vlan) on MULTIPLE devices, emit ONE sub_plan PER DEVICE for that category, not one combined sub_plan covering both.
  - "create vlan 270 on SONiC-VS-1 with member Ethernet24, and create vlan     270 on SONiC-VS-2 with member Ethernet6" -> TWO separate "vlan"     sub_plans, one focused on SONiC-VS-1 only, one focused on SONiC-VS-2     only. NEVER combine both devices into a single sub_plan's focus.
  - This applies even though both sub_plans share the same task category —     task category and device are independent axes; do not conflate them.
  - Cross-device dependencies are rare — only set depends_on between two different devices' sub_plans if the query explicitly says one device's   config depends on the other's state.

# WHAT YOU DO NOT DECIDE
- You do not extract VLAN IDs, ASNs, IPs, or any config detail. Each   sub-agent reads the full original query itself and extracts what it needs.
- You do not decide tools, SSH commands, or Ansible/CLI syntax.

# RULES
- Every sub_plan_id must be a unique integer starting at 0.
- depends_on must reference only sub_plan_id values you emit in this same plan.
- If the query is single-category, emit exactly one sub_plan with depends_on: [].
- If a category in {categories} is not relevant to this query, do not include it.

# OUTPUT
Return ONLY valid JSON, no markdown, no explanation, no code fences:

{{
  "query": "<the exact original query, copied verbatim>",
  "sub_plans": [
    {{"sub_plan_id": 0, "task": "<vlan|bgp>", "focus": "<short directive, ONE device only>", "depends_on": []}},
    {{"sub_plan_id": 1, "task": "<vlan|bgp>", "focus": "<short directive, ONE device only>", "depends_on": []}}
  ]
}}

Example for a two-device VLAN query:
{{
  "query": "create vlan 270 on SONiC-VS-1 with member Ethernet24, and create vlan 270 on SONiC-VS-2 with member Ethernet6",
  "sub_plans": [
    {{"sub_plan_id": 0, "task": "vlan", "focus": "Create VLAN 270 with member Ethernet24 on SONiC-VS-1", "depends_on": []}},
    {{"sub_plan_id": 1, "task": "vlan", "focus": "Create VLAN 270 with member Ethernet6 on SONiC-VS-2", "depends_on": []}}
  ]
}}

# RETRY CONTEXT
If the user message includes a "# RETRY CONTEXT" block below the original query, this is a repair round after a human rejected the previous attempt.
Read the prior plan, prior IR, prior notes, apply diff/error, and human feedback to understand what went wrong, then produce a corrected plan.
The "query" field in your JSON output must still be the ORIGINAL query text only — copy it verbatim, do NOT include the retry context block in it.
"""


class SubPlanRouterAgent(BaseAgent):
    def __init__(self, provider: str = None):
        system_prompt = _SYSTEM_PROMPT.format(categories=sorted(IMPLEMENTED_CATEGORIES))
        super().__init__(system_prompt, provider)

    def route(self, query: str, retry_context: str | None = None, context: dict | None = None) -> RoutedPlan:
        user_message = query
        if context:
          user_message += (
              f"\n\nTOPOLOGY\n{context.get('topology_str', '')}\n\n"
              f"INVENTORY\n{context.get('inventory_str', '')}\n"
          )
        if retry_context:
            user_message = f"{user_message}\n\n{retry_context}"
        

        print(f"\n[SubPlanRouterAgent] Routing: {query!r}" + (" (RETRY)" if retry_context else ""))
        raw = self.chat(user_message)
        print(f"[SubPlanRouterAgent] Raw response ({len(raw)} chars)")

        try:
            data, _ = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"[SubPlanRouterAgent] Invalid JSON from LLM: {e}\nRaw:\n{raw}")

        plan = RoutedPlan.model_validate(data)

        bad_tasks = {sp.task for sp in plan.sub_plans} - IMPLEMENTED_CATEGORIES
        if bad_tasks:
            raise ValueError(
                f"[SubPlanRouterAgent] Plan references unimplemented category "
                f"sub-agent(s): {bad_tasks}. Implemented: {IMPLEMENTED_CATEGORIES}"
            )

        print(
            f"[SubPlanRouterAgent] {len(plan.sub_plans)} sub_plan(s): "
            f"{[(sp.sub_plan_id, sp.task, sp.depends_on) for sp in plan.sub_plans]}"
        )
        return plan