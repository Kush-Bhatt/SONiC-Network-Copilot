"""
python -m sonic.bench_net.harness.capture_init

Walks the 11 Chain A items starting from S0. For each item: captures the
live state of every switch, checks it against the item's saved init_state,
then applies the item's reference playbook to reach the next init_state.
The switches end at C0.
"""
import json
from pathlib import Path

from sonic.bench_net.config import SSH_PASSWORD, SSH_USERNAME, mgmt_ips
from sonic.bench_net.generate.apply import apply_playbook
from sonic.bench_net.generate.capture import capture

DATASET_DIR = Path("sonic/bench_net/dataset")
INIT_STATES_DIR = Path("sonic/bench_net/init_states")
CAPTURE_DIR = Path("sonic/bench_net/harness/tmp")

# Chain A order from the task sheet (§4): each item's init_state is the state
# left by all the items before it.
CHAIN_A_ORDER = [
    "VLAN-E-01", "VLAN-E-02", "VLAN-E-03", "VLAN-M-01",
    "BGP-E-01", "BGP-E-02", "BGP-M-02", "BGP-M-03",
    "BGP-E-03", "BGP-E-04", "BGP-M-04",
]

# Fields left out of the comparison, per table. During dataset creation
# SONiC-VS-1's PORT|Ethernet0 changed from mtu 9100 / speed 40000 to
# mtu 1500 / speed 10 in a step that never touches it (VLAN-M-01), so every
# saved state from "->  VLAN-M-01" onward carries a change no playbook makes.
# No task sets mtu or speed, and Batfish never sees them, so ignoring them only
# affects this init check. admin_status stays compared (task sheet §2.2).
IGNORED_FIELDS = {"PORT": {"mtu", "speed"}}

# Changes made by hand during dataset creation, between two saved init_states,
# on switches the step's reference playbook never touches (mostly leaf-spine
# link addressing). No task makes them and no expected_diff contains them, so
# a live chain run can never reproduce them; they are left out of the init
# check for the items whose saved init_state carries them. The exact rows and
# lines are read from the saved states: whatever changed on `switch` across
# `step`. Each entry: (step, switch, part, first affected item, last affected item).
MANUAL_CHANGES = [
    ("VLAN-M-01", "SONiC-VS-1", "config_db", "BGP-E-01", "BGP-M-04"),  # Ethernet0/4 IPs
    # network 10.100.1.0/24 only; BGP-E-01's playbook removes it, BGP-E-03 adds it for real
    ("VLAN-M-01", "SONiC-VS-1", "frr", "BGP-E-01", "BGP-E-01"),
    ("VLAN-M-01", "SONiC-VS-5", "config_db", "BGP-E-01", "BGP-M-04"),  # Ethernet8 -> 10.1.1.1/31
    ("BGP-E-02", "SONiC-VS-1", "config_db", "BGP-M-02", "BGP-M-04"),   # Ethernet8 10.1.2.0/31
    ("BGP-E-02", "SONiC-VS-6", "config_db", "BGP-M-02", "BGP-M-04"),   # Ethernet8 -> 10.1.2.1/31
    ("BGP-M-03", "SONiC-VS-1", "frr", "BGP-E-03", "BGP-M-04"),         # 71 interface/ip lines
    ("BGP-M-03", "SONiC-VS-5", "frr", "BGP-E-03", "BGP-M-04"),         # factory neighbour 10.0.0.5
]


def _drop_ignored(table: str, rows: dict) -> dict:
    ignored = IGNORED_FIELDS.get(table)
    if not ignored:
        return rows
    return {k: {f: v for f, v in row.items() if f not in ignored} for k, row in rows.items()}


def diff_row(saved_row: dict, live_row: dict) -> dict:
    """Return {field: {"saved": old, "live": new}} for every field that differs (None = absent)."""
    return {
        field: {"saved": saved_row.get(field), "live": live_row.get(field)}
        for field in sorted(set(saved_row) | set(live_row))
        if saved_row.get(field) != live_row.get(field)
    }


def diff_config_db(saved: dict, live: dict) -> dict:
    """Return {table: {added, removed, changed}} for every table that differs."""
    result = {}
    for table in sorted(set(saved) | set(live)):
        saved_rows = _drop_ignored(table, saved.get(table, {}))
        live_rows = _drop_ignored(table, live.get(table, {}))
        if saved_rows == live_rows:
            continue
        result[table] = {
            "added": sorted(set(live_rows) - set(saved_rows)),
            "removed": sorted(set(saved_rows) - set(live_rows)),
            "changed": {
                k: diff_row(saved_rows[k], live_rows[k])
                for k in sorted(set(saved_rows) & set(live_rows))
                if saved_rows[k] != live_rows[k]
            },
        }
    return result

def diff_frr(saved: str, live: str) -> dict:
    """Return the FRR lines only in live ("added") and only in saved ("removed")."""
    saved_lines = {line.strip() for line in saved.splitlines()}
    live_lines = {line.strip() for line in live.splitlines()}
    if saved_lines == live_lines:
        return {}
    return {
        "added": sorted(live_lines - saved_lines),
        "removed": sorted(saved_lines - live_lines),
    }


def _read(state_dir: Path, switch: str) -> tuple[dict, str]:
    cfg = json.loads((state_dir / "raw" / f"{switch}.config_db.json").read_text())
    return cfg, (state_dir / "raw" / f"{switch}.frr.txt").read_text()


def _init_state_dir(item_id: str) -> Path:
    return INIT_STATES_DIR / json.loads((DATASET_DIR / f"{item_id}.json").read_text())["init_state"]


def manual_ignores(item_id: str) -> dict:
    """{switch: {"config_db": {table: row keys}, "frr": lines}} to leave out of item_id's init check."""
    pos = CHAIN_A_ORDER.index(item_id)
    ignores = {}
    for step, switch, part, first, last in MANUAL_CHANGES:
        if not CHAIN_A_ORDER.index(first) <= pos <= CHAIN_A_ORDER.index(last):
            continue
        next_item = CHAIN_A_ORDER[CHAIN_A_ORDER.index(step) + 1]
        before = _read(_init_state_dir(step), switch)
        after = _read(_init_state_dir(next_item), switch)
        ignore = ignores.setdefault(switch, {"config_db": {}, "frr": set()})
        if part == "config_db":
            for table, d in diff_config_db(before[0], after[0]).items():
                ignore["config_db"].setdefault(table, set()).update(d["added"], d["removed"], d["changed"])
        else:
            d = diff_frr(before[1], after[1])
            ignore["frr"].update(d.get("added", []), d.get("removed", []))
    return ignores


def _without(cfg: dict, frr: str, ignore: dict) -> tuple[dict, str]:
    rows_to_drop = ignore.get("config_db", {})
    cfg = {
        table: {k: v for k, v in rows.items() if k not in rows_to_drop.get(table, ())}
        for table, rows in cfg.items()
    }
    lines_to_drop = ignore.get("frr", set())
    frr = "\n".join(line for line in frr.splitlines() if line.strip() not in lines_to_drop)
    return cfg, frr


def compare(saved_dir: Path, live_dir: Path, ignore: dict | None = None) -> dict:
    """Compare every switch's raw files, leaving out `ignore` (see manual_ignores).
    An empty dict means everything matches."""
    differences = {}
    for switch in sorted(mgmt_ips):
        switch_ignore = (ignore or {}).get(switch, {})
        saved_cfg, saved_frr = _without(*_read(saved_dir, switch), switch_ignore)
        live_cfg, live_frr = _without(*_read(live_dir, switch), switch_ignore)

        switch_diff = {}
        cfg_diff = diff_config_db(saved_cfg, live_cfg)
        if cfg_diff:
            switch_diff["config_db"] = cfg_diff
        frr_diff = diff_frr(saved_frr, live_frr)
        if frr_diff:
            switch_diff["frr"] = frr_diff
        if switch_diff:
            differences[switch] = switch_diff
    return differences


def run_item(item: dict) -> dict:
    item_id = item["id"]
    saved_dir = INIT_STATES_DIR / item["init_state"]
    print(f"\n{'=' * 60}\n{item_id}: expected init_state = {item['init_state']!r}\n{'=' * 60}")

    if not saved_dir.exists():
        print(f"[{item_id}] no saved init_state at {saved_dir} - skipping")
        return {"id": item_id, "verdict": "no_init_state"}

    live_dir = CAPTURE_DIR / f"{item_id}_init"
    capture(live_dir, mgmt_ips)

    differences = compare(saved_dir, live_dir, manual_ignores(item_id))
    if differences:
        print(json.dumps(differences, indent=2))
    return {
        "id": item_id,
        "verdict": "match" if not differences else "mismatch",
        "differences": differences,
    }


def build_inventory() -> str:
    """Ansible hosts.ini for the switches in config.mgmt_ips."""
    lines = ["[sonic_switches]"] + [
        f"{sw} ansible_host={ip} ansible_user={SSH_USERNAME} "
        f"ansible_password={SSH_PASSWORD} ansible_connection=ssh ansible_network_os=sonic"
        for sw, ip in mgmt_ips.items()
    ]
    return "\n".join(lines) + "\n"


def run_all():
    """
    Walks Chain A from S0. After an item's init_state matches, its reference
    playbook is applied, which moves the switches into the next item's
    init_state. Stops at the first mismatch or failed apply, since every
    later state builds on it. The switches end at C0.
    """
    results = []
    for item_id in CHAIN_A_ORDER:
        item = json.loads((DATASET_DIR / f"{item_id}.json").read_text())
        result = run_item(item)
        results.append(result)
        print(f"[{item_id}] verdict={result['verdict']}")
        if result["verdict"] != "match":
            print(f"[{item_id}] stopping: later init_states build on this one")
            break

        print(f"[{item_id}] applying reference playbook to reach the next init_state...")
        if not apply_playbook(item["reference_playbook"], build_inventory())["success"]:
            print(f"[{item_id}] stopping: reference playbook failed")
            break

    matched = sum(1 for r in results if r["verdict"] == "match")
    print(f"\n{matched}/{len(CHAIN_A_ORDER)} init_states matched")


if __name__ == "__main__":
    run_all()
