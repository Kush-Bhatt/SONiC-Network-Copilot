---

type : vlan-batfish-example

title : Verify VLAN10 SVI, access port membership, and VLAN scoping on access switch VS-4

description : Runs PyBatfish queries against the t2 access-switch snapshot to confirm configs parse cleanly, the VLAN10 SVI exists and is active on VS-4, both access ports are correctly assigned to VLAN10, and VLAN10 is correctly scoped only to VS-4 and absent elsewhere.

resource : data/t2/vlan/batfish/batfish.py

tags : [vlan, svi, access, switchport]

category : vlan

role : batfish

topology : t2

paired_with : metadata/t2/vlan/ansible.md

topology_path : metadata/t2/index.md

---

## What it verifies

- All four device configs (VS-1, VS-2, VS-3, VS-4) parse cleanly in Batfish with no errors, confirming the SONiC `show running-config` exports were converted to valid Batfish-readable configs.
- The `Vlan10` SVI exists on VS-4, is active, and holds the correct primary address, confirming the Layer 3 interface for VLAN 10 was created as intended.
- `Ethernet4` and `Ethernet8` on VS-4 are correctly switched into VLAN 10, confirming the untagged (access) member assignments took effect.
- Both access ports on VS-4 report the correct switchport mode and access VLAN ID (10), and are active.
- VLAN 10 does not exist on VS-1, VS-2, or VS-3, confirming the VLAN is correctly scoped only to the access switch VS-4 and wasn't accidentally propagated elsewhere.

## PyBatfish Queries used

- `bf.q.fileParseStatus()` — reports parse status per config file, flags any that failed or partially parsed.
- `bf.q.interfaceProperties(nodes="VS-4", interfaces="Vlan10")` — returns properties of the `Vlan10` SVI on VS-4; filtered to `Interface`, `Active`, `Primary_Address`, `Switchport`.
- `bf.q.switchedVlanProperties(nodes="VS-4")` — returns VLAN membership details for switched interfaces on VS-4; no column filter, full frame printed.
- `bf.q.interfaceProperties(nodes="VS-4")` — returns properties of all interfaces on VS-4; filtered to `Interface`, `Switchport_Mode`, `Access_VLAN`, `Active`.
- `bf.q.switchedVlanProperties()` — returns VLAN membership details across **all** nodes (no `nodes` filter), used as a sanity check that VLAN 10 doesn't appear on VS-1, VS-2, or VS-3; no column filter, full frame printed.

## check() assertions

- No explicit `bf.q.*().answer().frame()` assertions/`assert` statements are present in this script — every query's result is printed via `print()` for manual/visual inspection rather than programmatically checked.
- Intended manual checks per printed frame: `fileParseStatus` shows no `FAILED`/`PARTIAL` entries; the VLAN10 SVI frame shows `Active = True`, `Primary_Address = 10.0.2.1/24`, and `Switchport` reflecting an SVI; `switchedVlanProperties(nodes="VS-4")` lists both `Ethernet4` and `Ethernet8` under VLAN 10; `interfaceProperties(nodes="VS-4")` shows `Access_VLAN = 10` and `Active = True` for both ports; the全-switch `switchedVlanProperties()` frame shows VLAN 10 entries only for VS-4, with no rows for VS-1, VS-2, or VS-3.

## Pass Condition

- Every file in `fileParseStatus` parses with no failures.
- The `Vlan10` SVI on VS-4 is `Active`, has primary address `10.0.2.1/24`, and reflects a routed/SVI switchport type.
- `switchedVlanProperties(nodes="VS-4")` shows both `Ethernet4` and `Ethernet8` as members of VLAN 10.
- `interfaceProperties(nodes="VS-4")` shows `Access_VLAN = 10` for both `Ethernet4` and `Ethernet8`, both `Active = True`.
- The all-switch `switchedVlanProperties()` query shows VLAN 10 present only on VS-4, with no VLAN 10 entries on VS-1, VS-2, or VS-3.

## Fail condition

- Any file in `fileParseStatus` shows `FAILED` or `PARTIAL`, indicating a config conversion or syntax issue.
- The `Vlan10` SVI on VS-4 is missing, inactive, or shows an incorrect/missing primary address.
- `switchedVlanProperties(nodes="VS-4")` is missing `Ethernet4` or `Ethernet8` from VLAN 10, indicating a member assignment failed to apply.
- `interfaceProperties(nodes="VS-4")` shows an incorrect `Access_VLAN` (not 10) or `Active = False` for either access port.
- The all-switch `switchedVlanProperties()` query shows VLAN 10 leaking onto VS-1, VS-2, or VS-3, indicating incorrect scoping or an unintended config push.
