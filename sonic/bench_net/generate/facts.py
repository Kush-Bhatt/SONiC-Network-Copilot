"""
sonic/bench_net/generate/facts.py
-------------------------------------
Standalone copy of sonic/hitl/facts.py. See that file's docstring for
design rationale. Deduplicated named_structure entry (the original had
it listed twice with different key_columns — fixed here to the more
complete version only).
"""
import difflib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from pybatfish.client.session import Session

from sonic.bench_net.config import BATFISH_HOST

NETWORK_NAME = "sonic-network-hitl"


def _unified_diff(path_a: Path, path_b: Path, label_a: str, label_b: str) -> str:
    text_a = path_a.read_text(encoding="utf-8").splitlines(keepends=True) if path_a.exists() else []
    text_b = path_b.read_text(encoding="utf-8").splitlines(keepends=True) if path_b.exists() else []
    diff = list(difflib.unified_diff(text_a, text_b, fromfile=label_a, tofile=label_b, lineterm=""))
    return "".join(diff) if diff else ""


def extract_raw_diff(t1_dir: Path, t2_dir: Path) -> dict:
    t1_switches = {p.stem.split(".")[0] for p in (t1_dir / "raw").glob("*.config_db.json")}
    t2_switches = {p.stem.split(".")[0] for p in (t2_dir / "raw").glob("*.config_db.json")}
    switches = sorted(t1_switches | t2_switches)
    result = {}
    for name in switches:
        result[name] = {
            "config_db": _unified_diff(
                t1_dir / "raw" / f"{name}.config_db.json", t2_dir / "raw" / f"{name}.config_db.json",
                f"t1/{name}.config_db.json", f"t2/{name}.config_db.json",
            ),
            "frr": _unified_diff(
                t1_dir / "raw" / f"{name}.frr.txt", t2_dir / "raw" / f"{name}.frr.txt",
                f"t1/{name}.frr.txt", f"t2/{name}.frr.txt",
            ),
        }
    return result


@dataclass
class Fact:
    id: str
    table: str
    node: str
    change: str
    detail: dict


def _fact_id(table: str, node: str, *parts) -> str:
    return ":".join([table, node, *[str(p) for p in parts]])


def _change_from_presence(presence: str) -> Optional[str]:
    if presence == "Only in Snapshot":
        return "added"
    if presence == "Only in Reference":
        return "removed"
    if presence == "In both":
        return "modified"
    return None


def _row_to_modified_detail(row: dict) -> dict:
    detail = {}
    snapshot_keys = {k[len("Snapshot_"):] for k in row if k.startswith("Snapshot_")}
    reference_keys = {k[len("Reference_"):] for k in row if k.startswith("Reference_")}
    for field in sorted(snapshot_keys & reference_keys):
        new_val = row.get(f"Snapshot_{field}")
        old_val = row.get(f"Reference_{field}")

        def _norm(v):
            if v is None:
                return None
            if isinstance(v, (list, tuple)):
                return tuple(str(x) for x in v) if v else None
            if isinstance(v, str) and v == "":
                return None
            return v

        old_n, new_n = _norm(old_val), _norm(new_val)
        if old_n == new_n:
            continue
        detail[field] = {
            "old": list(old_n) if isinstance(old_n, tuple) else old_n,
            "new": list(new_n) if isinstance(new_n, tuple) else new_n,
        }
    return detail


def _row_to_full_detail(row: dict, prefix: str) -> dict:
    detail = {}
    for key, val in row.items():
        if not key.startswith(prefix):
            continue
        clean_key = key[len(prefix):]
        if val is None:
            continue
        if isinstance(val, (list, tuple)) and len(val) == 0:
            continue
        if isinstance(val, str) and val == "":
            continue
        try:
            if hasattr(val, "__iter__") and not isinstance(val, str):
                val = [str(v) for v in val]
        except TypeError:
            pass
        detail[clean_key] = val
    return detail


QUESTIONS = [
    ("vlan", lambda bf: bf.q.switchedVlanProperties(), ["Node", "VLAN_ID"]),
    ("interface", lambda bf: bf.q.interfaceProperties(), ["Interface"]),
    ("bgp_neighbor", lambda bf: bf.q.bgpPeerConfiguration(), ["Node", "Remote_IP"]),
    ("bgp_session_status", lambda bf: bf.q.bgpSessionStatus(), ["Node", "Remote_IP"]),
    ("bgp_session_compat", lambda bf: bf.q.bgpSessionCompatibility(), ["Node", "Remote_IP"]),
    ("bgp_edge", lambda bf: bf.q.bgpEdges(), ["Node"]),
    ("route", lambda bf: bf.q.routes(), ["Node"]),
    ("bgp_process", lambda bf: bf.q.bgpProcessConfiguration(), ["Node", "VRF"]),
    ("ip_owner", lambda bf: bf.q.ipOwners(), ["Node", "Interface", "IP"]),
    ("named_structure", lambda bf: bf.q.namedStructures(), ["Node", "Structure_Type", "Structure_Name"]),
]


def _node_from_row(row: dict) -> str:
    if row.get("Node") is not None:
        return str(row["Node"])
    if row.get("Interface") is not None:
        iface = row["Interface"]
        hostname = getattr(iface, "hostname", None)
        return str(hostname) if hostname else str(iface)
    return "unknown"


def extract_facts(t1_dir: Path, t2_dir: Path) -> list[Fact]:
    bf = Session(host=BATFISH_HOST)
    bf.set_network(NETWORK_NAME)
    bf.init_snapshot(str(t1_dir), name="t1", overwrite=True)
    bf.init_snapshot(str(t2_dir), name="t2", overwrite=True)

    facts: list[Fact] = []
    for table_label, q_fn, key_columns in QUESTIONS:
        try:
            df = q_fn(bf).answer(snapshot="t2", reference_snapshot="t1").frame()
        except Exception as e:
            print(f"[facts] WARNING: question {table_label} failed: {e}")
            continue

        for _, row in df.iterrows():
            row = row.to_dict()
            change = _change_from_presence(row.get("KeyPresence"))
            if change is None:
                continue
            node = _node_from_row(row)
            if change == "modified":
                detail = _row_to_modified_detail(row)
                if not detail:
                    continue
            else:
                prefix = "Snapshot_" if change == "added" else "Reference_"
                detail = _row_to_full_detail(row, prefix)

            id_parts = [str(row.get(col)) if row.get(col) is not None else "none" for col in key_columns]
            facts.append(Fact(id=_fact_id(table_label, node, *id_parts), table=table_label, node=node, change=change, detail=detail))

    return facts


def facts_to_dicts(facts: list[Fact]) -> list[dict]:
    return [asdict(f) for f in facts]
