"""
sonic/agents/planner_agent.py
-----------------------------
PlannerAgent - runs first in the pipeline.

Given query + topology context + example metadata, produces a validated
JSON execution plan for the orchestrator.
"""

from __future__ import annotations

import json

from sonic.agents.base_agent import BaseAgent


# Validation constants

VALID_INTENTS = {
    "gather_state",
    "build_inventory",
    "ansible_apply",
}

VALID_CATEGORIES = {
    "vlan",
    "bgp",
    "intervlan",
    "iptables",
    "vrf",
    "other",
}

INTENT_TO_OUTPUT_KEY = {
    "gather_state": "parsed_context",
    "build_inventory": "inventory",
    "ansible_apply": "ansible_result",
}


# System prompt

_SYSTEM_PROMPT = """You are a network automation planner for SONiC switches.

Given a natural-language configuration query, topology context, and available
examples, produce a detailed JSON execution plan for the orchestrator.

# WHAT YOU DECIDE
- query_category : MUST be exactly one of these strings (no others, no variations): vlan, bgp, intervlan, iptables, vrf, other
- steps : ordered list of PlanStep objects

# WHAT YOU DO NOT DECIDE
- Which SSH commands to run - SwitchDataAgent handles that
- How Ansible runs internally - orchestrator handles that
- Full example code content - orchestrator loads that separately

# STEP INTENTS
- gather_state      : collect live switch state (targets = switches to query)
- build_inventory   : build hosts.ini from mgmt_ips (targets = all switches)
- ansible_apply     : generate and apply Ansible playbook (targets = switches the change touches)

# HITL LOOP
- The orchestrator handles post-Ansible capture, diffing, rollback, and human review.
- Do not emit Batfish steps. Human review uses the Batfish-based differential
  helpers in sonic.hitl.facts after Ansible has run.

# DEPENDENCY RULES
- gather_state      : always step 1, depends_on []
- build_inventory   : depends_on [gather_state]
- ansible_apply     : depends_on [build_inventory]

# TARGETING RULES
- gather_state targets  : all switches the change will touch (when in doubt, all)
- ansible_apply targets : only the switches the query explicitly touches
- all other steps       : all switches

# OUTPUT
Return ONLY valid JSON - no markdown, no explanation, no code fences.

{{
  "query_category": "<vlan|bgp|intervlan|iptables|vrf|other>",
  "steps": [
    {{
      "step_id": <int>,
      "intent": "<intent>",
      "targets": ["<switch_name>", ...],
      "data_output": "<what this step produces>",
      "depends_on": [<step_id>, ...],
      "reason": "<one line: why this step exists>"
    }}
  ]
}}

# TOPOLOGY
{topology}

# SWITCHES
{switches}

# EXAMPLES
{examples}
"""


class PlannerAgent(BaseAgent):
    def __init__(
        self,
        topology: str,
        mgmt_ips: dict,
        selected_example: dict,
        provider: str = None,
    ):
        switches_block = "\n".join(f"  {name}: {ip}" for name, ip in mgmt_ips.items())

        example_lines = []
        ansible_ex = selected_example.get("ansible")
        if ansible_ex:
            example_lines.append(
                f"  ANSIBLE EXAMPLE - {ansible_ex.get('title')}\n"
                f"    {ansible_ex.get('description')}"
            )
        examples_block = (
            "\n\n".join(example_lines) if example_lines else "  (no matching example found)"
        )

        system_prompt = _SYSTEM_PROMPT.format(
            topology=topology,
            switches=switches_block,
            examples=examples_block,
        )

        self._mgmt_ips = mgmt_ips
        super().__init__(system_prompt, provider)

    def plan(self, query: str) -> dict:
        """
        Run the planner for the given query.
        Returns a validated plan dict.
        Raises ValueError if LLM output is invalid JSON or fails validation.
        """
        print(f"\n[PlannerAgent] Planning: {query!r}")

        raw = self.chat(query)

        print(f"[PlannerAgent] Raw response ({len(raw)} chars)")

        plan_dict = self._parse(raw)
        self._validate(plan_dict)

        print(
            f"[PlannerAgent] category={plan_dict['query_category']!r} "
            f"| steps={len(plan_dict['steps'])}"
        )
        return plan_dict

    def _parse(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[PlannerAgent] Invalid JSON from LLM: {e}\nRaw:\n{raw}"
            )

    def _validate(self, plan: dict) -> None:
        for key in ("query_category", "steps"):
            if key not in plan:
                raise ValueError(f"[PlannerAgent] Missing key: {key!r}")

        if plan["query_category"] not in VALID_CATEGORIES:
            raise ValueError(
                f"[PlannerAgent] Invalid category: {plan['query_category']!r}"
            )

        steps = plan["steps"]
        if not steps:
            raise ValueError("[PlannerAgent] Plan has no steps")

        step_ids = {s["step_id"] for s in steps}
        if len(step_ids) != len(steps):
            raise ValueError("[PlannerAgent] Plan contains duplicate step_id values")
        known_targets = set(self._mgmt_ips.keys())
        required_keys = {"step_id", "intent", "targets", "data_output", "depends_on", "reason"}

        for step in steps:
            missing = required_keys - step.keys()
            if missing:
                raise ValueError(
                    f"[PlannerAgent] Step {step.get('step_id')} missing keys: {missing}"
                )

            if step["intent"] not in VALID_INTENTS:
                raise ValueError(
                    f"[PlannerAgent] Step {step['step_id']}: unknown intent {step['intent']!r}"
                )

            step["output_key"] = INTENT_TO_OUTPUT_KEY[step["intent"]]

            bad_targets = set(step["targets"]) - known_targets
            if bad_targets:
                print(
                    f"[PlannerAgent] WARNING Step {step['step_id']}: dropping unknown "
                    f"targets {bad_targets} (not in this topology's mgmt_ips — likely "
                    f"leaked from a retrieved example belonging to a different topology)"
                )
                step["targets"] = [t for t in step["targets"] if t in known_targets]
                if not step["targets"]:
                    raise ValueError(
                        f"[PlannerAgent] Step {step['step_id']}: all targets were "
                        f"unknown, nothing left to target"
                    )

            bad_deps = set(step["depends_on"]) - step_ids
            if bad_deps:
                raise ValueError(
                    f"[PlannerAgent] Step {step['step_id']}: depends_on unknown step_ids {bad_deps}"
                )

        print(f"[PlannerAgent] Validated - {len(steps)} steps OK")