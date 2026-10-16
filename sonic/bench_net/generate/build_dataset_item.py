"""
python -m sonic.bench_net.generate.build_dataset_item --id q02 \
    --query "create vlan 324 on all switches, no SVI needed" \
    --category vlan --init-state ans-gns-bat-t4-1

Diffs the already-applied live state against the saved init_state
(NOT a fresh "before" capture — init_state IS the before), via
Batfish facts, and writes dataset/<id>.json. Assumes apply_ansible.py
already ran successfully against this init_state.
"""
import argparse
import json
from pathlib import Path

from sonic.bench_net.generate.facts import extract_facts, facts_to_dicts
from sonic.bench_net.generate.capture import capture

from sonic.bench_net.config import mgmt_ips, TOPOLOGY_NAME

PLAYBOOK_PATH = Path("sonic/bench_net/generate/playbook.yaml")

parser = argparse.ArgumentParser()
parser.add_argument("--id", required=True)
parser.add_argument("--query", required=True)
parser.add_argument("--category", required=True)
parser.add_argument("--init-state", required=True, dest="init_state")
args = parser.parse_args()

before_dir = Path(f"sonic/bench_net/init_states/{args.init_state}")
if not before_dir.exists():
    raise FileNotFoundError(
        f"init_state '{args.init_state}' not found at {before_dir} — "
        f"run capture_init_state.py --name {args.init_state} first."
    )

after_dir = Path(f"/tmp/{args.id}_after")
print(f"[build_dataset_item] Capturing current (post-apply) state...")
capture(after_dir, mgmt_ips)

facts = extract_facts(before_dir, after_dir)
expected_diff = facts_to_dicts(facts)

item = {
    "id": args.id,
    "query": args.query,
    "category": args.category,
    "topology": TOPOLOGY_NAME,
    "init_state": args.init_state,
    "reference_playbook": PLAYBOOK_PATH.read_text(),
    "expected_diff": expected_diff,
}

out_path = Path(f"sonic/bench_net/dataset/{args.id}.json")
out_path.write_text(json.dumps(item, indent=2))
print(f"Wrote {out_path} with {len(expected_diff)} expected fact(s)")
