"""
sonic/bench_net/generate/capture.py
---------------------------------------
Standalone copy of sonic/rollback/capture.py's capture(). Writes both
tiers facts.py needs: raw/<name>.config_db.json + .frr.txt, and
configs/<name>.cfg (Batfish-translated).
"""
import json
from pathlib import Path

from sonic.bench_net.generate.ssh_client import get_frr_config, get_sonic_config, run_command
from sonic.bench_net.generate.translate import translate_to_batfish_cfg


def capture(dst_dir: Path, mgmt_ips: dict):
    raw_dir = dst_dir / "raw"
    cfg_dir = dst_dir / "configs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    for name, ip in sorted(mgmt_ips.items()):
        print(f"[capture] {name} ({ip}): persisting live config to disk before capture...")
        success, out, err = run_command(ip, "sudo config save -y")
        if not success:
            print(f"[capture] WARNING: config save failed for {name}: {err or out}")

        print(f"[capture] {name} ({ip}): capturing config_db + FRR via fresh SSH...")
        raw_json_str = get_sonic_config(ip, hostname=name)
        (raw_dir / f"{name}.config_db.json").write_text(raw_json_str, encoding="utf-8")

        frr_cfg = get_frr_config(ip)
        (raw_dir / f"{name}.frr.txt").write_text(frr_cfg, encoding="utf-8")

        config_dict = json.loads(raw_json_str)
        cfg_text = translate_to_batfish_cfg(config_dict, hostname=name, frr_config=frr_cfg)
        (cfg_dir / f"{name}.cfg").write_text(cfg_text, encoding="utf-8")

    print(f"[capture] Captured {len(mgmt_ips)} switches -> {dst_dir}")
