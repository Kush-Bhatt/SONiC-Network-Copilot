from pybatfish.client.session import Session
from pybatfish.datamodel import *

bf = Session(host="localhost")
bf.set_network("t2-topo")
bf.init_snapshot("/home/user/t2toposnapshot", name="t2-snapshot", overwrite=True)

# 1. Parse check across all four switches
print("=== Parse Status ===")
print(bf.q.fileParseStatus().answer().frame())

# 2. VLAN10 SVI on VS-4 (confirms the L3 interface/SVI exists)
print("\n=== Interface Properties (VLAN10 SVI on VS-4) ===")
vlan_iface = bf.q.interfaceProperties(
    nodes="VS-4",
    interfaces="Vlan10"
).answer().frame()
print(vlan_iface[["Interface", "Active", "Primary_Address", "Switchport"]])

# 3. VLAN membership — which interfaces on VS-4 belong to VLAN10
print("\n=== Switched VLAN Properties (VS-4) ===")
switch_iface = bf.q.switchedVlanProperties(
    nodes="VS-4"
).answer().frame()
print(switch_iface)

# 4. Full switchport detail on VS-4 (access ports + mode + VLAN ID)
print("\n=== All Interface Switchport Details (VS-4) ===")
iface_props = bf.q.interfaceProperties(
    nodes="VS-4"
).answer().frame()
print(iface_props[["Interface", "Switchport_Mode", "Access_VLAN", "Active"]])

# 5. Sanity check: confirm VLAN10 does NOT exist on VS-1/VS-2/VS-3
#    (useful to prove it's correctly scoped only to VS-4)
print("\n=== VLAN Check Across All Switches ===")
all_vlans = bf.q.switchedVlanProperties().answer().frame()
print(all_vlans)
