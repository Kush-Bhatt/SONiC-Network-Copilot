"""
python -m sonic.bench_net.harness.run_eval                       # all 11 Chain A items
python -m sonic.bench_net.harness.run_eval --items VLAN-E-01     # just these, in chain order

Runs the AI pipeline over Chain A as one continuous chain, starting from S0:
the AI's result on one item is the starting state of the next. Per item:
  1. capture live state; record whether it matches the item's saved init_state
  2. give the item's query (plus the device/port label map) to the AI pipeline,
     with both HITL gates auto-accepted
  3. wait for the switches to settle
  4. capture again; actual diff = Batfish facts(before, after)
  5. score the actual diff against the item's expected_diff
Nothing is restored between items, so a wrong AI result carries into the
next item; step 1's init_match flag shows where that happened.
"""
import argparse
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from sonic.bench_net.config import QUERY_LABELS, mgmt_ips
from sonic.bench_net.generate.capture import capture
from sonic.bench_net.generate.facts import extract_facts, facts_to_dicts
from sonic.bench_net.harness.capture_init import (
    CAPTURE_DIR,
    CHAIN_A_ORDER,
    DATASET_DIR,
    INIT_STATES_DIR,
    compare,
    manual_ignores,
)
from sonic.config.settings import GNS3_PROJECT_ID, LMSTUDIO_MODEL
from sonic.orchestrator.agentic_orchestrator import run_agentic_hitl_pipeline
from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project
from sonic.svc.eval import _auto_accept_gate1, _auto_accept_gate2

# The Batfish diff is computed from saved configs, not live protocol state, so
# this only has to cover FRR applying the config after `config save`.
SETTLE_SECONDS = 30


def check_same_switches() -> None:
    """bench_net/config.py and the pipeline's mgmt_ips.json name the switches
    differently (SONiC-VS-1 vs SonicVS-1) but must point at the same IPs."""
    pipeline_ips, _ = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
    if sorted(mgmt_ips.values()) != sorted(pipeline_ips.values()):
        raise RuntimeError(
            f"Switch IPs differ: bench_net/config.py {sorted(mgmt_ips.values())} vs "
            f"sonic/config/mgmt_ips.json {sorted(pipeline_ips.values())}"
        )


def build_query(item: dict) -> str:
    labels = ", ".join(f"{label} = {name}" for label, name in QUERY_LABELS.items())
    return f"{item['query']}\n\nDevice and port names in this lab: {labels}."


def score(expected: list[dict], actual: list[dict]) -> dict:
    """Facts match on (id, change); a matched fact whose detail differs is a detail_mismatch."""
    expected_by_key = {(f["id"], f["change"]): f for f in expected}
    actual_by_key = {(f["id"], f["change"]): f for f in actual}
    matched = sorted(set(expected_by_key) & set(actual_by_key))
    missing = sorted(set(expected_by_key) - set(actual_by_key))
    unexpected = sorted(set(actual_by_key) - set(expected_by_key))
    detail_mismatch = [k for k in matched if expected_by_key[k]["detail"] != actual_by_key[k]["detail"]]
    return {
        "exact_match": not missing and not unexpected and not detail_mismatch,
        "expected_count": len(expected_by_key),
        "actual_count": len(actual_by_key),
        "matched": [list(k) for k in matched],
        "missing": [list(k) for k in missing],
        "unexpected": [list(k) for k in unexpected],
        "detail_mismatch": [list(k) for k in detail_mismatch],
    }


def run_pipeline(query: str) -> dict:
    started = time.time()
    try:
        state = run_agentic_hitl_pipeline(
            query=query,
            project_id=GNS3_PROJECT_ID,
            gate1_callback=_auto_accept_gate1,
            gate2_callback=_auto_accept_gate2,
        )
    except Exception as e:
        return {
            "status": "exception",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
            "latency_s": round(time.time() - started, 1),
        }
    return {
        "status": state.get("status"),
        "stage": state.get("stage"),
        "error": state.get("error"),
        "rounds": state.get("round_number"),
        "ansible_success": (state.get("ansible_result") or {}).get("success"),
        "combined_ir": state.get("combined_ir"),
        "combined_notes": state.get("combined_notes"),
        "playbook_yaml": state.get("playbook_yaml"),
        "latency_s": round(time.time() - started, 1),
    }


def run_item(item: dict, run_dir: Path) -> dict:
    item_id = item["id"]
    item_dir = run_dir / item_id
    print(f"\n{'=' * 60}\n{item_id}: {item['query']}\n{'=' * 60}")

    before_dir = item_dir / "before"
    capture(before_dir, mgmt_ips)
    init_diff = compare(INIT_STATES_DIR / item["init_state"], before_dir, manual_ignores(item_id))
    print(f"[{item_id}] init_state match: {not init_diff}")

    query = build_query(item)
    pipeline = run_pipeline(query)
    print(f"[{item_id}] pipeline status={pipeline['status']} error={pipeline.get('error')}")

    time.sleep(SETTLE_SECONDS)
    after_dir = item_dir / "after"
    capture(after_dir, mgmt_ips)
    actual_diff = facts_to_dicts(extract_facts(before_dir, after_dir))

    result = score(item["expected_diff"], actual_diff)
    print(
        f"[{item_id}] exact_match={result['exact_match']} matched={len(result['matched'])}/"
        f"{result['expected_count']} missing={len(result['missing'])} "
        f"unexpected={len(result['unexpected'])} detail_mismatch={len(result['detail_mismatch'])}"
    )
    return {
        "id": item_id,
        "query": query,
        "init_match": not init_diff,
        "init_diff": init_diff,
        "pipeline": pipeline,
        "score": result,
        "actual_diff": actual_diff,
    }


def run_all(item_ids: list[str]) -> None:
    check_same_switches()
    run_dir = CAPTURE_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True)
    print(f"[run_eval] model={LMSTUDIO_MODEL} items={item_ids} -> {run_dir}")

    results = []
    for item_id in item_ids:
        item = json.loads((DATASET_DIR / f"{item_id}.json").read_text())
        results.append(run_item(item, run_dir))
        # Rewritten after every item so a long run that dies midway keeps its results.
        (run_dir / "results.json").write_text(
            json.dumps({"model": LMSTUDIO_MODEL, "results": results}, indent=2, default=str)
        )

    print(f"\n{'ID':<11} {'INIT':<6} {'PIPELINE':<16} {'EXACT':<6} MATCHED")
    for r in results:
        s = r["score"]
        print(
            f"{r['id']:<11} {str(r['init_match']):<6} {str(r['pipeline']['status']):<16} "
            f"{str(s['exact_match']):<6} {len(s['matched'])}/{s['expected_count']}"
        )
    passed = sum(1 for r in results if r["score"]["exact_match"])
    print(f"\n{passed}/{len(results)} exact matches. Results: {run_dir / 'results.json'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", nargs="+", default=CHAIN_A_ORDER, help="Item ids; run in chain order.")
    args = parser.parse_args()
    unknown = set(args.items) - set(CHAIN_A_ORDER)
    if unknown:
        parser.error(f"not Chain A items: {sorted(unknown)}")
    run_all([i for i in CHAIN_A_ORDER if i in args.items])


if __name__ == "__main__":
    main()
