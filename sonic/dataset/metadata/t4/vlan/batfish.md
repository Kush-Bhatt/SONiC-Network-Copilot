---

type : vlan-batfish-example

title : Verify VLAN10 membership, static routing, and end-to-end reachability across a 3-switch hybrid SONiC topology

description : Runs PyBatfish queries against a 3-switch hybrid SONiC snapshot (VLAN 10 on VS-1/VS-2, routed VS-3) to confirm configs parse without warnings, VLAN/interface properties and static routes are correctly installed, and fourteen data-plane reachability paths succeed across VLAN gateways, VLAN hosts, and the external network.

resource : data/t4/vlan/batfish/batfish_vlan_hybrid_verify.py

tags : [vlan, static-route, reachability, portchannel]

category : vlan

role : batfish

topology : t4

paired_with : metadata/t4/vlan/ansible.md

topology_path : metadata/t4/index.md

---

## Scenario

This script verifies a 3-switch hybrid SONiC topology loaded from a snapshot at `/home/user/vlan_hybrid_snapshot` (network `sonic-vlan-lab`, snapshot `sonic-vlan-hybrid-v1`) containing a `layer1_topology.json` and per-switch configs (`VS-1.cfg`, `VS-2.cfg`, `VS-3.cfg`). VS-1 and VS-2 each run VLAN 10 with their own SVI gateway (`10.0.1.253/24` on VS-1, `10.0.1.254/24` on VS-2), each with two untagged host ports (`Ethernet12`, `Ethernet16`). VS-3 carries no VLAN and instead has a routed external-facing interface (`Ethernet16` → `192.168.200.2/24`). All three switches are interconnected via PortChannels — `PortChannel12` (VS-1↔VS-2, `10.0.12.0/30`), `PortChannel13` (VS-1↔VS-3, `10.0.13.0/30`), and `PortChannel23` (VS-2↔VS-3, `10.0.23.0/30`) — and routing is entirely static (no BGP), with each switch carrying host-specific `/32` routes toward the far side plus a default route on VS-1/VS-2 pointing at VS-3. As in the paired BGP-verification script, this one defines a `to_df()` helper (`.frame()` → `.as_frame()` → `pd.DataFrame(list(answer))` fallback) and a `check_reach()` helper wrapping `bf.q.reachability()` in a try/except, printing flow/trace/action columns, a "no successful paths found" message, or the exception itself. Pandas display options are widened (`max_columns=None`, `width=200`, `max_colwidth=80`) so wide frames aren't truncated.

## PyBatfish Queries used

- `bf.q.parseWarning()` — checks for parse warnings across the snapshot; prints a clean "no parse warnings" message if empty, otherwise the full warning frame.
- `bf.q.interfaceProperties(properties="Interface, Primary_Address, Active, Switchport, Switchport_Mode, Access_VLAN, Allowed_VLANs")` — returns interface properties across all nodes, then filtered in Python to rows whose `Interface` name contains `Vlan`, `PortChannel`, `Ethernet12`, `Ethernet16`, or `Loopback` (to avoid printing all 32 ports).
- `bf.q.switchedVlanProperties()` — returns VLAN membership across all nodes, no filter, full frame printed; used to confirm VLAN 10 exists on VS-1 and VS-2 with the correct member ports.
- `bf.q.routes()` — the full main RIB per node; filtered to `Node`, `VRF`, `Network`, `Protocol`, `Next_Hop_IP`, `Metric`, used to confirm all static routes installed correctly on all three switches.
- The same `routes()` result, re-filtered in Python to rows where `Network` starts with `"10.0.1."`, to specifically verify VLAN 10 subnet and host routes are present everywhere.
- `bf.q.layer3Edges()` — returns Layer 3 adjacency; filtered to `Interface`, `Remote_Interface`, `IPs`, `Remote_IPs`, used to confirm the PortChannel links between all three switches.
- `bf.q.reachability(pathConstraints=PathConstraints(startLocation=...), headers=HeaderConstraints(srcIps=..., dstIps=...), actions="SUCCESS")` — run fourteen times via `check_reach()`, covering:
  - `[7a]` VS-1 gateway (`10.0.1.253`) → VS-2 gateway (`10.0.1.254`)
  - `[7b]` VS-2 gateway (`10.0.1.254`) → VS-1 gateway (`10.0.1.253`)
  - `[7c]` VS-1 host (`10.0.1.10`) → VS-2 host (`10.0.1.11`)
  - `[7d]` VS-1 host (`10.0.1.20`) → VS-2 host (`10.0.1.30`)
  - `[7e]` VS-2 host (`10.0.1.11`) → VS-1 host (`10.0.1.10`)
  - `[7f]` VS-2 host (`10.0.1.30`) → VS-1 host (`10.0.1.20`)
  - `[7g]` VS-1 host (`10.0.1.10`) → VS-3 external (`192.168.200.1`)
  - `[7h]` VS-2 host (`10.0.1.11`) → VS-3 external (`192.168.200.1`)
  - `[7i]` VS-3 (`192.168.200.2`) → VS-1 host (`10.0.1.10`) via PortChannel13
  - `[7j]` VS-3 (`192.168.200.2`) → VS-1 host (`10.0.1.20`) via PortChannel13
  - `[7k]` VS-3 (`192.168.200.2`) → VS-2 host (`10.0.1.11`) via PortChannel23
  - `[7l]` VS-3 (`192.168.200.2`) → VS-2 host (`10.0.1.30`) via PortChannel23
  - `[7m]` VS-3 (`192.168.200.2`) → VS-1 Vlan10 gateway (`10.0.1.253`)
  - `[7n]` VS-3 (`192.168.200.2`) → VS-2 Vlan10 gateway (`10.0.1.254`)

## check() assertions

- No explicit `assert` statements are present in this script — results are printed via `print()` for manual/visual inspection, with light conditional logic (`if ifaces.empty`, `if vlan_routes.empty`, `if r.empty`) used to choose an "empty/no data" message versus printing the frame, not to fail the script.
- Intended manual checks per printed section: `[1]` shows no parse warnings; `[2]` shows correct `Primary_Address`, `Active`, `Switchport_Mode`, and `Access_VLAN`/`Allowed_VLANs` on the `Vlan10`, `PortChannel*`, `Ethernet12`/`Ethernet16`, and loopback interfaces on all three nodes; `[3]` shows VLAN 10 present on VS-1 and VS-2 with `Ethernet12` and `Ethernet16` as members on each, and absent on VS-3; `[4]` shows every static route listed in the topology comment block installed with `Protocol = static` and the correct `Next_Hop_IP`; `[5]` shows `10.0.1.x` routes present across all three nodes (VS-1, VS-2, VS-3); `[6]` shows the three PortChannel adjacencies (VS-1↔VS-2, VS-1↔VS-3, VS-2↔VS-3) with matching local/remote IPs; `[7a]`–`[7n]` each show at least one successful flow/trace for the specified src→dst pair.

## Pass Condition

- `parseWarning()` returns an empty frame (no warnings) for all three switch configs.
- `interfaceProperties()` shows correct `Primary_Address`/`Active`/`Switchport_Mode`/`Access_VLAN` for `Vlan10` (VS-1, VS-2), all PortChannels, `Ethernet12`/`Ethernet16` on VS-1/VS-2, and VS-3's routed `Ethernet16`.
- `switchedVlanProperties()` shows VLAN 10 with `Ethernet12` and `Ethernet16` as members on both VS-1 and VS-2, and no VLAN 10 entries on VS-3.
- `routes()` shows every static route from the topology (default routes, host `/32` routes, VLAN gateway routes) installed on the correct node with the correct `Next_Hop_IP`.
- The `10.0.1.x`-filtered route check in `[5]` returns a non-empty frame with rows on all three nodes.
- `layer3Edges()` shows all three PortChannel adjacencies (VS-1↔VS-2, VS-1↔VS-3, VS-2↔VS-3) with correct local/remote IPs.
- All fourteen `check_reach()` calls (`[7a]`–`[7n]`) return at least one successful flow/trace.

## Fail condition

- `parseWarning()` returns a non-empty frame, indicating a config parsing issue on one or more switches.
- `interfaceProperties()` shows a missing, inactive, or incorrectly addressed `Vlan10`, PortChannel, or host-facing interface on any node.
- `switchedVlanProperties()` is missing `Ethernet12` or `Ethernet16` from VLAN 10 on VS-1 or VS-2, or shows VLAN 10 present on VS-3.
- `routes()` is missing an expected static route on any node, or shows an incorrect `Next_Hop_IP`.
- The `[5]` `10.0.1.x` route check returns an empty frame or is missing rows on one of the three nodes, suggesting static routes weren't parsed or applied correctly.
- `layer3Edges()` is missing one of the three PortChannel adjacencies or shows mismatched IPs, indicating a link/addressing problem.
- Any of `[7a]`–`[7n]` prints "No successful paths found" or raises/prints a query failure, indicating the data plane doesn't support that specific traffic path despite the static routes appearing correctly configured.
