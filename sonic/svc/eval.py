"""
sonic/svc/eval.py
-----------------
Stable benchmark-facing wrapper over run_agentic_hitl_pipeline().

Auto-accepts both HITL gates (Gate #1 IR review, Gate #2 diff review) so
benchmark runs proceed unattended. Returns a fixed schema so benchmark
callers stay insulated from AgenticPipelineState internals.
"""

from __future__ import annotations

import time
import traceback

from sonic.orchestrator.agentic_orchestrator import run_agentic_hitl_pipeline
from sonic.svc.preflight import run_preflight


def preflight_or_abort(project_id: str, mgmt_ips: dict) -> None:
    """Call ONCE before a benchmark run starts (not per-query)."""
    result = run_preflight(project_id=project_id, mgmt_ips=mgmt_ips)
    if not result["ok"]:
        failed = [name for name, c in result["checks"].items() if c and not c["ok"]]
        raise RuntimeError(
            f"Preflight failed before benchmark run - aborting. "
            f"Failed checks: {failed}. See output above for details."
        )


def _auto_accept_gate1(result: dict) -> bool:
    return True


def _auto_accept_gate2(round_number: int, bundle) -> dict:
    return {"accepted": True, "feedback": ""}


def run_eval_query(
    query: str,
    project_id: str,
    exclude_topology: str | None = None,  # kept for call-site compat; unused by the new pipeline
) -> dict:
    """
    Returns a fixed schema:
      {
        "ansible": {...} | None,
        "gate2_accepted": bool | None,
        "gate2_feedback": str | None,
        "round_number": int | None,
        "diff_human": str | None,
        "diff_machine": dict | None,
        "verdict": "pass" | "fail" | "exception",
        "latency_s": float,
        "exception": str | None,
        "raw_state": dict | None,
      }
    """
    t_start = time.time()

    try:
        final_state = run_agentic_hitl_pipeline(
            query=query,
            project_id=project_id,
            gate1_callback=_auto_accept_gate1,
            gate2_callback=_auto_accept_gate2,
        )
    except Exception as e:
        return {
            "ansible": None,
            "gate2_accepted": None,
            "gate2_feedback": None,
            "round_number": None,
            "diff_human": None,
            "diff_machine": None,
            "verdict": "exception",
            "latency_s": round(time.time() - t_start, 1),
            "exception": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            "raw_state": None,
        }

    ans = final_state.get("ansible_result")
    gate2_accepted = final_state.get("gate2_accepted")
    bundle = final_state.get("diff_bundle")

    ansible_ok = bool(ans and ans.get("success"))
    verdict = "pass" if (ansible_ok and gate2_accepted) else "fail"

    return {
        "ansible": ans,
        "gate2_accepted": gate2_accepted,
        "gate2_feedback": final_state.get("gate2_feedback"),
        "round_number": final_state.get("round_number"),
        "diff_human": getattr(bundle, "human_diff", None),
        "diff_machine": getattr(bundle, "machine_diff", None),
        "verdict": verdict,
        "latency_s": round(time.time() - t_start, 1),
        "exception": None,
        "raw_state": final_state,
    }


if __name__ == "__main__":
    from sonic.gns3.api_client import GNS3_PROJECT_ID

    result = run_eval_query(
        query="create vlan 100 on all switches",
        project_id=GNS3_PROJECT_ID,
    )
    print("\nVERDICT:", result["verdict"])
    print("LATENCY:", result["latency_s"], "s")
    print("ANSIBLE SUCCESS:", (result["ansible"] or {}).get("success"))
    print("GATE2 ACCEPTED:", result["gate2_accepted"])