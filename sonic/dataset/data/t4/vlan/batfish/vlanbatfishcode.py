"""
Batfish VLAN verification for the 3-switch SONiC Hybrid Topology.

Topology (from actual configs):
  VS-1 -- PortChannel12 (Ethernet4)  -- VS-2 (PortChannel12, Ethernet4)
  VS-1 -- PortChannel13 (Ethernet20) -- VS-3 (PortChannel13, Ethernet4)
  VS-2 -- PortChannel23 (Ethernet20) -- VS-3 (PortChannel23, Ethernet12)

  VS-1: Vlan10 = 10.0.1.253/24
        Ethernet12 (untagged, VLAN10) -- host 10.0.1.10
        Ethernet16 (untagged, VLAN10) -- host 10.0.1.20
        PortChannel12 = 10.0.12.1/30  (link to VS-2)
        PortChannel13 = 10.0.13.1/30  (link to VS-3)

  VS-2: Vlan10 = 10.0.1.254/24
        Ethernet12 (untagged, VLAN10) -- host 10.0.1.30
        Ethernet16 (untagged, VLAN10) -- host 10.0.1.11
        PortChannel12 = 10.0.12.2/30  (link to VS-1)
        PortChannel23 = 10.0.23.1/30  (link to VS-3)

  VS-3: No VLAN
        Ethernet16 = 192.168.200.2/24 (external network)
        PortChannel13 = 10.0.13.2/30  (link to VS-1)
        PortChannel23 = 10.0.23.2/30  (link to VS-2)

Routing: All STATIC (no BGP in this topology)

  VS-1 static routes:
    0.0.0.0/0     → 10.0.13.2 (VS-3 via PortChannel13)
    10.0.1.11/32  → 10.0.12.2 (VS-2 via PortChannel12)
    10.0.1.30/32  → 10.0.12.2 (VS-2 via PortChannel12)
    10.0.1.254/32 → 10.0.12.2 (VS-2 via PortChannel12)

  VS-2 static routes:
    0.0.0.0/0     → 10.0.23.2 (VS-3 via PortChannel23)
    10.0.1.10/32  → 10.0.12.1 (VS-1 via PortChannel12)
    10.0.1.20/32  → 10.0.12.1 (VS-1 via PortChannel12)
    10.0.1.253/32 → 10.0.12.1 (VS-1 via PortChannel12)

  VS-3 static routes:
    10.0.1.10/32  → 10.0.13.1 (VS-1 via PortChannel13)
    10.0.1.11/32  → 10.0.23.1 (VS-2 via PortChannel23)
    10.0.1.20/32  → 10.0.13.1 (VS-1 via PortChannel13)
    10.0.1.30/32  → 10.0.23.1 (VS-2 via PortChannel23)
    10.0.1.253/32 → 10.0.13.1 (VS-1 via PortChannel13)
    10.0.1.254/32 → 10.0.23.1 (VS-2 via PortChannel23)

Snapshot layout:
  /home/user/vlan_hybrid_snapshot/
      layer1_topology.json
      configs/
          VS-1.cfg
          VS-2.cfg
          VS-3.cfg

Run:
  pip install pybatfish pandas
  python3 batfish_vlan_hybrid_verify.py
"""

import pandas as pd
from pybatfish.client.session import Session
from pybatfish.datamodel import *
from pybatfish.datamodel.flow import *

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)

# ── 1. Connect & load snapshot ────────────────────────────────────────────────
SNAPSHOT_PATH = "/home/user/vlan_hybrid_snapshot"
SNAPSHOT_NAME = "sonic-vlan-hybrid-v1"
NETWORK_NAME  = "sonic-vlan-lab"

bf = Session(host="localhost")
bf.set_network(NETWORK_NAME)
bf.init_snapshot(SNAPSHOT_PATH, name=SNAPSHOT_NAME, overwrite=True)

print("=" * 60)
print("Network  : ", NETWORK_NAME)
print("Snapshot : ", SNAPSHOT_NAME)
print("Path     : ", SNAPSHOT_PATH)
print("=" * 60)


# ── helper ────────────────────────────────────────────────────────────────────
def to_df(answer):
    try:
        return answer.frame()
    except AttributeError:
        pass
    try:
        return answer.as_frame()
    except AttributeError:
        pass
    try:
        return pd.DataFrame(list(answer))
    except Exception:
        return pd.DataFrame()


# ── [1] Parse Warnings ────────────────────────────────────────────────────────
print("\n[1] Parse Warnings")
w = to_df(bf.q.parseWarning().answer())
print("  No parse warnings — configs loaded cleanly." if w.empty
      else w.to_string(index=False))


# ── [2] Interface Properties ──────────────────────────────────────────────────
# Verifies VLAN membership, access mode, IP assignment on each interface
print("\n[2] Interface Properties")
try:
    ifaces = to_df(bf.q.interfaceProperties(
        properties="Interface, Primary_Address, Active, "
                   "Switchport, Switchport_Mode, Access_VLAN, Allowed_VLANs"
    ).answer())
    if ifaces.empty:
        print("  No interface data returned.")
    else:
        # Only show relevant interfaces (not all 32 ports)
        relevant = ifaces[
            ifaces["Interface"].astype(str).str.contains(
                "Vlan|PortChannel|Ethernet12|Ethernet16|Loopback", na=False
            )
        ]
        print(relevant.to_string(index=False))
except Exception as e:
    print(f"  !! Query failed: {e}")


# ── [3] VLAN Properties ───────────────────────────────────────────────────────
# Checks VLAN 10 exists on VS-1 and VS-2 with correct member ports
print("\n[3] VLAN Properties — VLAN 10 on VS-1 and VS-2")
try:
    vlans = to_df(bf.q.switchedVlanProperties().answer())
    if vlans.empty:
        print("  No VLAN data returned.")
    else:
        print(vlans.to_string(index=False))
except Exception as e:
    print(f"  !! Query failed: {e}")


# ── [4] Main RIB ──────────────────────────────────────────────────────────────
# Verifies all static routes are installed correctly on all 3 switches
print("\n[4] Main RIB — static routes per node")
routes = to_df(bf.q.routes().answer())
if not routes.empty:
    print(routes[[
        "Node", "VRF", "Network", "Protocol",
        "Next_Hop_IP", "Metric"
    ]].to_string(index=False))
else:
    print("  No route data returned.")


# ── [5] VLAN subnet routes ────────────────────────────────────────────────────
# Verifies 10.0.1.0/24 and host-specific routes are present everywhere
print("\n[5] VLAN 10 host routes — 10.0.1.x across all nodes")
if not routes.empty:
    vlan_routes = routes[
        routes["Network"].astype(str).str.startswith("10.0.1.")
    ]
    if vlan_routes.empty:
        print("  No 10.0.1.x routes found — static routes may not be parsed.")
    else:
        print(vlan_routes[[
            "Node", "VRF", "Network", "Protocol", "Next_Hop_IP"
        ]].to_string(index=False))


# ── [6] Layer-3 Edges ─────────────────────────────────────────────────────────
# Verifies PortChannel adjacencies between the 3 switches
print("\n[6] Layer-3 Edges — PortChannel links between switches")
try:
    adj = to_df(bf.q.layer3Edges().answer())
    if adj.empty:
        print("  No L3 edge data returned.")
    else:
        cols = [c for c in ["Interface", "Remote_Interface", "IPs", "Remote_IPs"]
                if c in adj.columns]
        print(adj[cols].to_string(index=False))
except Exception as e:
    print(f"  !! Query failed: {e}")


# ── reachability helper ───────────────────────────────────────────────────────
def check_reach(label, src_iface, src_ip, dst_ip):
    print(f"\n{label}")
    try:
        r = to_df(bf.q.reachability(
            pathConstraints=PathConstraints(startLocation=src_iface),
            headers=HeaderConstraints(srcIps=src_ip, dstIps=dst_ip),
            actions="SUCCESS"
        ).answer())
        if r.empty:
            print("  !! No successful paths found.")
        else:
            cols = [c for c in ["Flow", "Traces", "Action"] if c in r.columns]
            print(r[cols].to_string(index=False))
    except Exception as e:
        print(f"  !! Query failed: {e}")


# ── [7] Reachability Checks ───────────────────────────────────────────────────
# Based on actual static routes in configs

# --- VLAN gateway to gateway (inter-switch VLAN) ---
check_reach(
    "[7a] VS-1 gateway (10.0.1.253) → VS-2 gateway (10.0.1.254)",
    "@enter(VS-1[Vlan10])", "10.0.1.253", "10.0.1.254"
)

check_reach(
    "[7b] VS-2 gateway (10.0.1.254) → VS-1 gateway (10.0.1.253)",
    "@enter(VS-2[Vlan10])", "10.0.1.254", "10.0.1.253"
)

# --- Host on VS-1 side → Host on VS-2 side ---
# VS-1 has static: 10.0.1.11/32 → 10.0.12.2, 10.0.1.30/32 → 10.0.12.2
# VS-2 has static: 10.0.1.10/32 → 10.0.12.1, 10.0.1.20/32 → 10.0.12.1
check_reach(
    "[7c] VS-1 host (10.0.1.10) → VS-2 host (10.0.1.11)",
    "@enter(VS-1[Ethernet16])", "10.0.1.10", "10.0.1.11"
)

check_reach(
    "[7d] VS-1 host (10.0.1.20) → VS-2 host (10.0.1.30)",
    "@enter(VS-1[Ethernet12])", "10.0.1.20", "10.0.1.30"
)

check_reach(
    "[7e] VS-2 host (10.0.1.11) → VS-1 host (10.0.1.10)",
    "@enter(VS-2[Ethernet16])", "10.0.1.11", "10.0.1.10"
)

check_reach(
    "[7f] VS-2 host (10.0.1.30) → VS-1 host (10.0.1.20)",
    "@enter(VS-2[Ethernet12])", "10.0.1.30", "10.0.1.20"
)

# --- VLAN hosts → VS-3 external network ---
# VS-1 default route → 10.0.13.2 (VS-3 PortChannel13)
# VS-2 default route → 10.0.23.2 (VS-3 PortChannel23)
check_reach(
    "[7g] VS-1 host (10.0.1.10) → VS-3 external (192.168.200.1)",
    "@enter(VS-1[Ethernet16])", "10.0.1.10", "192.168.200.1"
)

check_reach(
    "[7h] VS-2 host (10.0.1.11) → VS-3 external (192.168.200.1)",
    "@enter(VS-2[Ethernet16])", "10.0.1.11", "192.168.200.1"
)

# --- VS-3 external → VLAN hosts (return path) ---
# VS-3 has individual host routes split across PortChannel13 and PortChannel23
check_reach(
    "[7i] VS-3 (192.168.200.2) → VS-1 host (10.0.1.10) via PortChannel13",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.10"
)

check_reach(
    "[7j] VS-3 (192.168.200.2) → VS-1 host (10.0.1.20) via PortChannel13",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.20"
)

check_reach(
    "[7k] VS-3 (192.168.200.2) → VS-2 host (10.0.1.11) via PortChannel23",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.11"
)

check_reach(
    "[7l] VS-3 (192.168.200.2) → VS-2 host (10.0.1.30) via PortChannel23",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.30"
)

# --- VS-3 → VLAN gateways ---
# VS-3 has: 10.0.1.253/32 → 10.0.13.1, 10.0.1.254/32 → 10.0.23.1
check_reach(
    "[7m] VS-3 (192.168.200.2) → VS-1 Vlan10 gateway (10.0.1.253)",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.253"
)

check_reach(
    "[7n] VS-3 (192.168.200.2) → VS-2 Vlan10 gateway (10.0.1.254)",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.254"
)


print("\n" + "=" * 60)
print("Verification complete.")
print("=" * 60)
