"""
sonic/svc/run.py
--------------------------
Interactive terminal entrypoint. Thin wrapper over run_pipeline() —
contains NO pipeline logic itself, only input collection (query loop)
and output presentation (verdict printing).

mgmt_ips/inventory_str resolution now happens INSIDE run_pipeline()
(via project_id) — this file no longer duplicates that logic.

Usage:
    python -m sonic.entrypoints.run
"""
import traceback

from sonic.gns3.api_client import GNS3_PROJECT_ID
from sonic.orchestrator.agentic_orchestrator import run_agentic_hitl_pipeline
from sonic.svc.preflight import run_preflight
from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project

def print_verdict(final_state: dict):
    status = final_state.get("status")

    if status == "cancelled":
        print("\n" + "=" * 70)
        print("CANCELLED at Gate #1 (IR review) — nothing was applied.")
        print("=" * 70)
        return

    if status == "pipeline_failed":
        print("\n" + "=" * 70)
        print(f"PIPELINE FAILED at stage={final_state.get('stage')}")
        print(f"  error: {final_state.get('error')}")
        print("=" * 70)
        return

    if status == "max_rounds_reached":
        print("\n" + "=" * 70)
        print(f"MAX ROUNDS REACHED (round {final_state.get('round_number')}) — no approval given.")
        print("=" * 70)
        return

    ans = final_state.get("ansible_result") or {}
    print("\n" + "=" * 70)
    print("HITL SUMMARY")
    print("=" * 70)
    print(f"  rounds executed      : {final_state.get('round_number')}")
    print(f"  ansible_apply success: {ans.get('success')}")
    print(f"  gate2 accepted       : {final_state.get('gate2_accepted')}")
    if final_state.get("gate2_feedback"):
        print(f"  last feedback        : {final_state.get('gate2_feedback')}")
    print("=" * 70)


def main():
    print("=" * 70)
    print("Agentic-DAO-LAB — Interactive Pipeline Runner")
    print("=" * 70)

    if not GNS3_PROJECT_ID:
        print("ERROR: GNS3_PROJECT_ID is not set in .env — cannot continue.")
        return

    print(f"GNS3_PROJECT_ID: {GNS3_PROJECT_ID}")

    # Pre Checking System - preflight
    mgmt_ips, topology_name = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
    print(f"Topology: {topology_name}")

    preflight = run_preflight(project_id=GNS3_PROJECT_ID, mgmt_ips=mgmt_ips)
    if not preflight["ok"]:
        proceed = input("\nSome preflight checks failed. Continue anyway? (y/N): ").strip().lower()
        if proceed != "y":
            print("Aborting.")
            return

    while True:
        query = input("\nEnter query (or 'quit' to exit): ").strip()
        if query.lower() in ("quit", "exit", "q"):
            break
        if not query:
            continue

        try:
            final_state = run_agentic_hitl_pipeline(
                query=query,
                project_id=GNS3_PROJECT_ID,
            )
        except Exception as e:
            print(f"\nPIPELINE FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()
            continue

        print_verdict(final_state)

    print("\nGoodbye.")


if __name__ == "__main__":
    main()