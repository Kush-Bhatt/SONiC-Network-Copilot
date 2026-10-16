"""
python -m sonic.bench_net.generate.capture_init_state --name <init_state_name>

Captures current live switch state (config_db.json + frr.txt, raw and
Batfish-translated) to sonic/bench_net/init_states/<name>/. Run once
per distinct starting state a dataset item needs (may be reused across
multiple items sharing the same starting point).
"""
import argparse
from pathlib import Path
from sonic.bench_net.generate.capture import capture

from sonic.bench_net.config import mgmt_ips

parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True, help="init_state name, e.g. ans-gns-bat-t4-1")
args = parser.parse_args()

dst = Path(f"sonic/bench_net/init_states/{args.name}")
capture(dst, mgmt_ips)
print(f"Captured init state '{args.name}' to {dst}")
