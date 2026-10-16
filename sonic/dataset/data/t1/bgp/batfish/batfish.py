from pybatfish.client.session import Session
from pybatfish.datamodel import *

bf = Session(host="localhost")
bf.set_network("t1-topo")
bf.init_snapshot("/home/user/t1toposnapshot", name="t1-snapshot", overwrite=True)

# 1. Parse check across all four switches
print("=== Parse Status ===")
print(bf.q.fileParseStatus().answer().frame())

# 2. Layer 3 topology — confirms Batfish sees the physical links between switches
print("\n=== Layer 3 Edges ===")
l3_edges = bf.q.layer3Edges().answer().frame()
print(l3_edges)

# 3. BGP process configuration on all four switches
print("\n=== BGP Process Configuration (All Switches) ===")
bgp_proc = bf.q.bgpProcessConfiguration().answer().frame()
print(bgp_proc[["Node", "VRF", "Router_ID", "Neighbors"]])

# 4. BGP peer configuration on all four switches
print("\n=== BGP Peer Configuration (All Switches) ===")
bgp_peers = bf.q.bgpPeerConfiguration().answer().frame()
print(bgp_peers[["Node", "VRF", "Local_AS", "Remote_AS", "Local_IP", "Remote_IP", "Description"]])

# 5. BGP session compatibility — config-level check (AS numbers, IPs match up)
print("\n=== BGP Session Compatibility (All Switches) ===")
bgp_compat = bf.q.bgpSessionCompatibility().answer().frame()
print(bgp_compat[["Node", "VRF", "Remote_AS", "Remote_IP", "Configured_Status"]])

# 6. BGP session status — did sessions actually establish?
print("\n=== BGP Session Status (All Switches) ===")
bgp_status = bf.q.bgpSessionStatus().answer().frame()
print(bgp_status[["Node", "VRF", "Remote_AS", "Remote_IP", "Established_Status"]])

# 7. BGP RIB — confirm routes learned via BGP show up per switch
print("\n=== BGP RIB (All Switches) ===")
bgp_rib = bf.q.bgpRib().answer().frame()
print(bgp_rib)
