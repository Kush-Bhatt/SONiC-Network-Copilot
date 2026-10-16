from sonic.bench_net.generate.gns3_client import close_project, open_project, export_project, start_all_nodes
from pathlib import Path
import time
import paramiko

from sonic.bench_net.config import PID, TOPOLOGY_NAME, TAP_IFACE, TAP_IP
from sonic.bench_net.config import GNS3_HOST, GNS3_JUMP_USER, GNS3_JUMP_PASS

out_dir = Path(f"sonic/bench_net/topologies/{TOPOLOGY_NAME}")
out_dir.mkdir(parents=True, exist_ok=True)

close_project(PID)
export_project(PID, str(out_dir / "topology.gns3project"))
open_project(PID)
print(f"Exported topology to {out_dir}")

# tap interfaces live on GNS3_HOST, not on whatever machine runs this
# script — restore them there over SSH, not via a local subprocess.
print(f"Restoring {TAP_IFACE} on {GNS3_HOST} (close/export/open cycle can reset it)...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(GNS3_HOST, username=GNS3_JUMP_USER, password=GNS3_JUMP_PASS)
client.exec_command(f"sudo ip link set {TAP_IFACE} up")
client.exec_command(f"sudo ip addr add {TAP_IP} dev {TAP_IFACE}")  # harmless if already present
client.close()

print("Restarting nodes...")
start_all_nodes(PID)
print("Waiting for nodes to boot (~2 min)...")
time.sleep(120)
print("Done — verify reachability before proceeding (e.g. ping each mgmt IP).")
