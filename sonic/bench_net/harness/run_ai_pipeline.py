"""
python -m sonic.bench_net.harness.run_ai_pipeline               # VLAN-E-01 -> VLAN-M-01 (test run)
python -m sonic.bench_net.harness.run_ai_pipeline --all         # all 11 Chain A items
python -m sonic.bench_net.harness.run_ai_pipeline --no-restore  # use the lab as it is now

Unattended run of the AI pipeline over Chain A:
  1. preflight: LM Studio, GNS3 and Batfish must be up
  2. restore the GNS3 snapshot "S0 snapshot", start the nodes, and wait until
     every switch answers and its captured state matches init_states/S0
  3. for each item, in chain order: init-state check, AI pipeline, capture,
     Batfish diff, score against expected_diff (run_eval.run_item)
Results go to harness/tmp/eval_<timestamp>/results.json, rewritten after each item.
"""
import argparse
import json
import time
from datetime import datetime

import requests

from sonic.bench_net.config import GNS3_BASE_URL, mgmt_ips
from sonic.bench_net.generate.capture import capture
from sonic.bench_net.generate.gns3_client import AUTH, start_all_nodes
from sonic.bench_net.harness.capture_init import (
    CAPTURE_DIR,
    CHAIN_A_ORDER,
    DATASET_DIR,
    INIT_STATES_DIR,
    compare,
)
from sonic.bench_net.harness.run_eval import check_same_switches, run_item
from sonic.config.settings import GNS3_PROJECT_ID, LMSTUDIO_MODEL
from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project
from sonic.svc.preflight import check_switches, run_preflight

# Stage 1 of Chain A — enough to test the pipeline end to end on VLAN tasks.
TEST_ITEMS = CHAIN_A_ORDER[: CHAIN_A_ORDER.index("VLAN-M-01") + 1]

S0_SNAPSHOT = "S0 snapshot"
BOOT_TIMEOUT_SECONDS = 900  # SONiC-VS needs several minutes after a restore
S0_CHECK_ATTEMPTS = 6  # bgp/swss keep starting after SSH already answers
POLL_SECONDS = 30


def restore_s0() -> None:
    """Restore the S0 GNS3 snapshot and wait until the live state matches init_states/S0."""
    base = f"{GNS3_BASE_URL}/projects/{GNS3_PROJECT_ID}"
    snapshots = requests.get(f"{base}/snapshots", auth=AUTH, timeout=30).json()
    snap = next((s for s in snapshots if s["name"] == S0_SNAPSHOT), None)
    if snap is None:
        raise RuntimeError(f"No GNS3 snapshot named {S0_SNAPSHOT!r} in project {GNS3_PROJECT_ID}")

    print(f"[restore] restoring GNS3 snapshot {S0_SNAPSHOT!r}...")
    requests.post(
        f"{base}/snapshots/{snap['snapshot_id']}/restore", auth=AUTH, timeout=600
    ).raise_for_status()
    start_all_nodes(GNS3_PROJECT_ID)

    pipeline_ips, _ = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
    deadline = time.time() + BOOT_TIMEOUT_SECONDS
    while True:
        ok, detail = check_switches(pipeline_ips)
        if ok:
            break
        if time.time() > deadline:
            raise RuntimeError(f"Switches unreachable {BOOT_TIMEOUT_SECONDS}s after restore: {detail}")
        print(f"[restore] waiting for switches: {detail}")
        time.sleep(POLL_SECONDS)

    check_dir = CAPTURE_DIR / "restore_check"
    diff = None
    for attempt in range(1, S0_CHECK_ATTEMPTS + 1):
        time.sleep(POLL_SECONDS)
        try:
            capture(check_dir, mgmt_ips)
            diff = compare(INIT_STATES_DIR / "S0", check_dir)
        except Exception as e:  # noqa: BLE001 - a half-booted switch fails capture; retry
            diff = f"capture failed: {e}"
        if not diff:
            print(f"[restore] live state matches S0 (check {attempt})")
            return
        print(f"[restore] not at S0 yet (check {attempt}/{S0_CHECK_ATTEMPTS})")
    raise RuntimeError(f"Lab does not match S0 after restore:\n{json.dumps(diff, indent=2, default=str)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Run all 11 Chain A items, not just VLAN-E-01..VLAN-M-01.")
    parser.add_argument("--no-restore", action="store_true", help=f"Skip restoring {S0_SNAPSHOT!r}.")
    args = parser.parse_args()
    item_ids = CHAIN_A_ORDER if args.all else TEST_ITEMS

    if not run_preflight(GNS3_PROJECT_ID)["ok"]:
        raise SystemExit("Preflight failed - fix the [FAIL] items above and rerun.")
    check_same_switches()
    if not args.no_restore:
        restore_s0()

    run_dir = CAPTURE_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True)
    print(f"[run] model={LMSTUDIO_MODEL} items={item_ids} -> {run_dir}")

    results = []
    for item_id in item_ids:
        item = json.loads((DATASET_DIR / f"{item_id}.json").read_text())
        results.append(run_item(item, run_dir))
        (run_dir / "results.json").write_text(
            json.dumps({"model": LMSTUDIO_MODEL, "results": results}, indent=2, default=str)
        )

    print(f"\n{'ID':<11} {'INIT':<6} {'PIPELINE':<16} {'EXACT':<6} MATCHED")
    for r in results:
        s = r["score"]
        print(
            f"{r['id']:<11} {r['init_match']!s:<6} {r['pipeline']['status']!s:<16} "
            f"{s['exact_match']!s:<6} {len(s['matched'])}/{s['expected_count']}"
        )
    passed = sum(1 for r in results if r["score"]["exact_match"])
    print(f"\n{passed}/{len(results)} exact matches. Results: {run_dir / 'results.json'}")


if __name__ == "__main__":
    main()
