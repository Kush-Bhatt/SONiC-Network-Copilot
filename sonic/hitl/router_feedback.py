"""
sonic/hitl/router_feedback.py
------------------------------
Builds the retry context appended to the router's user message on a
Gate #2 rejection — the query re-enters at the ROUTER, not just codegen,
per project decision: router may need to change sub_plans/focus (e.g. pick
a different interface), not just regenerate the playbook from the same IR.
"""
from __future__ import annotations

from typing import Any


def _format_ir_by_subplan(prior_results: dict) -> str:
    lines = []
    for sid, (ir_objects, notes) in sorted(prior_results.items()):
        lines.append(f"  sub_plan {sid}:")
        for obj in ir_objects:
            lines.append(f"    {obj}")
        lines.append(f"    notes: {notes or '(none)'}")
    return "\n".join(lines) if lines else "(none)"


def _format_sub_plans(plan) -> str:
    lines = []
    for sp in plan.sub_plans:
        lines.append(
            f"  sub_plan_id={sp.sub_plan_id} task={sp.task!r} "
            f"focus={sp.focus!r} depends_on={sp.depends_on}"
        )
    return "\n".join(lines)


def build_router_retry_context(
    *,
    prior_plan: Any,
    prior_results: dict,
    combined_notes: str,
    playbook_yaml: str,
    ansible_result: dict | None,
    diff_human: str,
    human_feedback: str,
    round_number: int,
) -> str:
    """Composes the '# RETRY CONTEXT' block appended to the router's next
    user message. See SubPlanRouterAgent's system prompt for how it's read."""
    ansible_result = ansible_result or {}
    apply_status = "SUCCEEDED" if ansible_result.get("success") else "FAILED"
    apply_detail = ""
    if not ansible_result.get("success"):
        apply_detail = (ansible_result.get("stderr") or ansible_result.get("stdout") or "").strip()

    sections = [
        "# RETRY CONTEXT",
        f"Round: {round_number}",
        "",
        "# Prior Sub-Plans",
        _format_sub_plans(prior_plan),
        "",
        "# Prior IR + Notes",
        _format_ir_by_subplan(prior_results),
        "",
        "# Prior Combined Notes",
        combined_notes or "(none)",
        "",
        "# Prior Playbook",
        playbook_yaml.strip(),
        "",
        "# Apply Result",
        apply_status + (f" — {apply_detail}" if apply_detail else ""),
        "",
        "# Diff After Apply",
        diff_human or "(no structured differences detected)",
        "",
        "# Human Feedback (why this was rejected)",
        human_feedback.strip() or "(no feedback provided)",
        "",
        "# Instructions",
        "The prior attempt was rejected. Produce a corrected set of sub_plans",
        "that addresses the human feedback above. Keep sub_plans that were not",
        "implicated in the feedback unchanged in scope (same device/category),",
        "but you may adjust their focus text if the feedback affects them too.",
    ]
    return "\n".join(sections)