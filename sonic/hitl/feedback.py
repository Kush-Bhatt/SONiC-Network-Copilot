"""
sonic/hitl/feedback.py
----------------------
Helpers for the human-in-the-loop repair cycle.

This module turns a baseline snapshot plus a live follow-up capture into
reviewable diff artifacts, and it builds the retry prompt that gets sent
back into AnsibleAgent when a human rejects a change.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sonic.hitl.facts import Fact, extract_facts, extract_raw_diff, facts_to_dicts
from sonic.hitl.render import render_for_human, render_for_machine


@dataclass(slots=True)
class ReviewDecision:
    """Human review outcome for a single Ansible round."""

    accepted: bool
    feedback: str = ""


@dataclass(slots=True)
class DiffBundle:
    """Structured diff bundle built from a baseline and a live capture."""

    baseline_dir: Path
    current_dir: Path
    facts: list[Fact]
    raw_diff: dict[str, dict[str, str]]
    machine_diff: dict[str, Any]
    human_diff: str
    # Result of the ansible-playbook apply that produced this capture, so the
    # reviewer can tell an empty diff caused by "nothing to do" apart from one
    # caused by a failed apply. Populated by the orchestrator before review.
    ansible_result: dict[str, Any] | None = None

    # Deterministic verification, attached by the orchestrator before review.
    # annotated: hitl.expected.AnnotatedResult (Idea 1)
    # checks:    list[batfish.checks.CheckResult] (Idea 2)
    # verification_pass: annotated.satisfied AND no FAIL check. None = not run
    # (e.g. an out-of-scope category, or verification disabled).
    annotated: Any | None = None
    checks: list[Any] | None = None
    verification_pass: bool | None = None

    def has_changes(self) -> bool:
        if self.facts:
            return True
        return any(
            tier.get("config_db") or tier.get("frr")
            for tier in self.raw_diff.values()
        )

    def machine_json(self) -> str:
        return json.dumps(self.machine_diff, indent=2, default=str)

    def raw_json(self) -> str:
        return json.dumps(self.raw_diff, indent=2, default=str)


def build_diff_bundle(
    baseline_dir: Path,
    current_dir: Path,
    all_switches: list[str] | None = None,
) -> DiffBundle:
    """Build raw + structured diff artifacts for a round."""
    facts = extract_facts(baseline_dir, current_dir)
    raw_diff = extract_raw_diff(baseline_dir, current_dir)
    machine_diff = render_for_machine(facts, all_switches=all_switches)
    human_diff = render_for_human(facts, all_switches=all_switches)
    return DiffBundle(
        baseline_dir=baseline_dir,
        current_dir=current_dir,
        facts=facts,
        raw_diff=raw_diff,
        machine_diff=machine_diff,
        human_diff=human_diff,
    )


def build_review_display(bundle: DiffBundle) -> str:
    """Formats the diff bundle for human review in the terminal."""
    lines = []
    # The apply result must be shown FIRST. A failed apply produces an empty
    # diff, and "no differences detected" on its own reads as "nothing to do"
    # when it actually means "nothing ran" — the exact confusion DiffBundle
    # carries ansible_result to prevent.
    result = bundle.ansible_result or {}
    if result and not result.get("success"):
        lines += [
            "=== Apply FAILED — nothing was applied ===",
            _apply_result_section(bundle),
            "",
        ]
    if bundle.annotated is not None:
        from sonic.hitl.render import render_annotated_diff

        lines += [
            "=== Deterministic Verification ===",
            render_annotated_diff(bundle.annotated, bundle.checks),
            "",
        ]
    lines += [
        "=== Machine Diff ===",
        bundle.machine_json(),
        "",
        "=== Human Diff ===",
        bundle.human_diff or "(no structured differences detected)",
        "",
        "=== Raw Diff ===",
        bundle.raw_json(),
    ]
    return "\n".join(lines)


def _verification_section(bundle: DiffBundle) -> str:
    """The failing rows only (design doc §5.7) — a compact, specific error signal
    repairs better than a wall of text."""
    if bundle.annotated is None:
        return ""
    parts = []
    ann = bundle.annotated
    if ann.missing:
        parts.append("APPROVED BUT NOT APPLIED (fix these):")
        parts += [f"  {f.device} {f.kind} {f.key} {f.value}" for f in ann.missing]
    if ann.unexpected:
        parts.append("NOT IN APPROVED SPEC (remove these):")
        parts += [f"  {f.device} {f.kind} {f.key} {f.value}" for f in ann.unexpected]
    for c in bundle.checks or []:
        if c.status == "FAIL":
            parts.append(f"BATFISH FAIL: {c.name} — {c.detail}")
    return "\n".join(parts) if parts else ""


def prompt_for_review(round_number: int, bundle: DiffBundle) -> ReviewDecision:
    """Interactive terminal review helper."""
    print("\n" + "=" * 72)
    print(f"  HITL Review - Round {round_number}")
    print("=" * 72)
    print(build_review_display(bundle))
    print("=" * 72)

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Interactive HITL review was requested, but stdin is not a TTY. "
            "Pass an explicit review callback for non-interactive runs."
        )

    answer = input("Accept this change? [y/N or type feedback directly]: ").strip()
    lowered = answer.lower()

    if lowered in {"y", "yes"}:
        return ReviewDecision(accepted=True, feedback="")

    if lowered in {"n", "no", ""}:
        feedback = input("Feedback for the next Ansible attempt: ").strip()
        return ReviewDecision(accepted=False, feedback=feedback)

    # Anything else typed at this prompt is treated as a rejection *and*
    # the feedback itself — covers the common case of typing the reason
    # directly instead of "n" first.
    return ReviewDecision(accepted=False, feedback=answer)


def auto_accept_review(round_number: int, bundle: DiffBundle) -> ReviewDecision:
    """Non-interactive helper for tests or scripted runs."""
    feedback = "auto-accepted by callback"
    if bundle.has_changes():
        feedback = "auto-accepted with remaining diff"
    return ReviewDecision(accepted=True, feedback=feedback)


def _tail(text: str | None, n: int = 25) -> str:
    lines = (text or "").strip().splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join(["... (truncated) ..."] + lines[-n:])


def _apply_result_section(bundle: DiffBundle) -> str:
    """Surface the previous ansible-playbook run so regeneration can see WHY it
    failed. A failed apply produces an empty diff, so without this the agent is
    blind to syntax/structure errors like a play-level `when:`."""
    result = bundle.ansible_result or {}
    if not result:
        return "(no apply result captured)"

    lines = [
        "SUCCEEDED"
        if result.get("success")
        else "FAILED (ansible-playbook exited non-zero) — fix this before anything else"
    ]
    stderr = _tail(result.get("stderr"))
    stdout = _tail(result.get("stdout"))
    if stderr:
        lines += ["stderr (tail):", stderr]
    if stdout:
        lines += ["stdout (tail):", stdout]
    return "\n".join(lines)


def build_ansible_retry_context(
    *,
    query: str,
    topology_str: str,
    inventory_str: str,
    previous_playbook: str,
    bundle: DiffBundle,
    human_feedback: str,
    round_number: int,
) -> str:
    """
    Compose the retry context that gets appended to the Ansible prompt.

    The original user query stays the user message; this block carries the
    failed playbook, the previous apply result (stdout/stderr), the
    machine/human diff, and the operator's feedback.
    """
    sections = [
        "# HITL RETRY CONTEXT",
        f"Round: {round_number}",
        "",
        "# Original Query",
        query,
        "",
        "# Topology",
        topology_str,
        "",
        "# Inventory",
        inventory_str,
        "",
        "# Failed Playbook",
        previous_playbook.strip(),
        "",
        "# Previous Apply Result",
        _apply_result_section(bundle),
        "",
        "# Deterministic Verification Failures",
        _verification_section(bundle) or "(none)",
        "",
        "# Machine Diff",
        bundle.machine_json(),
        "",
        "# Human Diff",
        bundle.human_diff or "(no structured differences detected)",
        "",
        "# Human Feedback",
        human_feedback.strip() or "(no feedback provided)",
        "",
        "# Instructions",
        "If the apply FAILED above, the diff will be empty — the real problem is the",
        "error in the apply output (e.g. an Ansible syntax/structure error). Fix that first.",
        "Revise the playbook to address the feedback and preserve the parts that were correct.",
        "Return only valid Ansible YAML.",
    ]
    return "\n".join(sections)