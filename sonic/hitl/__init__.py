"""
sonic/hitl/
-----------
Deterministic human-in-the-loop helpers.

facts.py   -> raw and structured diff extraction
render.py  -> human and machine renderers for diff facts
feedback.py -> review bundles, review decisions, and retry prompt helpers
"""

from sonic.hitl.feedback import (
    DiffBundle,
    ReviewDecision,
    build_ansible_retry_context,
    build_diff_bundle,
    build_review_display,
)

__all__ = [
    "DiffBundle",
    "ReviewDecision",
    "build_ansible_retry_context",
    "build_diff_bundle",
    "build_review_display",
]