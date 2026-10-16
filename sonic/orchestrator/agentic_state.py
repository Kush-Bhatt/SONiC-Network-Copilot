"""
sonic/orchestrator/agentic_state.py
------------------------------------
LangGraph state schema for the multi-agent (router -> sub-agents -> HITL)
pipeline. Mirrors the old orchestrator/state.py's PipelineState pattern,
adapted for hitl_orchestrator.py's actual fields.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class AgenticPipelineState(TypedDict, total=False):
    # Inputs, set once before the graph runs.
    query: str
    project_id: str
    mgmt_ips: dict
    inventory_str: str
    all_switches: list
    max_rounds: int
    retry_context: Optional[str]

    # router_node -> topo_sort_node -> run_subagents_node -> cumulate_node
    plan: object
    ordered_subplans: list
    prior_results: dict
    subagent_index: int
    subagent_context: dict
    combined_ir: list
    combined_notes: str



    # gate1_node
    gate1_accepted: bool

    # codegen_node
    playbook_yaml: str

    # apply_node
    session_id: str
    baseline_dir: str
    ansible_result: dict
    diff_bundle: object

    # gate2_node
    round_number: int
    gate2_accepted: bool
    gate2_feedback: str

    # final
    status: str
    stage: str
    error: Optional[str]

