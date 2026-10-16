# sonic/bench_net/eval_harness.py
"""
Benchmark evaluation harness. Assumes:
  - The topology for each dataset item's `topology` field is already     running in GNS3 (not created/torn down here).
  - Switches are reachable via mgmt_ips (same 3-switch pattern as today).

For each dataset item:
  1. Seed switches to the item's init_state (push saved config_db/FRR).
  2. Capture "before" state (should match init_state, used as diff baseline).
  3. Run the pipeline under test on the item's query.
  4. Capture "after" state.
  5. Compute actual_diff via extract_facts(before, after).
  6. Compare actual_diff against the dataset item's stored expected_diff.
  7. Record pass/fail + a granular match score.
"""
import json
from pathlib import Path
from dataclasses import dataclass, asdict

from sonic.hitl.facts import extract_facts, facts_to_dicts, Fact
from sonic.rollback.capture import capture
from sonic.orchestrator.agentic_orchestrator import run_agentic_hitl_pipeline
from sonic.svc.eval import _auto_accept_gate1, _auto_accept_gate2

DATASET_DIR = Path("sonic/bench_net/dataset")
INIT_STATES_DIR = Path("sonic/bench_net/init_states")


def _fact_key(f: dict) -> tuple:
    """Comparable key for a fact dict, ignoring nothing — exact match per
    the project's stated evaluation philosophy (same names/numbers, no
    partial credit at the fact-identity level)."""
    return (f["id"], f["table"], f["node"], f["change"], json.dumps(f["detail"], sort_keys=True))


def seed_switches(mgmt_ips: dict, init_state_name: str):
    """
    Push the init_state's saved config_db.json + FRR config onto each
    switch, replacing current state.

    ** UNVERIFIED **: `config reload -y` is assumed to fully replace
    prior VLAN/BGP/interface state rather than merge with it. This has
    NOT been empirically confirmed. Before trusting this harness's
    results, test in isolation: configure something on a switch, seed
    a different known-clean config_db.json via this function, then
    check `show vlan brief` / `show ip bgp summary` — if residual state
    from before the seed remains, this function is unsafe to use as-is
    and needs a different SONiC config-replace mechanism.
    """
    import paramiko
    from sonic.switch.ssh_client import run_command
    from sonic.config.settings import SSH_USERNAME, SSH_PASSWORD

    init_dir = INIT_STATES_DIR / init_state_name / "raw"

    for name, ip in mgmt_ips.items():
        config_path = init_dir / f"{name}.config_db.json"
        frr_path = init_dir / f"{name}.frr.txt"
        if not config_path.exists():
            raise FileNotFoundError(f"No saved config_db.json for {name} in {init_dir}")

        print(f"[seed] {name} ({ip}): pushing config_db.json...")
        transport = paramiko.Transport((ip, 22))
        transport.connect(username=SSH_USERNAME, password=SSH_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.put(str(config_path), "/tmp/config_db_seed.json")
        sftp.close()
        transport.close()

        run_command(ip, "sudo cp /tmp/config_db_seed.json /etc/sonic/config_db.json")
        print(f"[seed] {name}: reloading config (config reload -y)...")
        success, out, err = run_command(ip, "sudo config reload -y")
        if not success:
            print(f"[seed] WARNING: config reload failed for {name}: {err or out}")

    print("[seed] Done. NOTE: FRR/BGP restore not yet implemented in this function — "
          "vlan-only dataset items are safe, bgp items are NOT yet properly seeded.")


def score_item(item: dict, actual_facts: list[dict]) -> dict:
    expected_keys = {_fact_key(f) for f in item["expected_diff"]}
    actual_keys = {_fact_key(f) for f in actual_facts}

    matched = expected_keys & actual_keys
    missing = expected_keys - actual_keys      # expected but not produced
    unexpected = actual_keys - expected_keys    # produced but not expected

    exact_match = (not missing) and (not unexpected)

    return {
        "exact_match": exact_match,
        "expected_count": len(expected_keys),
        "actual_count": len(actual_keys),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "partial_score": len(matched) / len(expected_keys) if expected_keys else None,
    }


def run_item(item: dict, mgmt_ips: dict) -> dict:
    item_id = item["id"]
    print(f"\n{'='*60}\nRunning {item_id}: {item['query']!r}\n{'='*60}")

    print(f"[{item_id}] seeding switches to init_state={item['init_state']}...")
    seed_switches(mgmt_ips, item["init_state"])

    before_dir = Path(f"/tmp/eval_{item_id}_before")
    capture(before_dir, mgmt_ips)

    try:
        final_state = run_agentic_hitl_pipeline(
            query=item["query"],
            project_id=None,  # not used when mgmt_ips is passed explicitly
            mgmt_ips=mgmt_ips,
            gate1_callback=_auto_accept_gate1,
            gate2_callback=_auto_accept_gate2,
        )
    except Exception as e:
        return {"id": item_id, "verdict": "exception", "error": str(e)}

    status = final_state.get("status")
    if status != "complete":
        return {"id": item_id, "verdict": "pipeline_incomplete", "status": status}

    after_dir = Path(f"/tmp/eval_{item_id}_after")
    capture(after_dir, mgmt_ips)

    facts = extract_facts(before_dir, after_dir)
    actual_diff = facts_to_dicts(facts)

    result = score_item(item, actual_diff)
    result["id"] = item_id
    result["verdict"] = "pass" if result["exact_match"] else "fail"
    result["actual_diff"] = actual_diff
    return result


def run_all():
    results = []
    mgmt_ips = {"SONiC-VS-1": "192.168.40.2", "SONiC-VS-2": "192.168.40.3", "SONiC-VS-3": "192.168.40.4"}

    for item_path in sorted(DATASET_DIR.glob("*.json")):
        item = json.loads(item_path.read_text())
        result = run_item(item, mgmt_ips)
        results.append(result)
        print(f"[{item['id']}] verdict={result['verdict']}")

    out_path = Path("sonic/bench_net/results.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote results to {out_path}")

    passed = sum(1 for r in results if r.get("verdict") == "pass")
    print(f"\n{passed}/{len(results)} passed")


if __name__ == "__main__":
    run_all()