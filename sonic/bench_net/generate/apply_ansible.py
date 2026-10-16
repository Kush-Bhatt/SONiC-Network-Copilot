"""
python -m sonic.bench_net.generate.apply_ansible

Applies sonic/bench_net/generate/playbook.yaml to the current live
topology using the topology's saved hosts.ini. Run this AFTER
capture_init_state.py (so there's a known starting point on disk to
diff against later) and BEFORE build_dataset_item.py.
"""
import sys
from pathlib import Path

from sonic.bench_net.generate.apply import apply_playbook
from sonic.bench_net.config import TOPOLOGY_NAME

PLAYBOOK_PATH = Path("sonic/bench_net/generate/playbook.yaml")

inventory_path = Path(f"sonic/bench_net/topologies/{TOPOLOGY_NAME}/hosts.ini")
if not inventory_path.exists():
    sys.exit(f"No hosts.ini for topology {TOPOLOGY_NAME} — run write_hosts_ini.py once first.")

playbook_yaml = PLAYBOOK_PATH.read_text()
if playbook_yaml.strip() in ("", "---"):
    sys.exit(f"{PLAYBOOK_PATH} looks empty — paste the playbook before running this.")

result = apply_playbook(playbook_yaml, inventory_path.read_text())

if not result["success"]:
    print(f"\n[apply_ansible] FAILED")
    sys.exit(1)

print(f"\n[apply_ansible] SUCCESS")
