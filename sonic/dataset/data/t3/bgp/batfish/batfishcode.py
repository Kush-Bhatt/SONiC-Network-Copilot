from pybatfish.client.session import Session
from pybatfish.datamodel import *

bf = Session(host="localhost")
bf.set_network("t3-topo")
bf.init_snapshot("/home/user/t3toposnapshot", name="t3-snapshot", overwrite=True)

# 1. Parse check
print("=== Parse Status ===")
print(bf.q.parseStatus().answer().frame())

# 2. Is BGP process configured at all (AS number, router ID)?
print("\n=== BGP Process Configuration ===")
bgp_proc = bf.q.bgpProcessConfiguration(nodes="VS-1").answer().frame()
print(bgp_proc)

# 3. Is the neighbor/peer configured correctly?
print("\n=== BGP Peer Configuration ===")
bgp_peers = bf.q.bgpPeerConfiguration(nodes="VS-1").answer().frame()
print(bgp_peers[["Node", "VRF", "Local_AS", "Remote_AS", "Remote_IP", "Description"]])
