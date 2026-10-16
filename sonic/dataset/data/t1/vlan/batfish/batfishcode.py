from pybatfish.client.session import Session
from pybatfish.datamodel import *

bf = Session(host="localhost")
bf.set_network("t1-topo")
bf.init_snapshot("/home/user/t1toposnapshot", name="t1-snapshot", overwrite=True)

# 1. Parse check across all four switches
print("=== Parse Status ===")
print(bf.q.fileParseStatus().answer().frame())

# 2. VLAN SVI check — pull all interfaces, filter in pandas
print("\n=== Interface Properties (VLAN SVI on VS-1/VS-2) ===")
vlan_iface = bf.q.interfaceProperties().answer().frame()
vlan_only = vlan_iface[
    vlan_iface["Interface"].astype(str).str.contains("Vlan")
    & vlan_iface["Interface"].astype(str).str.contains("vs-1|vs-2")
]
print(vlan_only[["Interface", "Active", "Primary_Address", "Switchport"]])

# 3. VLAN membership — which interfaces on VS-1/VS-2 belong to which VLAN
print("\n=== Switched VLAN Properties (VS-1/VS-2) ===")
switch_iface = bf.q.switchedVlanProperties().answer().frame()
switch_iface_filtered = switch_iface[switch_iface["Node"].isin(["vs-1", "vs-2"])]
print(switch_iface_filtered)

# 4. Full switchport detail on VS-1/VS-2 (access ports + mode + VLAN ID)
print("\n=== All Interface Switchport Details (VS-1/VS-2) ===")
iface_props = bf.q.interfaceProperties().answer().frame()
iface_props_filtered = iface_props[
    iface_props["Interface"].astype(str).str.contains("vs-1|vs-2")
]
print(iface_props_filtered[["Interface", "Switchport_Mode", "Access_VLAN", "Active"]])

# 5. Sanity check: confirm VLAN does NOT exist on VS-3/VS-4 (top tier, no VLAN expected)
print("\n=== VLAN Check Across All Four Switches ===")
all_vlans = bf.q.switchedVlanProperties().answer().frame()
print(all_vlans)
