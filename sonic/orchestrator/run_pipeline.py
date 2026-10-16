"""
sonic/orchestrator/run_pipeline.py
----------------------------------
Entrypoint for the planner-driven pipeline.

Flow:
  1. Resolve mgmt_ips and inventory_str for the current GNS3 project.
  2. Build static topology + load metadata overview.
  3. Retrieve the best matching examples.
  4. Create the HITL baseline once for this session.
  5. Ask PlannerAgent for the pre-Ansible plan.
  6. Run the graph + HITL retry loop in the orchestrator.
  7. Return the final state dict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sonic.agents.intent_spec import IntentSpecAgent
from sonic.agents.planner_agent import PlannerAgent
from sonic.config.settings import (
    BASE_DIR,
    DATASET_DIR,
    MAX_RETRY_ROUNDS,
    SSH_PASSWORD,
    SSH_USERNAME,
    VERIFICATION_ENABLED,
)
from sonic.gns3.topology_builder import build_topology
# from sonic.hitl.expected import current_vlan_view
from sonic.hitl.render import render_intent_spec
from sonic.orchestrator.orchestrator import run_plan
from sonic.orchestrator.state import PipelineState
from sonic.retrieval.factory import get_retriever
from sonic.rollback.cleanup import cleanup_session
from sonic.rollback.core import create_baseline, delete_checkpoint
from sonic.utils.metadata_loader import (
    load_metadata_overview,
    load_metadata_overview_excluding,
)

METADATA_ROOT = str(DATASET_DIR / "metadata")
MGMT_IPS_PATH = BASE_DIR / "config" / "mgmt_ips.json"


def _load_mgmt_ips_for_project(project_id: str) -> tuple[dict, str]:
    """
    Returns (mgmt_ips, topology_name) for the given project_id.
    """
    import json

    with open(MGMT_IPS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    if project_id not in cfg:
        available = {k: v.get("_name") for k, v in cfg.items() if not k.startswith("_")}
        raise ValueError(
            f"project_id={project_id!r} not found in {MGMT_IPS_PATH}.\n"
            f"Available: {available}"
        )

    entry = cfg[project_id]
    topology_name = entry.get("_name", project_id)
    mgmt_ips = {k: v for k, v in entry.items() if not k.startswith("_")}
    return mgmt_ips, topology_name


def _build_inventory_str(mgmt_ips: dict) -> str:
    lines = ["[sonic_switches]"] + [
        f"{sw} ansible_host={ip} ansible_user={SSH_USERNAME} "
        f"ansible_password={SSH_PASSWORD} ansible_connection=ssh ansible_network_os=sonic"
        for sw, ip in mgmt_ips.items()
    ]
    return "\n".join(lines) + "\n"


def _load_examples_from_selection(selected_example: dict) -> dict:
    """
    Converts retriever metadata into actual example file contents.

    Only Ansible examples are carried into the current HITL pipeline.
    """
    examples = {}

    ansible_ex = selected_example.get("ansible")
    if ansible_ex and ansible_ex.get("resource"):
        path = DATASET_DIR / ansible_ex["resource"]
        if path.exists():
            examples["ansible"] = path.read_text(encoding="utf-8")
            examples["type"] = ansible_ex.get("category")
        else:
            print(f"[run_pipeline] WARNING: {path} does not exist - skipping")

    return examples


# def _default_approval_callback(spec, baseline_dir=None) -> bool:
#     """CLI IntentSpec approval gate (design doc §3.3). Approval freezes the spec
#     as the reference point for Ideas 1 & 3. Returns True to proceed.

#     `baseline_dir` is the pre-change snapshot (captured before this gate runs,
#     see run_pipeline()) so the render can show a real current-vs-proposed
#     delta instead of a proposed-values-only listing."""
#     import sys

#     current = current_vlan_view(spec, baseline_dir) if (baseline_dir and spec.category == "vlan") else None

#     print("\n" + "=" * 72)
#     print(render_intent_spec(spec, current=current))
#     print("=" * 72)

#     if not sys.stdin.isatty():
#         raise RuntimeError(
#             "IntentSpec approval was requested, but stdin is not a TTY. "
#             "Pass an explicit approval_callback for non-interactive runs."
#         )
#     answer = input("Approve this intent? [y/N]: ").strip().lower()
#     return answer in {"y", "yes"}


def _default_approval_callback(spec) -> bool:
    """CLI IntentSpec approval gate.

    The operator approves only the interpreted intent. No network state is
    consulted here; this stage exists solely to verify that the system
    understood the user's request correctly.
    """
    import sys

    print("\n" + "=" * 72)
    print(render_intent_spec(spec))
    print("=" * 72)

    if not sys.stdin.isatty():
        raise RuntimeError(
            "IntentSpec approval was requested, but stdin is not a TTY. "
            "Pass an explicit approval_callback for non-interactive runs."
        )

    answer = input("Approve this intent? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _make_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid4().hex[:8]}"


def _cleanup_hitl_session(session_id: str, mgmt_ips: dict) -> None:
    """
    Best-effort cleanup for failed or aborted runs.
    """
    try:
        delete_checkpoint(session_id, mgmt_ips)
    except Exception as e:
        print(f"[run_pipeline] WARNING: checkpoint cleanup failed: {e}")

    try:
        cleanup_session(session_id)
    except Exception as e:
        print(f"[run_pipeline] WARNING: session cleanup failed: {e}")


def run_pipeline(
    query: str,
    project_id: str,
    mgmt_ips: dict | None = None,
    inventory_str: str | None = None,
    exclude_topology: str | None = None,
    review_callback=None,
    approval_callback=None,
    max_rounds: int | None = None,
) -> dict:
    """
    Args:
      query          : natural language config request
      project_id     : GNS3_PROJECT_ID - resolves mgmt_ips from mgmt_ips.json
      mgmt_ips       : optional override - if omitted, resolved from project_id
      inventory_str  : optional override - if omitted, built from mgmt_ips
      review_callback: optional HITL callback used by orchestrator.run_plan()
      max_rounds     : optional retry cap for the HITL loop

    Returns:
      Final PipelineState dict after the operator accepts the change.
    """
    print(f"\n{'=' * 60}")
    print("  SONiC HITL Pipeline")
    print(f"  Query: {query}")
    print(f"{'=' * 60}\n")

    if mgmt_ips is None:
        mgmt_ips, _ = _load_mgmt_ips_for_project(project_id)

    if inventory_str is None:
        inventory_str = _build_inventory_str(mgmt_ips)

    print("[setup] Building topology string...")
    topology_str = build_topology(mgmt_ips, fetch_state=False)

    print("[setup] Loading metadata overview...")
    if exclude_topology:
        metadata_overview = load_metadata_overview_excluding(METADATA_ROOT, exclude_topology)
    else:
        metadata_overview = load_metadata_overview(METADATA_ROOT)

    retriever = get_retriever()
    selected_example = retriever.select(query, topology_str, mgmt_ips, metadata_overview) or {}

    if max_rounds is None:
        max_rounds = MAX_RETRY_ROUNDS

    # Extract + freeze the IntentSpec before touching the network.
    # This stage verifies only that the user's request was understood correctly.
    # No snapshots or checkpoints are required.
    intent_spec = None
    if VERIFICATION_ENABLED:
        print("[setup] Extracting IntentSpec...")
        intent_spec = IntentSpecAgent(set(mgmt_ips.keys())).extract(query)

        if intent_spec is not None:
            approve = approval_callback or _default_approval_callback
            if not approve(intent_spec):
                print("[run_pipeline] Operator cancelled at IntentSpec approval.")
                return {
                    "status": "cancelled",
                    "reason": "intent_spec_rejected",
                }

    print("[setup] Creating HITL baseline...")
    session_id = _make_session_id()
    hitl_session = create_baseline(session_id, mgmt_ips)

    baseline_dir = hitl_session["actual_t1_dir"]

    print("[setup] Running PlannerAgent...")
    examples = _load_examples_from_selection(selected_example)
    planner_example = {"ansible": selected_example.get("ansible")}
    planner = PlannerAgent(topology_str, mgmt_ips, planner_example)
    plan = planner.plan(query)

    print("\n" + "=" * 70)
    print(f"FULL PLAN - category: {plan.get('query_category')} | {len(plan['steps'])} steps")
    print("=" * 70)
    for step in plan["steps"]:
        print(
            f"  [{step['step_id']}] {step['intent']:<18} "
            f"targets={step['targets']} depends_on={step['depends_on']}"
        )
        print(f"       data_output: {step.get('data_output')}")
        print(f"       reason: {step['reason']}")
    print("=" * 70 + "\n")

    initial_state: PipelineState = {
        "query": query,
        "mgmt_ips": mgmt_ips,
        "inventory_str": inventory_str,
        "project_id": project_id,
        "session_id": session_id,
        "topology_str": topology_str,
        "examples": examples,
        "hitl_session": hitl_session,
        "round_number": 1,
        "intent_spec": intent_spec,
    }

    try:
        final_state = run_plan(
            plan,
            initial_state,
            review_callback=review_callback,
            max_rounds=max_rounds,
        )
    except Exception:
        _cleanup_hitl_session(session_id, mgmt_ips)
        raise

    return final_state


if __name__ == "__main__":
    from sonic.gns3.api_client import GNS3_PROJECT_ID

    result = run_pipeline(
        query="create vlan 100 on all switches",
        project_id=GNS3_PROJECT_ID,
    )
    print("\nFINAL STATE KEYS:", list(result.keys()))
    print("Ansible success:", (result.get("ansible_result") or {}).get("success"))
    print("Human review accepted:", (result.get("human_review") or {}).get("accepted"))