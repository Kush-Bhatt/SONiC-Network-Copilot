"""
sonic/bench_net/generate/write_hosts_ini.py
---------------------------------------------
One-time-per-topology: writes topologies/<TOPOLOGY_NAME>/hosts.ini
from config.py's mgmt_ips. Re-run only if mgmt_ips changes for this
topology (new switch added, IP changed) — not per dataset item.
"""
from pathlib import Path
from sonic.bench_net.config import SSH_USERNAME, SSH_PASSWORD
from sonic.bench_net.config import mgmt_ips, TOPOLOGY_NAME


def build_inventory_str(mgmt_ips: dict) -> str:
    lines = ["[sonic_switches]"] + [
        f"{sw} ansible_host={ip} ansible_user={SSH_USERNAME} "
        f"ansible_password={SSH_PASSWORD} ansible_connection=ssh ansible_network_os=sonic"
        for sw, ip in mgmt_ips.items()
    ]
    return "\n".join(lines) + "\n"


out_dir = Path(f"sonic/bench_net/topologies/{TOPOLOGY_NAME}")
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "hosts.ini"
out_path.write_text(build_inventory_str(mgmt_ips))
print(f"Wrote {out_path}")
print(out_path.read_text())

