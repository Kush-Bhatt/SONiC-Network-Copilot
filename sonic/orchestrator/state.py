"""
sonic/orchestrator/state.py
---------------------------
LangGraph state schema for the planner-driven pipeline.

The state carries the original request, orchestrator plumbing, HITL
session metadata, and one field per output_key populated by the plan
steps.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class PipelineState(TypedDict, total=False):
    # Original inputs, set once before the graph runs.
    query: str
    mgmt_ips: dict
    inventory_str: Optional[str]
    project_id: Optional[str]
    session_id: str
    examples: dict  # {"ansible": <file text>, "type": <category>}
    hitl_session: dict  # create_baseline() result
    round_number: int
    ansible_playbook: str
    ansible_repair_context: str
    # Frozen, operator-approved IntentSpec (agents/intent_spec). None when the
    # request is out of verification scope (not vlan/bgp) — the pipeline still
    # runs but skips the deterministic checks.
    intent_spec: Optional[object]

    # Orchestrator-owned plumbing fields.
    topology_str: str
    full_context: str

    # One field per plan output_key plus HITL artifacts.
    parsed_context: dict
    inventory: dict
    ansible_result: dict
    snapshot_t2: dict
    snapshot_t3: dict
    diff_bundle: dict
    diff_human: str
    diff_machine: dict
    diff_raw: dict
    rollback_result: dict
    review_history: list
    human_review: dict