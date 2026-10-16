"""
sonic/orchestrator/agentic_orchestrator.py
---------------------------------------------
LangGraph entrypoint for the multi-agent (router -> sub-agents -> HITL)
pipeline. Replaces hitl_orchestrator.py's while-True loop with a compiled
StateGraph — see agentic_graph.py for node wiring, agentic_state.py for
the state schema.
"""
from __future__ import annotations

from sonic.agents.subplan_router_agent import SubPlanRouterAgent
from sonic.agents.ansible_codegen_agent import AnsibleCodeGenAgent
from sonic.orchestrator.context import build_scoped_context
from sonic.orchestrator.agentic_graph import build_agentic_graph
from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project, _build_inventory_str
from sonic.config.settings import MAX_RETRY_ROUNDS
# from IPython.display import Image, display


def run_agentic_hitl_pipeline(
    query: str,
    project_id: str,
    mgmt_ips: dict | None = None,
    max_rounds: int | None = None,
    gate1_callback=None,
    gate2_callback=None,
) -> dict:
    if mgmt_ips is None:
        mgmt_ips, _ = _load_mgmt_ips_for_project(project_id)
    inventory_str = _build_inventory_str(mgmt_ips)
    all_switches = list(mgmt_ips.keys())

    context = build_scoped_context()
    router = SubPlanRouterAgent()
    codegen_agent = AnsibleCodeGenAgent()

    graph = build_agentic_graph(
        router=router,
        codegen_agent=codegen_agent,
        context=context,
        gate1_callback=gate1_callback,
        gate2_callback=gate2_callback,
    )

    with open("./graph.png", "wb") as f:
        f.write((graph.get_graph().draw_mermaid_png()))

    initial_state = {
        "query": query,
        "project_id": project_id,
        "mgmt_ips": mgmt_ips,
        "inventory_str": inventory_str,
        "all_switches": all_switches,
        "max_rounds": max_rounds or MAX_RETRY_ROUNDS,
        "retry_context": None,
        "round_number": 1,
    }

    final_state = graph.invoke(initial_state, config={"recursion_limit": 50})
    return final_state


if __name__ == "__main__":
    from sonic.gns3.api_client import GNS3_PROJECT_ID

    result = run_agentic_hitl_pipeline(
        query="create vlan 402 on SONiC-VS-3, untagged member Ethernet80",
        project_id=GNS3_PROJECT_ID,
    )
    print("\nFINAL RESULT:", result.get("status"))
    print(result)