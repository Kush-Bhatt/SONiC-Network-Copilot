"""
sonic/orchestrator/context.py
-------------------------------
build_scoped_context() — assembles the shared, session-constant context
every category sub-agent receives: topology_str, inventory_str, mgmt_ips.
Pure Python, no LLM. Lives in orchestrator/ (not agents/) because it does
no reasoning — it's pipeline plumbing, same role as orchestrator_utils.py's
other input-resolver helpers.
"""
from __future__ import annotations

from sonic.gns3.topology_builder import build_topology
from sonic.gns3.inventory_builder import build_inventory


def build_scoped_context() -> dict:
    """
    Builds the session-constant context every sub-agent needs.
    Safe to call once per session and reuse across all sub_plans — nothing
    here depends on which sub_plan or category is running, and none of it
    is invalidated by applies (that's live switch state, fetched separately
    by each sub-agent's own tools, not this function).

    Returns:
      {
        "topology_str"   : str  — from build_topology()
        "inventory_str"  : str  — hosts.ini content
        "mgmt_ips"       : dict — {switch_name: ip}
      }
    """
    inv = build_inventory.invoke({})
    mgmt_ips = inv["mgmt_ips"]
    topology_str = build_topology(mgmt_ips)
    return {
        "topology_str": topology_str,
        "inventory_str": inv["inventory_str"],
        "mgmt_ips": mgmt_ips,
    }