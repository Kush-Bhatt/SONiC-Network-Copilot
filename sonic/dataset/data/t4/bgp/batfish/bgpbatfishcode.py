"""
Batfish BGP verification for the 3-switch SONiC topology.

Snapshot layout:
  /home/user/bgp_frrsnapshotnoans/
      layer1_topology.json
      configs/
          VS-1.cfg
          VS-2.cfg
          VS-3.cfg

Run:
  pip install pybatfish pandas
  python3 batfish_bgp_verify.py
"""

import pandas as pd
from pybatfish.client.session import Session
from pybatfish.datamodel import *
from pybatfish.datamodel.flow import *

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)

# ── 1. Connect & load snapshot ────────────────────────────────────────────────
SNAPSHOT_PATH = "/home/user/bgp_frrsnapshotnoans"
SNAPSHOT_NAME = "sonic-frr-v6"
NETWORK_NAME  = "sonic-lab"

bf = Session(host="localhost")
bf.set_network(NETWORK_NAME)
bf.init_snapshot(SNAPSHOT_PATH, name=SNAPSHOT_NAME, overwrite=True)

print("=" * 60)
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


# ── [1] Parse warnings ────────────────────────────────────────────────────────
print("\n[1] Parse Warnings")
w = to_df(bf.q.parseWarning().answer())
print("  No parse warnings — configs loaded cleanly." if w.empty
      else w.to_string(index=False))


# ── [2] BGP session compatibility ─────────────────────────────────────────────
print("\n[2] BGP Session Compatibility")
compat = to_df(bf.q.bgpSessionCompatibility().answer())
print(compat[[
    "Node", "VRF", "Local_IP", "Remote_IP",
    "Local_AS", "Remote_AS", "Configured_Status"
]].to_string(index=False))


# ── [3] BGP session status ────────────────────────────────────────────────────
print("\n[3] BGP Session Status")
status = to_df(bf.q.bgpSessionStatus().answer())
print(status[[
    "Node", "VRF", "Local_AS", "Local_IP",
    "Remote_AS", "Remote_IP", "Established_Status"
]].to_string(index=False))


# ── [4] BGP RIB ───────────────────────────────────────────────────────────────
print("\n[4] BGP RIB — what each node learned via BGP")
bgp_rib = to_df(bf.q.bgpRib().answer())
print(bgp_rib[[
    "Node", "VRF", "Network", "Next_Hop_IP",
    "AS_Path", "Origin_Type"
]].to_string(index=False))


# ── [5] Main RIB ──────────────────────────────────────────────────────────────
print("\n[5] Main RIB — full routing table per node")
routes = to_df(bf.q.routes().answer())
print(routes[[
    "Node", "VRF", "Network", "Protocol",
    "Next_Hop_IP", "Metric"
]].to_string(index=False))


# ── reachability helper ────────────────────────────────────────────────────────
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


# ── [6] Reachability checks ───────────────────────────────────────────────────
check_reach(
    "[6] Reachability: VS-2 (10.0.1.254) → 192.168.200.1 (VS-3 side)",
    "@enter(VS-2[Vlan10])", "10.0.1.254", "192.168.200.1"
)

check_reach(
    "[7] Reachability: VS-3 (192.168.200.2) → 10.0.1.254 (VS-2 Vlan10)",
    "@enter(VS-3[Ethernet16])", "192.168.200.2", "10.0.1.254"
)

print("\n" + "=" * 60)
print("Verification complete.")
print("=" * 60)
