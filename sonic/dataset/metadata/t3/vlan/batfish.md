---

type : vlan-batfish-example

title : Verify VLAN10 SVI and access port membership on single switch VS-1

description : Runs PyBatfish queries against the t3 access-switch snapshot to confirm the VLAN10 SVI exists and is active on VS-1, all five ports report correct switchport/access-VLAN membership, and interface-level details cross-check against the VLAN membership view.

resource : data/t3/vlan/batfish/batfish.py

tags : [vlan, svi, access, switchport]

category : vlan

role : batfish

topology : t3

paired_with : metadata/t3/vlan/ansible.md

topology_path : metadata/t3/index.md

---

## What it verifies

- The `Vlan10` SVI exists on VS-1, is active, and holds the correct primary address, confirming the Layer 3 interface for VLAN 10 was created as intended.
- All five configured ports (`Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24`) on VS-1 are correctly switched into VLAN 10, confirming the untagged (access) member assignments took effect.
- Each of the five ports reports the correct switchport mode and access VLAN ID (10), and is active, cross-checked directly via interface properties as a second, independent view of the same membership data.

## PyBatfish Queries used

- `bf.q.interfaceProperties(nodes="VS-1", interfaces="Vlan10")` — returns properties of the `Vlan10` SVI on VS-1; filtered to `Interface`, `Active`, `Primary_Address`, `Switchport`. (Note: this script does not include a `fileParseStatus()`/`parseStatus()` call, unlike the other examples.)
- `bf.q.switchedVlanProperties(nodes="VS-1")` — returns VLAN membership details for switched interfaces on VS-1; no column filter, full frame printed.
- `bf.q.interfaceProperties(nodes="VS-1")` — returns properties of all interfaces on VS-1; filtered to `Interface`, `Switchport_Mode`, `Access_VLAN`, `Active`. Used as a cross-check against the `switchedVlanProperties` view.

## check() assertions

- No explicit `bf.q.*().answer().frame()` assertions/`assert` statements are present in this script — every query's result is printed via `print()` for manual/visual inspection rather than programmatically checked.
- Intended manual checks per printed frame: the VLAN10 SVI frame shows `Active = True` and `Primary_Address = 192.168.10.1/24`; `switchedVlanProperties(nodes="VS-1")` lists all five ports (`Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24`) under VLAN 10; `interfaceProperties(nodes="VS-1")` shows `Access_VLAN = 10` and `Active = True` for all five ports, matching the membership view from the prior query.

## Pass Condition

- The `Vlan10` SVI on VS-1 is `Active`, has primary address `192.168.10.1/24`, and reflects a routed/SVI switchport type.
- `switchedVlanProperties(nodes="VS-1")` shows all five ports — `Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24` — as members of VLAN 10.
- `interfaceProperties(nodes="VS-1")` shows `Access_VLAN = 10` for all five ports, each with `Active = True`, consistent with the `switchedVlanProperties` output.

## Fail condition

- The `Vlan10` SVI on VS-1 is missing, inactive, or shows an incorrect/missing primary address.
- `switchedVlanProperties(nodes="VS-1")` is missing one or more of the five ports from VLAN 10, indicating a member assignment failed to apply (worth noting: this could point back to the retry logic in the paired Ansible playbook, if VLAN10 already existed with a different port set).
- `interfaceProperties(nodes="VS-1")` shows an incorrect `Access_VLAN` (not 10) or `Active = False` for any of the five ports, or disagrees with the `switchedVlanProperties` frame for the same port.
