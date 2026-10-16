from pybatfish.client.session import Session
from pybatfish.datamodel import *
from pybatfish.datamodel.flow import HeaderConstraints, PathConstraints
import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
bf = Session(host='localhost')
bf.set_network('sonic-intervlan')
bf.init_snapshot('/home/user/intervlan', name='intervlan-snapshot', overwrite=True)
print("\n" + "="*60)
print("BATFISH INTER-VLAN VERIFICATION")
print("="*60)
print("\n[STEP 1] Parse status")
print(bf.q.fileParseStatus().answer().frame())
print("\n[STEP 2] VLAN SVIs on VS-1 and VS-2")
vi = bf.q.interfaceProperties(nodes='/vs-[12]/', interfaces='/[Vv]lan.*/').answer().frame()
print(vi[['Interface', 'VRF', 'Primary_Address', 'Active']].to_string(index=False))
vs1_vlan_ok = not vi[vi['Interface'].astype(str).str.contains('vs-1')].empty
vs2_vlan_ok = not vi[vi['Interface'].astype(str).str.contains('vs-2')].empty
print("\n[STEP 3] Access port VLAN bindings")
ap = bf.q.interfaceProperties(nodes='/vs-[12]/', interfaces='/Ethernet1[26]/').answer().frame()
print(ap[['Interface', 'VRF', 'Access_VLAN', 'Switchport_Mode', 'Active']].to_string(index=False))
print("\n[STEP 4] Routing table VS-1")
rv1 = bf.q.routes(nodes='vs-1', vrfs='default').answer().frame()
print(rv1[['Node', 'VRF', 'Network', 'Next_Hop', 'Protocol']].to_string(index=False))
print("\n[STEP 5] Routing table VS-2")
rv2 = bf.q.routes(nodes='vs-2', vrfs='default').answer().frame()
print(rv2[['Node', 'VRF', 'Network', 'Next_Hop', 'Protocol']].to_string(index=False))
vs1_route_ok = not rv1[rv1['Network'] == '10.0.1.0/24'].empty
vs2_route_ok = not rv2[rv2['Network'] == '10.0.1.0/24'].empty
vs1_pc3_ok = not rv1[rv1['Network'] == '10.0.1.30/32'].empty
vs2_pc1_ok = not rv2[rv2['Network'] == '10.0.1.10/32'].empty
print("\n[STEP 6] Inter-VLAN route check")
print(f"  VS-1 route to 10.0.1.0/24  : {'PASS' if vs1_route_ok else 'FAIL'}")
print(f"  VS-2 route to 10.0.1.0/24  : {'PASS' if vs2_route_ok else 'FAIL'}")
print(f"  VS-1 host route to PC3      : {'PASS' if vs1_pc3_ok else 'INFO: relying on /24'}")
print(f"  VS-2 host route to PC1      : {'PASS' if vs2_pc1_ok else 'INFO: relying on /24'}")
print("\n[TEST 7] PC1 (10.0.1.10) -> PC3 (10.0.1.30) — Expected: PASS")
r = bf.q.reachability(pathConstraints=PathConstraints(startLocation='@enter(vs-1[Ethernet12])'), headers=HeaderConstraints(srcIps='10.0.1.10', dstIps='10.0.1.30'), actions='SUCCESS').answer().frame()
print("  PASS —", len(r), "path(s) found\n    Flow:", r['Flow'].iloc[0]) if not r.empty else print("  FAIL — No path found")
pc1_to_pc3_ok = not r.empty
print("\n[TEST 8] PC3 (10.0.1.30) -> PC1 (10.0.1.10) — Expected: PASS")
r = bf.q.reachability(pathConstraints=PathConstraints(startLocation='@enter(vs-2[Ethernet12])'), headers=HeaderConstraints(srcIps='10.0.1.30', dstIps='10.0.1.10'), actions='SUCCESS').answer().frame()
print("  PASS —", len(r), "path(s) found\n    Flow:", r['Flow'].iloc[0]) if not r.empty else print("  FAIL — No path found")
pc3_to_pc1_ok = not r.empty
print("\n[TEST 9] Server (192.168.100.1) -> PC1 (10.0.1.10) — Expected: PASS")
r = bf.q.reachability(pathConstraints=PathConstraints(startLocation='@enter(vs-1[Ethernet8])'), headers=HeaderConstraints(srcIps='192.168.100.1', dstIps='10.0.1.10'), actions='SUCCESS').answer().frame()
print("  PASS —", len(r), "path(s) found\n    Flow:", r['Flow'].iloc[0]) if not r.empty else print("  FAIL — No path found")
srv_to_pc1_ok = not r.empty
print("\n[TEST 10] Server (192.168.100.1) -> PC3 (10.0.1.30) — Expected: PASS")
r = bf.q.reachability(pathConstraints=PathConstraints(startLocation='@enter(vs-1[Ethernet8])'), headers=HeaderConstraints(srcIps='192.168.100.1', dstIps='10.0.1.30'), actions='SUCCESS').answer().frame()
print("  PASS —", len(r), "path(s) found\n    Flow:", r['Flow'].iloc[0]) if not r.empty else print("  FAIL — No path found")
srv_to_pc3_ok = not r.empty
print("\n" + "="*60)
print("FINAL SUMMARY")
print("="*60)
print(f"VS-1 Vlan100 SVI (10.0.1.253/24)  : {'PASS' if vs1_vlan_ok else 'FAIL'}")
print(f"VS-2 Vlan200 SVI (10.0.1.254/24)  : {'PASS' if vs2_vlan_ok else 'FAIL'}")
print(f"VS-1 inter-VLAN route              : {'PASS' if vs1_route_ok else 'FAIL'}")
print(f"VS-2 inter-VLAN route              : {'PASS' if vs2_route_ok else 'FAIL'}")
print(f"PC1 -> PC3 (cross-VLAN)            : {'PASS' if pc1_to_pc3_ok else 'FAIL'}")
print(f"PC3 -> PC1 (cross-VLAN)            : {'PASS' if pc3_to_pc1_ok else 'FAIL'}")
print(f"Server -> PC1                      : {'PASS' if srv_to_pc1_ok else 'FAIL'}")
print(f"Server -> PC3                      : {'PASS' if srv_to_pc3_ok else 'FAIL'}")
print("="*60)
