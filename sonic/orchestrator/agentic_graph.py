"""
sonic/orchestrator/agentic_graph.py
--------------------------------------
Wires agentic_graph_nodes.py's node functions into an actual LangGraph
StateGraph. build_agentic_graph() takes the stateful objects (router,
codegen_agent, context, callbacks) that node functions need beyond state,
binds them via functools.partial, and returns a compiled graph ready
for .invoke(initial_state).
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import StateGraph, END

from sonic.orchestrator.agentic_state import AgenticPipelineState
from sonic.orchestrator.agentic_graph_nodes import (
    router_node, topo_sort_node, run_subagents_node, cumulate_node,
    gate1_node, codegen_node, apply_node, gate2_node, cleanup_node,
    rollback_node, build_retry_context_node, max_rounds_reached,
    vlan_subagent_node, bgp_subagent_node, _route_next_subagent,
)



def _route_after_ir_stage(state: dict) -> str:
    """After router/topo_sort/subagents/cumulate: any hard failure short-
    circuits straight to END with status='pipeline_failed'."""
    if state.get("error"):
        return "pipeline_failed"
    return "ok"


def _route_after_gate1(state: dict) -> str:
    return "accepted" if state.get("gate1_accepted") else "rejected"


def _route_after_apply(state: dict) -> str:
    if state.get("stage") == "infra_failure":
        return "infra_failure"
    return "ok"


def _route_after_gate2(state: dict) -> str:
    return "accepted" if state.get("gate2_accepted") else "rejected"


def _route_after_rollback(state: dict) -> str:
    return "max_rounds" if max_rounds_reached(state) else "retry"


def build_agentic_graph(router, codegen_agent, context: dict,
                         gate1_callback=None, gate2_callback=None):
    g = StateGraph(AgenticPipelineState)

    g.add_node("router", partial(router_node, router=router, context=context))
    g.add_node("topo_sort", topo_sort_node)
    # g.add_node("run_subagents", partial(run_subagents_node, context=context))
    g.add_node("vlan_subagent", partial(vlan_subagent_node, context=context))
    g.add_node("bgp_subagent", partial(bgp_subagent_node, context=context))
    # g.add_node("subagent_dispatch", lambda state: {})
    g.add_node("subagent_dispatch", lambda state: print(f"[dispatch] idx={state.get('subagent_index')} error={state.get('error')}") or {})
    #
    g.add_node("cumulate", cumulate_node)
    g.add_node("gate1", partial(gate1_node, gate1_callback=gate1_callback))
    g.add_node("codegen", partial(codegen_node, codegen_agent=codegen_agent))
    g.add_node("apply", apply_node)
    g.add_node("gate2", partial(gate2_node, gate2_callback=gate2_callback))
    g.add_node("cleanup", cleanup_node)
    g.add_node("rollback", rollback_node)
    g.add_node("build_retry_context", build_retry_context_node)

    g.set_entry_point("router")

    g.add_conditional_edges("router", _route_after_ir_stage,
                             {"ok": "topo_sort", "pipeline_failed": END})
    # ================================================
    g.add_conditional_edges("topo_sort", _route_after_ir_stage,
                             {"ok": "subagent_dispatch", "pipeline_failed": END})

    g.add_conditional_edges("subagent_dispatch", _route_next_subagent,
                             {"vlan": "vlan_subagent", "bgp": "bgp_subagent",
                              "done": "cumulate", "failed": END})

    g.add_edge("vlan_subagent", "subagent_dispatch")
    g.add_edge("bgp_subagent", "subagent_dispatch")
    # =====================================================
    g.add_conditional_edges("cumulate", _route_after_ir_stage,
                             {"ok": "gate1", "pipeline_failed": END})

    g.add_conditional_edges("gate1", _route_after_gate1,
                             {"accepted": "codegen", "rejected": END})

    g.add_conditional_edges("codegen", _route_after_ir_stage,
                             {"ok": "apply", "pipeline_failed": END})

    g.add_conditional_edges("apply", _route_after_apply,
                             {"ok": "gate2", "infra_failure": "rollback"})

    g.add_conditional_edges("gate2", _route_after_gate2,
                             {"accepted": "cleanup", "rejected": "rollback"})

    g.add_edge("cleanup", END)

    g.add_conditional_edges("rollback", _route_after_rollback,
                             {"max_rounds": END, "retry": "build_retry_context"})

    g.add_edge("build_retry_context", "router")

    return g.compile()