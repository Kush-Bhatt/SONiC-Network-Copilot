"""
sonic/agents/subplan.py
------------------------
SubPlan model + topo-sort for the multi-agent routing pipeline.
Planner is routing-only: it decides WHICH category sub-agents run and in
what order (depends_on). It does NOT extract statements or IR — each
category sub-agent does its own extraction against the full original query.
"""
from __future__ import annotations

from pydantic import BaseModel, model_validator


class SubPlan(BaseModel):
    sub_plan_id: int
    task: str
    focus: str = ""     # short directive from the router: what this sub-agent
                         # should extract from the full query, e.g. "VLAN 230
                         # on SONiC-VS-1, SVI on Ethernet16". The sub-agent
                         # still receives the full verbatim query too — focus
                         # narrows attention, it never replaces context.
    depends_on: list[int] = []
    

class RoutedPlan(BaseModel):
    query: str                 # verbatim original user query — every sub-agent sees this unmodified
    sub_plans: list[SubPlan]

    @model_validator(mode="after")
    def _check_ids_and_deps(self) -> "RoutedPlan":
        ids = [sp.sub_plan_id for sp in self.sub_plans]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate sub_plan_id values: {ids}")
        id_set = set(ids)
        for sp in self.sub_plans:
            bad = set(sp.depends_on) - id_set
            if bad:
                raise ValueError(f"sub_plan {sp.sub_plan_id} depends_on unknown id(s): {bad}")
            if sp.sub_plan_id in sp.depends_on:
                raise ValueError(f"sub_plan {sp.sub_plan_id} depends on itself")
        return self


def topo_sort(sub_plans: list[SubPlan]) -> list[SubPlan]:
    """
    Deterministic Kahn's-algorithm topo sort by depends_on.
    Raises ValueError on a cycle — a cycle means the planner produced an
    unsatisfiable plan and must not be executed.
    """
    by_id = {sp.sub_plan_id: sp for sp in sub_plans}
    in_degree = {sp.sub_plan_id: len(sp.depends_on) for sp in sub_plans}
    remaining_edges = {
        sid: {sp2.sub_plan_id for sp2 in sub_plans if sid in sp2.depends_on}
        for sid in by_id
    }

    ready = sorted(sid for sid, deg in in_degree.items() if deg == 0)
    ordered: list[SubPlan] = []

    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for downstream in sorted(remaining_edges[current]):
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                ready.append(downstream)
        ready.sort()

    if len(ordered) != len(sub_plans):
        unresolved = set(by_id) - {sp.sub_plan_id for sp in ordered}
        raise ValueError(f"Cycle detected among sub_plan_id(s): {unresolved}")

    return ordered