"""
sonic/rollback/errors.py
--------------------------
RollbackError — raised on any per-switch failure during rollback.
Hard-stop, never partial: the moment one switch fails, the caller stops
and surfaces this to the human rather than continuing against a
now-inconsistent topology.

Beyond being a hard-stop signal, this carries enough structure (which
switch, at what stage, which command, what already succeeded) to render
a clear message for the human reviewing the HITL session — not just a
bare traceback.
"""

from __future__ import annotations


class RollbackError(Exception):
    """Raised on any per-switch failure during rollback — hard-stop, never partial.

    Can be raised the plain way, RollbackError("some message"), for quick
    call sites that don't have structured context to give. Call sites that
    do know which switch/stage/command failed should pass those in so the
    rendered message tells the human exactly where the topology was left.
    """

    def __init__(
        self,
        message: str,
        *,
        switch: str | None = None,
        stage: str | None = None,
        command: str | None = None,
        detail: str | None = None,
        completed: list[str] | None = None,
    ):
        self.message = message
        self.switch = switch
        self.stage = stage
        self.command = command
        self.detail = detail
        self.completed = completed or []
        super().__init__(self.format())

    def format(self) -> str:
        """Human-readable rendering of the failure for HITL surfacing."""
        lines = ["Rollback failed: " + self.message]

        if self.switch:
            lines.append(f"  switch    : {self.switch}")
        if self.stage:
            lines.append(f"  stage     : {self.stage}")
        if self.command:
            lines.append(f"  command   : {self.command}")
        if self.detail:
            lines.append(f"  error     : {self.detail}")
        if self.completed:
            lines.append(f"  completed : {', '.join(self.completed)} (before hard-stop)")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()