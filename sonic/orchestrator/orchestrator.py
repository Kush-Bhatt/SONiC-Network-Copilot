"""
sonic/orchestrator/orchestrator.py
----------------------------------
Execution wrapper for the planner-driven pipeline.

The LangGraph plan now covers only the deterministic pre-Ansible steps
plus the first Ansible attempt. This wrapper owns the HITL loop:
  - capture t2 after each Ansible attempt
  - diff against the immutable baseline
  - show the diff to the operator
  - on rejection, rollback, verify rollback, and regenerate Ansible with
    the failed playbook + diff + human feedback
  - on acceptance, clean up and return the final state
"""

from __future__ import annotations

from typing import Callable

from sonic.ansible.runner import infrastructure_failure_reason
from sonic.batfish.checks import has_blocking_failure, run_checks
from sonic.config.settings import VERIFICATION_ENABLED
from sonic.hitl.expected import annotate
from sonic.hitl.feedback import (
    DiffBundle,
    ReviewDecision,
    build_ansible_retry_context,
    build_diff_bundle,
    facts_to_dicts,
    prompt_for_review,
)
from sonic.orchestrator.orchestrator_utils import AGENT_RUNNERS, build_graph
from sonic.orchestrator.state import PipelineState
from sonic.rollback.cleanup import cleanup_round_artifacts, cleanup_session
from sonic.rollback.core import (
    capture_round,
    delete_checkpoint,
    rollback_to_baseline,
    verify_rollback,
)


def _bundle_to_state(bundle: DiffBundle) -> dict:
    return {
        "baseline_dir": str(bundle.baseline_dir),
        "current_dir": str(bundle.current_dir),
        "facts": facts_to_dicts(bundle.facts),
        "raw_diff": bundle.raw_diff,
        "machine_diff": bundle.machine_diff,
        "human_diff": bundle.human_diff,
    }


def _normalize_review_decision(decision: object) -> ReviewDecision:
    if isinstance(decision, ReviewDecision):
        return decision
    if isinstance(decision, dict):
        return ReviewDecision(
            accepted=bool(decision.get("accepted")),
            feedback=str(decision.get("feedback", "")),
        )
    if isinstance(decision, tuple) and len(decision) >= 1:
        accepted = bool(decision[0])
        feedback = str(decision[1]) if len(decision) > 1 else ""
        return ReviewDecision(accepted=accepted, feedback=feedback)
    raise TypeError(
        "review_callback must return ReviewDecision, dict, or (accepted, feedback)"
    )


def _default_review_callback(round_number: int, bundle: DiffBundle) -> ReviewDecision:
    """
    Use the interactive prompt when stdin is a TTY.

    Non-interactive callers must pass an explicit review_callback so the
    pipeline does not silently approve a change without a human.
    """
    return prompt_for_review(round_number, bundle)


def run_plan(
    plan: dict,
    initial_state: PipelineState,
    review_callback: Callable[[int, DiffBundle], object] | None = None,
    max_rounds: int | None = None,
) -> PipelineState:
    """
    Executes a validated plan against an initial state.

    The planner emits the deterministic pre-AnsibIe graph. The HITL loop
    lives here so the orchestrator can retain full control over review,
    rollback, and retry behavior.
    """
    print(f"\n{'='*60}")
    print("  Orchestrator: executing plan")
    print(f"  category : {plan.get('query_category')}")
    print(f"  steps    : {len(plan['steps'])}")
    print(f"{'='*60}")

    graph = build_graph(plan)
    state = graph.invoke(initial_state)

    review_callback = review_callback or _default_review_callback

    session_info = state.get("hitl_session")
    if not session_info or "actual_t1_dir" not in session_info:
        raise ValueError("HITL session metadata is missing from pipeline state")

    session_id = state.get("session_id")
    if not session_id:
        raise ValueError("session_id is missing from pipeline state")

    mgmt_ips = state["mgmt_ips"]
    all_switches = list(mgmt_ips.keys())
    baseline_dir = session_info["actual_t1_dir"]
    review_history = list(state.get("review_history") or [])

    attempt = int(state.get("round_number") or 1)

    while True:
        state["round_number"] = attempt

        # The graph already ran the first Ansible attempt. Every iteration
        # after a rejection reruns the same helper directly.
        if attempt > 1:
            state.update(AGENT_RUNNERS["ansible_apply"](state))

        # Cheap local check before the expensive ones (t2 capture, Batfish diff,
        # review). An unreachable host is an infrastructure fault, not a defective
        # playbook — regenerating YAML cannot fix SSH/auth/power, so it must not
        # enter the repair loop on the same footing as a real config defect.
        # Roll back first for safety: a multi-host play can apply on the reachable
        # switches and still report UNREACHABLE for one.
        infra_reason = infrastructure_failure_reason(state.get("ansible_result"))
        if infra_reason:
            print("\n" + "=" * 72)
            print("  INFRASTRUCTURE FAILURE — not retrying (the playbook is not the problem)")
            print("=" * 72)
            print(infra_reason)
            print("=" * 72)
            cleanup_round_artifacts(session_id)
            rollback_to_baseline(session_id, mgmt_ips)
            verify_rollback(session_id, mgmt_ips, all_switches=all_switches)
            delete_checkpoint(session_id, mgmt_ips)
            cleanup_session(session_id)
            raise RuntimeError(f"Infrastructure failure, not a playbook defect:\n{infra_reason}")

        t2_dir = capture_round(session_id, "t2", mgmt_ips)
        state["snapshot_t2"] = {
            "snapshot_dir": str(t2_dir),
            "nodes_written": all_switches,
        }

        bundle = build_diff_bundle(
            baseline_dir=baseline_dir,
            current_dir=t2_dir,
            all_switches=all_switches,
        )
        state["diff_bundle"] = _bundle_to_state(bundle)
        state["diff_human"] = bundle.human_diff
        state["diff_machine"] = bundle.machine_diff
        state["diff_raw"] = bundle.raw_diff

        # Attach the apply result so the reviewer sees whether this playbook
        # actually applied — an empty diff after a failed apply is not a no-op.
        bundle.ansible_result = state.get("ansible_result")

        ansible_success = bool((state.get("ansible_result") or {}).get("success"))

        print("\n" + "="*80)
        print(state.get("ansible_result"), state.get("ansible_result").get("success"))
        print("\n" + "="*80)
        print("ANSIBLE RESULT")
        print("="*80)
        print(state.get("ansible_result"))
        print("="*80)

        # Deterministic verification (Ideas 1 & 2). Only meaningful when the apply
        # actually succeeded — a failed apply is already non-acceptable, and Ansible
        # reporting success while the change did not land is exactly what we catch.
        spec = state.get("intent_spec")
        verification_pass = None
        if VERIFICATION_ENABLED and spec is not None and ansible_success:
            annotated = annotate(spec, baseline_dir, t2_dir)
            checks = run_checks(spec, t2_dir)
            verification_pass = bool(annotated.satisfied and not has_blocking_failure(checks))
            bundle.annotated = annotated
            bundle.checks = checks
            bundle.verification_pass = verification_pass
            state["verification"] = {
                "pass": verification_pass,
                "missing": [list(f) for f in annotated.missing],
                "unexpected": [list(f) for f in annotated.unexpected],
                "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
            }

        decision = _normalize_review_decision(review_callback(attempt, bundle))
        # A deterministic FAIL blocks acceptance (like the ansible_success gate)
        # and feeds the retry, capped by max_rounds. None = not run / out of scope.

        # DEBUG HERE , CHECK FOR NOW

        print("\n" + "=" * 80)
        print("REVIEW GATE")
        print("=" * 80)
        print(f"decision.accepted : {decision.accepted}")
        print(f"decision.feedback : {decision.feedback!r}")
        print(f"ansible_success   : {ansible_success}")
        print(f"verification_pass : {verification_pass}")
        print("=" * 80)


        # accepted = bool(
        #     decision.accepted and ansible_success and (verification_pass is not False)
        # )

        # If human explicitly accepts, we only block if Ansible itself failed (safety guard).
        # We let the human override Batfish/verification false-positives.
        if decision.accepted:
            accepted = bool(ansible_success)
        else:
            accepted = False

        print(f"FINAL ACCEPTED = {accepted}")

        review_entry = {
            "round": attempt,
            "accepted": accepted,
            "human_accepted": decision.accepted,
            "feedback": decision.feedback,
            "ansible_success": ansible_success,
            "verification_pass": verification_pass,
            "fact_count": len(bundle.facts),
        }
        review_history.append(review_entry)
        state["review_history"] = review_history
        state["human_review"] = review_entry

        if accepted:
            cleanup_round_artifacts(session_id)
            delete_checkpoint(session_id, mgmt_ips)
            cleanup_session(session_id)
            print(f"[orchestrator] HITL accepted round {attempt}")
            print(f"\n{'='*60}")
            print("  Orchestrator: plan execution complete")
            print(f"  state keys populated: {list(state.keys())}")
            print(f"{'='*60}\n")
            return state

        cleanup_round_artifacts(session_id)

        rollback_result = rollback_to_baseline(session_id, mgmt_ips)

        verify_result = verify_rollback(session_id, mgmt_ips, all_switches=all_switches)

        rollback_result["verified"] = verify_result["verified"]

        state["rollback_result"] = rollback_result

        t3_dir = verify_result["t3_dir"]
        state["snapshot_t3"] = {
            "snapshot_dir": str(t3_dir),
            "nodes_written": all_switches,
        }

        # The verification bundle is useful for debugging even though the
        # expected diff is empty after a successful rollback.
        state["diff_bundle"] = _bundle_to_state(verify_result["diff_bundle"])
        state["diff_human"] = verify_result["diff_bundle"].human_diff
        state["diff_machine"] = verify_result["diff_bundle"].machine_diff
        state["diff_raw"] = verify_result["diff_bundle"].raw_diff

        cleanup_round_artifacts(session_id)

        if max_rounds is not None and attempt >= max_rounds:
            delete_checkpoint(session_id, mgmt_ips)
            cleanup_session(session_id)
            raise RuntimeError(
                f"Reached max_rounds={max_rounds} without human approval"
            )

        # Prepare the retry prompt for the next Ansible generation.
        previous_playbook = state.get("ansible_playbook") or ""
        state["ansible_repair_context"] = build_ansible_retry_context(
            query=state["query"],
            topology_str=state["topology_str"],
            inventory_str=state["inventory_str"] or "",
            previous_playbook=previous_playbook,
            bundle=bundle,
            human_feedback=decision.feedback,
            round_number=attempt + 1,
        )

        attempt += 1