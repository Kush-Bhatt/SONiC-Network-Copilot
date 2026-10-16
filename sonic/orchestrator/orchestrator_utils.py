"""
sonic/orchestrator/orchestrator_utils.py
----------------------------------------
Turns a validated plan (from PlannerAgent) into a runnable LangGraph for
the deterministic pre-Ansible portion of the pipeline.

The human-in-the-loop retry cycle is handled by the orchestrator after the
graph finishes its initial Ansible attempt. This module exposes the shared
helpers used by both the graph and the retry loop.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sonic.agents.ansible_agent import AnsibleAgent
from sonic.ansible.guard import format_guard_failure, scope_guard
from sonic.config.settings import ANSIBLE_DIR, VERIFICATION_ENABLED
from sonic.orchestrator.dispatch import INTENT_TO_AGENT, INTENT_TO_TOOL
from sonic.orchestrator.state import PipelineState
from sonic.switch.parser import build_full_context


# Input resolvers for deterministic tool steps


def _resolve_build_inventory_inputs(state: PipelineState) -> dict:
    return {}


TOOL_INPUT_RESOLVERS = {
    "build_inventory": _resolve_build_inventory_inputs,
}


# Shared runners


def _run_gather_state(state: PipelineState) -> dict:
    agent_fn, _ = INTENT_TO_AGENT["gather_state"]
    parsed_context = agent_fn(state["query"], state["mgmt_ips"])
    full_context = build_full_context(state["topology_str"], parsed_context)
    return {"parsed_context": parsed_context, "full_context": full_context}


def _run_ansible_attempt(state: PipelineState) -> dict:
    agent_class, execution_tool = INTENT_TO_AGENT["ansible_apply"]
    agent = agent_class(
        state["full_context"],
        state["inventory_str"],
        examples=state.get("examples"),
        repair_context=state.get("ansible_repair_context"),
    )

    round_number = state.get("round_number", 1)
    print(f"\n--- Generating ansible_playbook (round {round_number}) ---")

    user_message = state["query"]
    review_history = state.get("review_history") or []
    if review_history:
        last_feedback = (review_history[-1].get("feedback") or "").strip()
        if last_feedback:
            user_message = (
                f"{state['query']}\n\n"
                f"CORRECTION (round {round_number}): {last_feedback}\n"
                f"Apply this correction on top of the original request above. "
                f"Do not repeat the mistake that prompted this correction."
            )

    playbook = agent.chat(user_message)
    print("\n--- Ansible Playbook Generated ---\n")

    (ANSIBLE_DIR / "ansible_playbook.yaml").write_text(playbook, encoding="utf-8")
    if state.get("inventory_str"):
        (ANSIBLE_DIR / "hosts.ini").write_text(state["inventory_str"], encoding="utf-8")

    # Idea 3 — Pre-Apply Scope Guard. Refuse to run a playbook that goes outside
    # the approved IntentSpec BEFORE it touches the switches. A rejection is
    # surfaced as a failed apply so the existing acceptance gate + retry loop
    # regenerate — no second control flow.
    spec = state.get("intent_spec")
    if VERIFICATION_ENABLED and spec is not None:
        guard = scope_guard(playbook, spec, state.get("inventory_str") or "")
        if not guard.ok:
            report = format_guard_failure(spec, guard)
            print(f"\n{report}\n")
            return {
                "ansible_playbook": playbook,
                "ansible_result": {
                    "success": False,
                    "stdout": "",
                    "stderr": report,
                    "guard_rejected": True,
                },
            }

    result = execution_tool.invoke({})
    return {"ansible_playbook": playbook, "ansible_result": result}


AGENT_RUNNERS = {
    "gather_state": _run_gather_state,
    "ansible_apply": _run_ansible_attempt,
}


# Node factory


def make_node(step: dict):
    """Wrap one plan step into a LangGraph node function."""
    intent = step["intent"]
    output_key = step["output_key"]

    def node_fn(state: PipelineState) -> dict:
        print(f"\n[orchestrator] Running step {step['step_id']} ({intent})...")

        if intent == "build_inventory" and state.get("inventory_str") and state.get("mgmt_ips"):
            update = {
                output_key: {
                    "inventory_str": state["inventory_str"],
                    "mgmt_ips": state["mgmt_ips"],
                }
            }
        elif intent in INTENT_TO_TOOL:
            tool_fn = INTENT_TO_TOOL[intent]
            resolver = TOOL_INPUT_RESOLVERS[intent]
            inputs = resolver(state)
            result = tool_fn.invoke(inputs)
            update = {output_key: result}
        elif intent in AGENT_RUNNERS:
            update = AGENT_RUNNERS[intent](state)
        else:
            raise ValueError(
                f"[orchestrator] No dispatch registered for intent {intent!r} "
                f"(step_id={step['step_id']}) - add it to dispatch.py"
            )

        print(
            f"[orchestrator] Step {step['step_id']} ({intent}) -> "
            f"wrote state key(s): {list(update.keys())}"
        )
        return update

    return node_fn


# Graph builder


def build_graph(plan: dict):
    """
    Build and compile the pre-Ansible portion of the pipeline.

    The planner emits gather_state -> build_inventory -> ansible_apply.
    HITL diff/review/rollback happens after the graph returns the initial
    ansible result.
    """
    steps = plan["steps"]
    graph = StateGraph(PipelineState)

    node_names: list[str] = []
    seen_step_ids: set[int] = set()

    def node_name(step_id: int) -> str:
        return f"step_{step_id}"

    for step in steps:
        missing_deps = set(step.get("depends_on", [])) - seen_step_ids
        if missing_deps:
            raise ValueError(
                f"[orchestrator] Step {step['step_id']} depends on future steps: {sorted(missing_deps)}"
            )

        current_name = node_name(step["step_id"])
        graph.add_node(current_name, make_node(step))
        node_names.append(current_name)
        seen_step_ids.add(step["step_id"])

    if node_names:
        graph.add_edge(START, node_names[0])
        for left, right in zip(node_names, node_names[1:]):
            graph.add_edge(left, right)
        graph.add_edge(node_names[-1], END)

    return graph.compile()