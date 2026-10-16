"""
sonic/svc/fabric.py
-------------------
Live fabric state for the web console: the GNS3 topology graph plus each
switch's currently-configured VLAN / BGP / interface state, refreshed on an
interval by one background thread and served from memory.

The pure builders at the top take already-fetched data, so they are testable
without GNS3 or SSH; every network call lives in FabricCache at the bottom.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sonic.gns3.inventory_builder import is_sonic_node
from sonic.switch.parser import (
    _parse_bgp,
    _parse_portchannels,
    _parse_svi_ips,
    _parse_vlan_members,
    _parse_vlans,
)


# ── Pure builders ────────────────────────────────────────────────────────


def build_fabric_graph(nodes: list[dict], links: list[dict], mgmt_ips: dict) -> dict:
    """GNS3 node/link JSON -> {"nodes": [...], "links": [...]}.

    Non-SONiC nodes (clouds, NAT, hosts) are kept but flagged is_switch=False:
    dropping them would leave dangling links and a topology that does not match
    what the operator sees in GNS3.
    """
    out_nodes = []
    for n in nodes:
        out_nodes.append({
            "name": n["name"],
            "node_id": n["node_id"],
            "node_type": n.get("node_type", "unknown"),
            "is_switch": is_sonic_node(n),
            "mgmt_ip": mgmt_ips.get(n["name"]),
            # GNS3 canvas coordinates — the operator's own layout, reused as-is
            # so the console and GNS3 agree on where each device sits.
            "x": n.get("x", 0),
            "y": n.get("y", 0),
        })

    by_id = {n["node_id"]: n["name"] for n in out_nodes}
    out_links = []
    for link in links:
        endpoints = link.get("nodes") or []
        if len(endpoints) != 2:
            continue
        a, b = endpoints
        name_a, name_b = by_id.get(a.get("node_id")), by_id.get(b.get("node_id"))
        if not name_a or not name_b:
            continue
        out_links.append({
            "link_id": link.get("link_id"),
            "a": name_a,
            "b": name_b,
            # Same port-number convention build_topology uses (topology_builder.py:42).
            "port_a": f"Ethernet{a.get('port_number', 0) * 4}",
            "port_b": f"Ethernet{b.get('port_number', 0) * 4}",
        })

    return {"nodes": out_nodes, "links": out_links}


def switch_state(config_db: dict, frr_text: str, bgp_summary: dict | None = None) -> dict:
    """config_db + FRR text -> the state shape the console renders.

    Reuses switch/parser.py's parsers so the console and the pipeline agree on
    what "the VLANs on this switch" means.
    """
    bgp = _parse_bgp(frr_text or "")
    peers = _bgp_summary_peers(bgp_summary)

    neighbors = []
    for nbr in bgp["neighbors"]:
        peer = peers.get(nbr["ip"], {})
        neighbors.append({
            "ip": nbr["ip"],
            "remote_as": nbr["remote_as"],
            # Session state is runtime, not config — absent unless a summary
            # was fetched. "unknown" is honest; "Idle" would be a guess.
            "session": peer.get("state", "unknown"),
        })
    # A peer FRR knows about at runtime but that is not in the parsed config.
    for ip, peer in peers.items():
        if not any(n["ip"] == ip for n in neighbors):
            neighbors.append({
                "ip": ip,
                "remote_as": str(peer.get("remoteAs", "")),
                "session": peer.get("state", "unknown"),
            })

    interfaces = []
    for name, port in sorted((config_db or {}).get("PORT", {}).items()):
        interfaces.append({
            "name": name,
            "admin_up": port.get("admin_status") == "up",
            "mtu": port.get("mtu"),
            "alias": port.get("alias"),
        })

    return {
        "vlans": _parse_vlans(config_db or {}),
        "vlan_members": _parse_vlan_members(config_db or {}),
        "svi_ips": _parse_svi_ips(config_db or {}),
        "portchannels": _parse_portchannels(config_db or {}),
        "bgp": {
            "local_as": bgp["as"],
            "router_id": bgp["router_id"],
            "neighbors": neighbors,
        },
        "interfaces": interfaces,
        "hostname": (config_db or {}).get("DEVICE_METADATA", {})
                        .get("localhost", {}).get("hostname"),
    }


def _bgp_summary_peers(summary: dict | None) -> dict:
    """Flatten `vtysh -c 'show ip bgp summary json'` into {peer_ip: {...}}."""
    if not summary:
        return {}
    peers = {}
    for af in summary.values():
        if isinstance(af, dict):
            peers.update(af.get("peers") or {})
    return peers


# ── The I/O edge: one background poller, one in-memory snapshot ──────────


class FabricCache:
    """Refreshes the fabric snapshot on an interval; serves it from memory.

    One thread for the whole process: N browser tabs cost zero extra SSH.
    """

    def __init__(self, poll_seconds: int, want_bgp_summary: bool):
        self._poll_seconds = poll_seconds
        self._want_bgp_summary = want_bgp_summary
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.generation = 0
        self._snapshot: dict[str, Any] = {
            "generation": 0, "updated_at": None, "poll_seconds": poll_seconds,
            "refreshing": False, "project_id": None, "topology_name": None,
            "nodes": [], "links": [], "error": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def request_refresh(self) -> None:
        """Ask for an out-of-band refresh (after an apply, or on operator click)."""
        self._wake.set()

    def _loop(self) -> None:
        while True:
            try:
                self._refresh_once()
            except Exception as e:  # a dead GNS3 must not kill the poller
                self._publish_error(f"{type(e).__name__}: {e}")
            self._wake.wait(timeout=self._poll_seconds)
            self._wake.clear()

    def _refresh_once(self) -> None:
        from sonic.gns3.api_client import GNS3_PROJECT_ID, get_links, get_nodes
        from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project

        self._set_refreshing(True)
        try:
            mgmt_ips, topology_name = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
            graph = build_fabric_graph(
                get_nodes(GNS3_PROJECT_ID), get_links(GNS3_PROJECT_ID), mgmt_ips
            )
            for node in graph["nodes"]:
                if node["is_switch"] and node["mgmt_ip"]:
                    node.update(self._fetch_switch(node["mgmt_ip"], node["name"]))
                else:
                    node.update({"state": None, "error": None, "reachable": None})

            with self._lock:
                self.generation += 1
                self._snapshot = {
                    "generation": self.generation,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "poll_seconds": self._poll_seconds,
                    "refreshing": False,
                    "project_id": GNS3_PROJECT_ID,
                    "topology_name": topology_name,
                    "nodes": graph["nodes"],
                    "links": graph["links"],
                    "error": None,
                }
        finally:
            self._set_refreshing(False)

    def _fetch_switch(self, ip: str, name: str) -> dict:
        """Per-switch SSH. A switch that fails is reported, not raised — one
        unreachable box must not blank the whole topology."""
        import json as _json

        from sonic.switch.ssh_client import get_frr_config, get_sonic_config, run_command

        try:
            config_db = _json.loads(get_sonic_config(ip, name))
            frr_text = get_frr_config(ip)
        except Exception as e:
            return {"state": None, "error": f"{type(e).__name__}: {e}", "reachable": False}

        bgp_summary = None
        if self._want_bgp_summary:
            ok, out, _ = run_command(ip, "sudo vtysh -c 'show ip bgp summary json'")
            if ok:
                try:
                    bgp_summary = _json.loads(out)
                except ValueError:
                    bgp_summary = None

        return {
            "state": switch_state(config_db, frr_text, bgp_summary),
            "error": None,
            "reachable": True,
        }

    def _set_refreshing(self, value: bool) -> None:
        with self._lock:
            self._snapshot = {**self._snapshot, "refreshing": value}

    def _publish_error(self, message: str) -> None:
        with self._lock:
            self.generation += 1
            self._snapshot = {
                **self._snapshot,
                "generation": self.generation,
                "error": message,
                "refreshing": False,
            }


from sonic.config.settings import FABRIC_BGP_SUMMARY, FABRIC_POLL_SECONDS  # noqa: E402

FABRIC = FabricCache(FABRIC_POLL_SECONDS, FABRIC_BGP_SUMMARY)
