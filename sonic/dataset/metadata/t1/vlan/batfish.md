---

type : vlan-batfish-example

title : Verify VLAN 100 SVI and membership on leaves, absent on spines

description : Verifies parse status, VLAN 100 SVI addressing, and VLAN membership on VS-1/VS-2 leaves, and confirms VLAN 100 is absent from VS-3/VS-4 spines.

resource : data/t1/vlan/batfish/batfish.py


tags : [vlan, svi]


category : vlan

role : batfish


topology : t1


paired_with : metadata/t1/vlan/ansible.md

topology_path : metadata/t1/index.md

---

## What it verifies

- All four device configs (VS-1–VS-4) parse cleanly.
- The VLAN 100 SVI on VS-1/VS-2 exists, is active, and carries the correct IP.
- VLAN 100 membership on VS-1/VS-2 (i.e. `Ethernet4` as untagged member).
- Switchport mode and access VLAN detail on VS-1/VS-2 interfaces.
- That VLAN 100 does **not** exist on VS-3/VS-4 (spines), by dumping VLAN membership across all four nodes for comparison.

## PyBatfish Queries used

- `bf.q.fileParseStatus()` — checks all four configs parsed without errors.
- `bf.q.interfaceProperties()` — used twice: (1) filtered to `Vlan*` interfaces on `vs-1`/`vs-2` to check SVI state/address/switchport; (2) filtered to `vs-1`/`vs-2` again to pull `Switchport_Mode`/`Access_VLAN`/`Active` per interface.
- `bf.q.switchedVlanProperties()` — used twice: (1) filtered to `vs-1`/`vs-2` for VLAN membership; (2) unfiltered, across all four nodes, as a sanity check that VLAN 100 doesn't appear on the spines.

## check() assertions

None. The script contains **no** `assert` or PyBatfish `check_df_out`/assertion calls — every query result is simply printed as a pandas DataFrame (`print(...)`) for manual/visual review. There is no automated pass/fail logic in the code itself.

## Pass Condition

*(Inferred from the printed data — not enforced by the script.)* Considered passing if, on inspection:
- `fileParseStatus()` shows no `PARSE_WARNING`/`PARSE_ERROR` for any of the four nodes.
- `Vlan100` on VS-1 shows `Active=True`, `Primary_Address=10.0.1.1/24`; on VS-2 shows `Active=True`, `Primary_Address=10.0.2.1/24`.
- `switchedVlanProperties` shows `Ethernet4` as an untagged member of VLAN 100 on both VS-1 and VS-2.
- `interfaceProperties` shows `Ethernet4` with `Switchport_Mode=ACCESS`, `Access_VLAN=100` on both leaves.
- `switchedVlanProperties` across all nodes shows no VLAN 100 entry for VS-3/VS-4.

## Fail condition

*(Also inferred, same caveat.)* Any of: parse errors/warnings on a node; missing or incorrect `Vlan100` SVI address/state on VS-1/VS-2; `Ethernet4` not shown as an untagged VLAN 100 member; wrong switchport mode/access VLAN; or VLAN 100 appearing on VS-3/VS-4.
