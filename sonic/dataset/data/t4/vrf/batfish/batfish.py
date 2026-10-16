from pybatfish.client.session import Session
from pybatfish.datamodel import *
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
pd.set_option('display.max_colwidth', None)

# ─────────────────────────────────────────
# CONNECT AND LOAD SNAPSHOT
# ─────────────────────────────────────────
SNAPSHOT_PATH = "/home/user/vrfsnapshot"
SNAPSHOT_NAME = "vrf-snapshot"
NETWORK_NAME  = "sonic-network"

bf = Session(host="localhost")
bf.set_network(NETWORK_NAME)
bf.init_snapshot(SNAPSHOT_PATH, name=SNAPSHOT_NAME, overwrite=True)
print("=" * 60)
print("Snapshot loaded")
print("=" * 60)

# ─────────────────────────────────────────
# STEP 1: Parse status
# ─────────────────────────────────────────
print("\nSTEP 1: File parse status")
print("-" * 40)
parse_status = bf.q.fileParseStatus().answer().frame()
print(parse_status)

# ─────────────────────────────────────────
# STEP 2: Nodes
# ─────────────────────────────────────────
print("\nSTEP 2: Nodes parsed")
print("-" * 40)
nodes = bf.q.nodeProperties().answer().frame()
if nodes.empty:
    print("ERROR: No nodes — fix parse issue first")
    exit()
print(nodes[["Node", "Configuration_Format"]])

# ─────────────────────────────────────────
# STEP 3: Ethernet16 interface on VS-1
# ─────────────────────────────────────────
print("\nSTEP 3: Ethernet16 on VS-1 — VRF binding check")
print("-" * 40)
eth16 = bf.q.interfaceProperties(
    nodes="vs-1",
    interfaces="Ethernet16"
).answer().frame()

if eth16.empty:
    print("ERROR: Ethernet16 not found on vs-1")
else:
    print(eth16[["Interface", "VRF", "Primary_Address", "Active"]].to_string(index=False))
    vrf_val = eth16["VRF"].values[0]
    ip_val  = eth16["Primary_Address"].values[0]
    print()
    if vrf_val == "Vrf-red":
        print(f"PASS: Ethernet16 is bound to VRF '{vrf_val}'")
        print(f"PASS: IP address on Ethernet16 is {ip_val}")
    else:
        print(f"FAIL: Ethernet16 is in VRF '{vrf_val}' — expected 'Vrf-red'")

# ─────────────────────────────────────────
# STEP 4: All interfaces on VS-1 with VRF
# ─────────────────────────────────────────
print("\nSTEP 4: All VS-1 interfaces and their VRFs")
print("-" * 40)
all_ifaces = bf.q.interfaceProperties(
    nodes="vs-1"
).answer().frame()
if not all_ifaces.empty:
    print(all_ifaces[["Interface", "VRF", "Primary_Address", "Active"]].to_string(index=False))

# ─────────────────────────────────────────
# STEP 5: Routes inside Vrf-red on VS-1
# ─────────────────────────────────────────
print("\nSTEP 5: Routes inside Vrf-red on VS-1")
print("-" * 40)
routes_red = bf.q.routes(
    nodes="vs-1",
    vrfs="Vrf-red"
).answer().frame()
if routes_red.empty:
    print("PASS: No routes leaked into Vrf-red — fully isolated!")
else:
    print(routes_red[["Node", "VRF", "Network", "Next_Hop", "Protocol"]].to_string(index=False))

# ─────────────────────────────────────────
# STEP 6: Routes in default VRF on VS-1
#         confirm 10.0.2.0/24 is NOT here
# ─────────────────────────────────────────
print("\nSTEP 6: Default VRF routes on VS-1")
print("        (10.0.2.0/24 must NOT appear here)")
print("-" * 40)
routes_default = bf.q.routes(
    nodes="vs-1",
    vrfs="default"
).answer().frame()
if routes_default.empty:
    print("No routes in default VRF")
else:
    print(routes_default[["Node", "VRF", "Network", "Next_Hop", "Protocol"]].to_string(index=False))
    vrf_red_leak = routes_default[routes_default["Network"].str.startswith("10.0.2.")]
    if vrf_red_leak.empty:
        print("\nPASS: 10.0.2.0/24 not in default VRF — no route leaking!")
    else:
        print("\nFAIL: 10.0.2.0/24 found in default VRF — route leaking detected!")

print("\n" + "=" * 60)
print("VRF VERIFICATION ON VS-1 ETHERNET16 COMPLETE")
print("=" * 60)
