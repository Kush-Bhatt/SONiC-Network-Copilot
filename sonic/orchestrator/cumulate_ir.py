"""
sonic/orchestrator/cumulate.py
--------------------------------
cumulate_ir() — merges multiple sub-agents' IR fragments into one list.
Pure Python, no LLM. Lives in orchestrator/ (not agents/) — same reasoning
as context.py: this is deterministic pipeline plumbing, not agent logic.

Fails hard (raises IRConflictError) on any real disagreement between
sub-agents rather than attempting to resolve it — same philosophy as
subplan.topo_sort() raising ValueError on a cycle. A conflict here means
two sub-agents produced genuinely inconsistent facts about the same
object, which must never be silently merged or pushed to the human as a
"pick one" — it should stop the pipeline before HITL Gate #1.
"""
from __future__ import annotations

from dataclasses import dataclass

from sonic.netfql.ir import IRObject


class IRConflictError(Exception):
    """Raised when two or more sub-agents produced conflicting IR for the
    same (device, object_type, object_id). Carries all conflicts found,
    not just the first, so they can all be reported at once."""
    def __init__(self, conflicts: list["IRConflict"]):
        self.conflicts = conflicts
        lines = "\n".join(f"  - {c}" for c in conflicts)
        super().__init__(f"{len(conflicts)} IR conflict(s) found:\n{lines}")


@dataclass
class IRConflict:
    device: str
    object_type: str
    object_id: str
    reason: str                    # human-readable description
    sources: list[tuple[int, IRObject]]  # (sub_plan_id, the conflicting IRObject)

    def __str__(self) -> str:
        srcs = ", ".join(f"sub_plan {sid}: verb={o.verb} state={o.expected_state}"
                          for sid, o in self.sources)
        return f"{self.device}/{self.object_type}/{self.object_id} — {self.reason} ({srcs})"


def cumulate_ir(
    results: dict[int, tuple[list[IRObject], str]],
) -> tuple[list[IRObject], str]:
    """
    Args:
      results: {sub_plan_id: (ir_objects, notes)} — the raw output of every
                sub-agent that ran, keyed by sub_plan_id. This is exactly
                the shape the fan-out loop accumulates prior_results in,
                so the orchestrator can pass its running dict straight in
                once all sub_plans are done.

    Returns:
      (combined_ir_objects, combined_notes) — combined_notes is each
      sub-agent's notes concatenated with a "[sub_plan N]" prefix, so
      nothing is silently dropped even if a sub-agent's notes flagged
      something for the human.

    Raises:
      IRConflictError if any two sub-agents disagree about the same
      (device, object_type, object_id).
    """
    # key -> list of (sub_plan_id, IRObject)
    groups: dict[tuple[str, str, str], list[tuple[int, IRObject]]] = {}
    for sub_plan_id, (ir_objects, _notes) in results.items():
        for obj in ir_objects:
            key = (obj.device, obj.object_type, obj.object_id)
            groups.setdefault(key, []).append((sub_plan_id, obj))

    combined: list[IRObject] = []
    conflicts: list[IRConflict] = []

    for (device, object_type, object_id), entries in groups.items():
        if len(entries) == 1:
            combined.append(entries[0][1])
            continue

        # Multiple sub-agents touched the same object — check verb agreement.
        verbs = {o.verb for _sid, o in entries}
        if len(verbs) > 1:
            conflicts.append(IRConflict(
                device, object_type, object_id,
                reason=f"conflicting verbs {sorted(verbs)}",
                sources=entries,
            ))
            continue

        # Same verb — check expected_state field-by-field.
        merged_state: dict[str, str] = {}
        state_conflict = False
        for _sid, o in entries:
            for k, v in o.expected_state.items():
                if k in merged_state and merged_state[k] != v:
                    state_conflict = True
                else:
                    merged_state[k] = v

        if state_conflict:
            conflicts.append(IRConflict(
                device, object_type, object_id,
                reason="conflicting expected_state values",
                sources=entries,
            ))
            continue

        # No disagreement — safe to merge into one object.
        first_sid, first_obj = entries[0]
        combined.append(IRObject(
            device=device, object_type=object_type, object_id=object_id,
            verb=first_obj.verb, expected_state=merged_state,
            device_display=first_obj.device_display,
        ))

    if conflicts:
        raise IRConflictError(conflicts)

    combined_notes = "\n".join(
        f"[sub_plan {sid}] {notes}"
        for sid, (_objs, notes) in sorted(results.items())
        if notes
    )
    return combined, combined_notes