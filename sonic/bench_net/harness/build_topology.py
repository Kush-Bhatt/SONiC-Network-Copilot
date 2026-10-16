# sonic/bench_net/build_topology.py
from sonic.gns3.api_client import (
    create_project, create_node, rename_node, create_link,
    GNS3_BASE_URL, AUTH,
)
import requests
import time

SONIC_TEMPLATE = "1bec1bff-6aa4-4f6e-a8f1-260588a204cd"
ALPINE_TEMPLATE = "0bc66251-9dde-4287-9360-a9e7cfc4d4a4"
CLOUD_TEMPLATE = "39e257dc-8412-3174-b6b3-0ee3ed6a43e9"

PID = create_project("bench-net-8sw-v4")
print("Project ID:", PID)

spines = {}
for i, name in enumerate(["Spine1", "Spine2"]):
    n = create_node(PID, name, SONIC_TEMPLATE, x=200 + i*300, y=0)
    rename_node(PID, n["node_id"], name)
    spines[name] = n["node_id"]

leaves = {}
leaf_names = ["Leaf1","Leaf2","Leaf3","Leaf4","Leaf5","Leaf6"]
for i, name in enumerate(leaf_names):
    n = create_node(PID, name, SONIC_TEMPLATE, x=i*150, y=300)
    rename_node(PID, n["node_id"], name)
    leaves[name] = n["node_id"]

hosts = {}
for i, name in enumerate(["Host1","Host2","Host3","Host4","Host5","Host6"]):
    n = create_node(PID, name, ALPINE_TEMPLATE, x=i*150, y=500)
    rename_node(PID, n["node_id"], name)
    hosts[name] = n["node_id"]

cloud = create_node(PID, "Cloud-bench", CLOUD_TEMPLATE, x=400, y=-200, compute_id="local")
rename_node(PID, cloud["node_id"], "Cloud-bench")
cloud_id = cloud["node_id"]
print("Cloud node id:", cloud_id)

tap6_mapping = [{"interface": "tap6", "name": "tap6", "port_number": 0, "type": "tap"}]
url = f"{GNS3_BASE_URL}/projects/{PID}/nodes/{cloud_id}"
r = requests.put(url, auth=AUTH, json={"properties": {"ports_mapping": tap6_mapping}}, timeout=30)
r.raise_for_status()
print("Cloud-bench port 0 bound to tap6")

print("Waiting for nodes to settle before wiring links...")
time.sleep(5)

link_count = 0

# mgmt links FIRST — adapter 0 on every switch, reserved for eth0/Cloud
all_switches = list(spines.items()) + list(leaves.items())
for sw_name, sw_id in all_switches:
    create_link(PID, cloud_id, port_a=0, adapter_a=0, node_b=sw_id, port_b=0, adapter_b=0)
    print(f"  mgmt: Cloud-bench(a0/tap6) <-> {sw_name}(a0/eth0)")
    link_count += 1

spine_order = ["Spine1", "Spine2"]

# leaf <-> spine full mesh (12 links): leaf adapters 1,2 for Spine1,Spine2;
# spine adapter = leaf index + 1
for spine_idx, spine_name in enumerate(spine_order):
    for leaf_idx, leaf_name in enumerate(leaf_names):
        create_link(PID, leaves[leaf_name], port_a=0, adapter_a=1+spine_idx,
                     node_b=spines[spine_name], port_b=0, adapter_b=1+leaf_idx)
        print(f"  linked {leaf_name}(a{1+spine_idx}) <-> {spine_name}(a{1+leaf_idx})")
        link_count += 1

# leaf-pair peer links (3 links), adapter 3 on both leaves
pairs = [("Leaf1","Leaf2"), ("Leaf3","Leaf4"), ("Leaf5","Leaf6")]
for a, b in pairs:
    create_link(PID, leaves[a], port_a=0, adapter_a=3, node_b=leaves[b], port_b=0, adapter_b=3)
    print(f"  peer-link {a}(a3) <-> {b}(a3)")
    link_count += 1

# dual-homed hosts (12 links): leaf adapters 4,5 for host-primary/secondary
host_pairing = [
    ("Host1", "Leaf1", 4, "Leaf2", 4),
    ("Host2", "Leaf1", 5, "Leaf2", 5),
    ("Host3", "Leaf3", 4, "Leaf4", 4),
    ("Host4", "Leaf3", 5, "Leaf4", 5),
    ("Host5", "Leaf5", 4, "Leaf6", 4),
    ("Host6", "Leaf5", 5, "Leaf6", 5),
]
for host_name, primary, primary_adapter, secondary, secondary_adapter in host_pairing:
    create_link(PID, hosts[host_name], port_a=0, adapter_a=0,
                node_b=leaves[primary], port_b=0, adapter_b=primary_adapter)
    create_link(PID, hosts[host_name], port_a=0, adapter_a=1,
                node_b=leaves[secondary], port_b=0, adapter_b=secondary_adapter)
    print(f"  {host_name} dual-homed: adapter0->{primary}(a{primary_adapter}), adapter1->{secondary}(a{secondary_adapter})")
    link_count += 2

print(f"\nTopology built. Project ID: {PID}")
print(f"Links created this run: {link_count} (expected 35)")
print("Spines:", spines)
print("Leaves:", leaves)
print("Hosts:", hosts)
print("Cloud:", cloud_id)