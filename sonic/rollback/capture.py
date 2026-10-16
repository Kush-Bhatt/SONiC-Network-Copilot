"""
sonic/rollback/capture.py
--------------------------
Snapshot capture helper shared by create_baseline()/verify_rollback() in
sonic/rollback/core.py. Writes both tiers sonic.hitl.facts needs:

  <dst_dir>/raw/<name>.config_db.json + <name>.frr.txt   (extract_raw_diff)
  <dst_dir>/configs/<name>.cfg                           (extract_facts,
                                                           via Batfish)

Reuses translate_to_batfish_cfg() from snapshot_builder.py rather than
duplicating that translation logic.
"""
import json
import shutil
from pathlib import Path

from sonic.switch.ssh_client import get_frr_config, get_sonic_config,  run_command
from sonic.switch.snapshot_builder import translate_to_batfish_cfg


def capture(dst_dir: Path, mgmt_ips: dict):
    raw_dir = dst_dir / "raw"
    cfg_dir = dst_dir / "configs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    for name, ip in sorted(mgmt_ips.items()):
        print(f"[rollback] {name} ({ip}): persisting live config to disk before capture...")
        success, out, err = run_command(ip, "sudo config save -y")
        if not success:
            print(f"[rollback] WARNING: config save failed for {name}: {err or out}")
            
        print(f"[rollback] {name} ({ip}): capturing config_db + FRR via fresh SSH...")
        raw_json_str = get_sonic_config(ip, hostname=name)
        (raw_dir / f"{name}.config_db.json").write_text(raw_json_str, encoding="utf-8")

        frr_cfg = get_frr_config(ip)
        (raw_dir / f"{name}.frr.txt").write_text(frr_cfg, encoding="utf-8")

        config_dict = json.loads(raw_json_str)
        cfg_text = translate_to_batfish_cfg(config_dict, hostname=name, frr_config=frr_cfg)
        (cfg_dir / f"{name}.cfg").write_text(cfg_text, encoding="utf-8")

    print(f"[rollback] Captured {len(mgmt_ips)} switches -> {dst_dir}")


def delete_dir(dst_dir: Path):
    if dst_dir.exists():
        shutil.rmtree(dst_dir)


def capture_full(dst_dir: Path, mgmt_ips: dict):
    """Backward-compatible alias used by rollback.__init__."""
    return capture(dst_dir, mgmt_ips)