"""
sonic/orchestrator/agentic_graph_nodes.py
-------------------------------------------
LangGraph node functions for the multi-agent (router -> sub-agents -> HITL)
pipeline. Each node takes the full AgenticPipelineState dict and returns a
dict of the keys it updates (LangGraph merges this into state).

Built incrementally — nodes are added and tested one at a time before
wiring the actual graph (StateGraph) itself.
"""
from __future__ import annotations

from sonic.agents.subplan import topo_sort
from sonic.agents.vlan_ir_agent import run_vlan_ir_agent
from sonic.agents.bgp_ir_agent import run_bgp_ir_agent

import sys

from sonic.agents.ansible_codegen_agent import CodegenError
from sonic.orchestrator.cumulate_ir import cumulate_ir, IRConflictError
from sonic.orchestrator.agentic_state import AgenticPipelineState
from sonic.config.settings import ANSIBLE_DIR
from sonic.rollback.core import create_baseline, capture_round
from sonic.ansible.runner import run_ansible_playbook, infrastructure_failure_reason
from sonic.hitl.feedback import build_diff_bundle, prompt_for_review, ReviewDecision
from sonic.hitl.router_feedback import build_router_retry_context
from sonic.rollback.core import rollback_to_baseline, verify_rollback, delete_checkpoint
from sonic.rollback.cleanup import cleanup_round_artifacts, cleanup_session


from functools import partial

DISPATCH = {
    "vlan": run_vlan_ir_agent,
    "bgp": run_bgp_ir_agent,
}


def router_node(state: AgenticPipelineState, router, context: dict) -> dict:
    """Calls SubPlanRouterAgent.route(). Note: `router` is passed in
    separately (not stored in state) since it's a stateful agent object,
    not serializable pipeline data — same reasoning hitl_orchestrator.py
    already used for keeping router/codegen_agent outside the loop's
    local-variable state.
    """
    query = state["query"]
    retry_context = state.get("retry_context")

    print(f"\n[router_node] Routing: {query!r}")
    try:
        plan = router.route(query, retry_context=retry_context, context=context)
    except Exception as e:
        return {"stage": "route_failed", "error": f"ROUTER FAILED: {e}"}

    print(f"  [route] {len(plan.sub_plans)} sub_plan(s):")
    for sp in plan.sub_plans:
        print(f"    sub_plan_id={sp.sub_plan_id} task={sp.task!r} "
              f"focus={sp.focus!r} depends_on={sp.depends_on}")

    return {"plan": plan, "stage": "routed", "error": None}


def topo_sort_node(state: AgenticPipelineState) -> dict:
    """Pure Python topo sort — no LLM call. Separated from router_node so
    a topo-sort failure is distinguishable from a routing failure in
    state['stage']."""
    plan = state.get("plan")
    if plan is None:
        return {"stage": "sort_failed", "error": "TOPO_SORT FAILED: no plan in state"}

    try:
        ordered = topo_sort(plan.sub_plans)
    except Exception as e:
        return {"stage": "sort_failed", "error": f"TOPO_SORT FAILED: {e}"}

    return {"ordered_subplans": ordered, "stage": "sorted", "error": None}

def run_subagents_node(state: AgenticPipelineState, context: dict) -> dict:
    """Runs every sub_plan in topo order, threading prior_results filtered
    to each sub_plan's own depends_on. Kept as ONE node (not per-sub-agent
    nodes) so depends_on ordering stays exactly as proven in testing —
    see design discussion: Send()-based fan-out risked breaking BGP's
    'that same interface' resolution against VLAN's prior_results."""
    ordered = state.get("ordered_subplans")
    if not ordered:
        return {"stage": "dispatch_missing", "error": "NO ordered_subplans in state"}

    prior_results: dict[int, tuple[list, str]] = {}
    for sp in ordered:
        fn = DISPATCH.get(sp.task)
        if fn is None:
            return {
                "stage": "dispatch_missing",
                "error": f"NO DISPATCH for task={sp.task!r} (sub_plan {sp.sub_plan_id})",
                "prior_results": prior_results,
            }
        deps = {sid: prior_results[sid] for sid in sp.depends_on if sid in prior_results}
        try:
            ir_objects, notes = fn(prior_results=deps, context=context, focus=sp.focus)
            prior_results[sp.sub_plan_id] = (ir_objects, notes)
            print(f"  [{sp.task} #{sp.sub_plan_id}] {len(ir_objects)} object(s):")
            for obj in ir_objects:
                print(f"      {obj}")
            print(f"    notes: {notes or '(none)'}")
        except Exception as e:
            return {
                "stage": "subagent_failed",
                "error": f"SUB-AGENT '{sp.task}' (id={sp.sub_plan_id}) FAILED: {e}",
                "prior_results": prior_results,
            }

    return {"prior_results": prior_results, "stage": "subagents_done", "error": None}


def _run_one_subagent(state: AgenticPipelineState, context: dict, task: str) -> dict:
    ordered = state["ordered_subplans"]
    idx = state.get("subagent_index", 0)
    sp = ordered[idx]

    prior_results = dict(state.get("prior_results", {}))
    fn = DISPATCH.get(sp.task)
    deps = {sid: prior_results[sid] for sid in sp.depends_on if sid in prior_results}

    try:
        ir_objects, notes = fn(prior_results=deps, context=context, focus=sp.focus)
        prior_results[sp.sub_plan_id] = (ir_objects, notes)
        print(f"  [{sp.task} #{sp.sub_plan_id}] {len(ir_objects)} object(s):")
        for obj in ir_objects:
            print(f"      {obj}")
        print(f"    notes: {notes or '(none)'}")
    except Exception as e:
        return {"stage": "subagent_failed",
                "error": f"SUB-AGENT '{sp.task}' (id={sp.sub_plan_id}) FAILED: {e}",
                "prior_results": prior_results}

    return {"prior_results": prior_results, "subagent_index": idx + 1, "error": None}


vlan_subagent_node = partial(_run_one_subagent, task="vlan")
bgp_subagent_node = partial(_run_one_subagent, task="bgp")


def _route_next_subagent(state: AgenticPipelineState) -> str:
    if state.get("error"):
        return "failed"
    ordered = state["ordered_subplans"]
    idx = state.get("subagent_index", 0)
    if idx >= len(ordered):
        return "done"
    return ordered[idx].task



def cumulate_node(state: AgenticPipelineState) -> dict:
    """Pure Python merge — no LLM call. This is where Gate #1's review
    material (combined_ir, combined_notes) becomes available in state."""
    prior_results = state.get("prior_results")
    if prior_results is None:
        return {"stage": "cumulate_failed", "error": "CUMULATE FAILED: no prior_results in state"}

    try:
        combined_ir, combined_notes = cumulate_ir(prior_results)
    except IRConflictError as e:
        return {"stage": "cumulate_conflict", "error": f"CUMULATE CONFLICT: {e}"}
    except Exception as e:
        return {"stage": "cumulate_failed", "error": f"CUMULATE FAILED: {e}"}

    print(f"  [cumulate] {len(combined_ir)} combined object(s)")
    return {
        "combined_ir": combined_ir,
        "combined_notes": combined_notes,
        "stage": "ir_complete",
        "error": None,
    }

def gate1_node(state: AgenticPipelineState, gate1_callback=None) -> dict:
    """Human reviews IR + notes only, before any Ansible code exists.
    Reject = END immediately (status=cancelled), no feedback carried —
    a rejected IR means 'start over with a fresh query', by design
    decision, not a repair loop (that's what Gate #2 is for)."""

    def _default_gate1(result: dict) -> bool:
        print("\n" + "=" * 72)
        print("  GATE #1 — Review IR only (no code generated yet)")
        print("=" * 72)
        print(f"\n--- combined IR ({len(result['combined_ir'])} object(s)) ---")
        for obj in result["combined_ir"]:
            print(" ", obj)
        print("\n--- combined notes ---")
        print(result["combined_notes"] or "(none)")
        print("=" * 72)

        if not sys.stdin.isatty():
            raise RuntimeError(
                "Gate #1 approval requested but stdin is not a TTY. "
                "Pass an explicit gate1_callback for non-interactive runs."
            )
        answer = input("Approve this IR and generate the playbook? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    gate1 = gate1_callback or _default_gate1
    accepted = gate1({
        "combined_ir": state.get("combined_ir", []),
        "combined_notes": state.get("combined_notes", ""),
    })

    if not accepted:
        return {"gate1_accepted": False, "status": "cancelled", "error": None}
    return {"gate1_accepted": True, "error": None}


def codegen_node(state: AgenticPipelineState, codegen_agent) -> dict:
    """Only reached after Gate #1 acceptance."""
    combined_ir = state.get("combined_ir", [])
    try:
        playbook_yaml = codegen_agent.generate(combined_ir)
    except CodegenError as e:
        return {"stage": "codegen_failed", "error": f"CODEGEN FAILED: {e}"}
    except Exception as e:
        return {"stage": "codegen_crashed", "error": f"CODEGEN CRASHED: {e}"}

    print(f"  [codegen] {len(playbook_yaml)} chars")
    return {"playbook_yaml": playbook_yaml, "stage": "codegen_done", "error": None}


def apply_node(state: AgenticPipelineState) -> dict:
    """Creates baseline, applies the playbook, captures t2, builds the
    diff bundle Gate #2 needs. Mirrors hitl_orchestrator.py's Stage 3
    body exactly — this is the piece with real infra side-effects
    (SSH, checkpoints), so it stays a single node rather than being
    split further."""
    from sonic.orchestrator.run_pipeline import _make_session_id  # local import, unchanged source

    mgmt_ips = state["mgmt_ips"]
    all_switches = state["all_switches"]
    inventory_str = state["inventory_str"]
    playbook_yaml = state["playbook_yaml"]

    session_id = _make_session_id()
    print("\n[apply_node] Creating baseline...")
    hitl_session = create_baseline(session_id, mgmt_ips)
    baseline_dir = hitl_session["actual_t1_dir"]

    (ANSIBLE_DIR / "ansible_playbook.yaml").write_text(playbook_yaml, encoding="utf-8")
    (ANSIBLE_DIR / "hosts.ini").write_text(inventory_str, encoding="utf-8")

    print("[apply_node] Applying playbook...")
    ansible_result = run_ansible_playbook.invoke({})

    infra_reason = infrastructure_failure_reason(ansible_result)
    if infra_reason:
        return {
            "session_id": session_id,
            "baseline_dir": baseline_dir,
            "ansible_result": ansible_result,
            "stage": "infra_failure",
            "error": infra_reason,
        }

    t2_dir = capture_round(session_id, "t2", mgmt_ips)
    bundle = build_diff_bundle(baseline_dir=baseline_dir, current_dir=t2_dir, all_switches=all_switches)
    # [DEBUG FOR BGP BATFISH PRINTS]
    for f in bundle.facts:
        if f.table.startswith("bgp"):
            print(f"[DEBUG BGP FACT] table={f.table} node={f.node} change={f.change} detail={f.detail}")
    # [DEBUG]
    bundle.ansible_result = ansible_result

    return {
        "session_id": session_id,
        "baseline_dir": baseline_dir,
        "ansible_result": ansible_result,
        "diff_bundle": bundle,
        "stage": "applied",
        "error": None,
    }

def gate2_node(state: AgenticPipelineState, gate2_callback=None) -> dict:
    """Human reviews the real diff. Accept requires BOTH human approval
    AND ansible_result['success'] — a human 'y' cannot override a genuine
    apply failure, matching hitl_orchestrator.py's existing safety rule."""
    gate2 = gate2_callback or prompt_for_review
    round_number = state.get("round_number", 1)
    bundle = state.get("diff_bundle")

    decision = gate2(round_number, bundle)
    if isinstance(decision, dict):
        decision = ReviewDecision(accepted=bool(decision.get("accepted")), feedback=str(decision.get("feedback", "")))
    elif isinstance(decision, tuple):
        decision = ReviewDecision(accepted=bool(decision[0]), feedback=str(decision[1]) if len(decision) > 1 else "")

    ansible_result = state.get("ansible_result") or {}
    ansible_success = bool(ansible_result.get("success"))
    accepted = bool(decision.accepted and ansible_success)

    if decision.accepted and not ansible_success:
        print("\n[gate2_node] NOTE: you accepted, but the Ansible apply itself "
              "failed — acceptance is overridden for safety. Rolling back.")

    return {"gate2_accepted": accepted, "gate2_feedback": decision.feedback, "error": None}


def cleanup_node(state: AgenticPipelineState) -> dict:
    """Terminal success path: delete checkpoint, clean session artifacts."""
    session_id = state["session_id"]
    mgmt_ips = state["mgmt_ips"]

    cleanup_round_artifacts(session_id)
    delete_checkpoint(session_id, mgmt_ips)
    cleanup_session(session_id)

    return {"status": "complete", "error": None}


def rollback_node(state: AgenticPipelineState) -> dict:
    """Gate #2 rejected — roll back to baseline and verify."""
    session_id = state["session_id"]
    mgmt_ips = state["mgmt_ips"]
    all_switches = state["all_switches"]
    round_number = state.get("round_number", 1)

    print(f"\n[rollback_node] Gate #2 rejected (round {round_number}). Rolling back...")
    cleanup_round_artifacts(session_id)
    rollback_to_baseline(session_id, mgmt_ips)
    verify_rollback(session_id, mgmt_ips, all_switches=all_switches)
    cleanup_round_artifacts(session_id)

    return {"error": None}


def build_retry_context_node(state: AgenticPipelineState) -> dict:
    """Builds the router retry prompt from this round's failure, then
    advances round_number and clears the checkpoint before looping back
    to router_node."""
    session_id = state["session_id"]
    mgmt_ips = state["mgmt_ips"]
    round_number = state.get("round_number", 1)
    bundle = state.get("diff_bundle")
    ansible_result = state.get("ansible_result") or {}

    retry_context = build_router_retry_context(
        prior_plan=state.get("plan"),
        prior_results=state.get("prior_results"),
        combined_notes=state.get("combined_notes"),
        playbook_yaml=state.get("playbook_yaml"),
        ansible_result=ansible_result,
        diff_human=bundle.human_diff if bundle else "",
        human_feedback=state.get("gate2_feedback", ""),
        round_number=round_number + 1,
    )

    delete_checkpoint(session_id, mgmt_ips)
    cleanup_session(session_id)

    return {"retry_context": retry_context, "round_number": round_number + 1, "error": None}


def max_rounds_reached(state: AgenticPipelineState) -> bool:
    """Routing predicate (used in a conditional edge, not a node itself)."""
    round_number = state.get("round_number", 1)
    max_rounds = state.get("max_rounds", 1)
    return round_number >= max_rounds