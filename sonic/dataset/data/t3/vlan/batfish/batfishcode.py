from pybatfish.client.session import Session
from pybatfish.datamodel import *

# Connect to Batfish service
bf = Session(host="localhost")

# Set network and snapshot
bf.set_network("t3-topo")
bf.init_snapshot("/home/user/t3toposnapshot", name="t3-snapshot", overwrite=True)

# 1. Check VLAN properties (does VLAN 10 exist as an L3 SVI / is it known to Batfish)
print("=== Interface Properties (VLAN10 SVI) ===")
vlan_iface = bf.q.interfaceProperties(
    nodes="VS-1",
    interfaces="Vlan10"
).answer().frame()
print(vlan_iface[["Interface", "Active", "Primary_Address", "Switchport"]])

# 2. Check switchport/access-VLAN membership on the physical interfaces
print("\n=== Switchport / Access VLAN Membership ===")
switch_iface = bf.q.switchedVlanProperties(
    nodes="VS-1"
).answer().frame()
print(switch_iface)

# 3. Cross-check: list all interfaces and their access VLAN assignment directly
print("\n=== All Interface Switchport Details ===")
iface_props = bf.q.interfaceProperties(
    nodes="VS-1"
).answer().frame()
print(iface_props[["Interface", "Switchport_Mode", "Access_VLAN", "Active"]])
