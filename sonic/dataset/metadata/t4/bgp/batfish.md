---

type : bgp-batfish-example

title : Verify BGP session establishment, RIB propagation, and end-to-end reachability across a 3-switch SONiC topology

description : Runs PyBatfish queries against a 3-switch SONiC/FRR snapshot to confirm configs parse without warnings, BGP sessions are compatible and established, routes propagate into the BGP RIB and main RIB, and specific cross-switch data-plane reachability succeeds in both directions.

resource : data/t4/bgp/batfish/batfish_bgp_verify.py

tags : [bgp, session-status, rib, reachability]

category : bgp

role : batfish

topology : t4

paired_with : metadata/t4/bgp/ansible.md

topology_path : metadata/t4/index.md

---

## Scenario

This script verifies a 3-switch SONiC topology (VS-1, VS-2, VS-3) running FRR-based BGP, loaded from a snapshot at `/home/user/bgp_frrsnapshotnoans` (network `sonic-lab`, snapshot `sonic-frr-v6`) containing a `layer1_topology.json` and per-switch configs (`VS-1.cfg`, `VS-2.cfg`, `VS-3.cfg`). Unlike the other batfish scripts covered so far, this one defines a `to_df()` helper that tries `.frame()`, then `.as_frame()`, then falls back to `pd.DataFrame(list(answer))`, so it works across different PyBatfish answer object types without crashing. It also defines a `check_reach()` helper wrapping `bf.q.reachability()` in a try/except, printing either the resulting flow/trace/action columns, a "no successful paths found" message, or the exception itself if the query fails outright. Pandas display options are widened (`max_columns=None`, `width=200`, `max_colwidth=80`) so wide result frames aren't truncated in output.

## PyBatfish Queries used

- `bf.q.parseWarning()` — checks for parse warnings across the snapshot; prints a clean "no parse warnings" message if the result is empty, otherwise prints the full warning frame.
- `bf.q.bgpSessionCompatibility()` — config-level compatibility check across all BGP sessions; filtered to `Node`, `VRF`, `Local_IP`, `Remote_IP`, `Local_AS`, `Remote_AS`, `Configured_Status`.
- `bf.q.bgpSessionStatus()` — runtime check of whether each BGP session actually established; filtered to `Node`, `VRF`, `Local_AS`, `Local_IP`, `Remote_AS`, `Remote_IP`, `Established_Status`.
- `bf.q.bgpRib()` — routes learned via BGP per node; filtered to `Node`, `VRF`, `Network`, `Next_Hop_IP`, `AS_Path`, `Origin_Type`.
- `bf.q.routes()` — the full main RIB (all protocols, not just BGP) per node; filtered to `Node`, `VRF`, `Network`, `Protocol`, `Next_Hop_IP`, `Metric`.
- `bf.q.reachability(pathConstraints=PathConstraints(startLocation=...), headers=HeaderConstraints(srcIps=..., dstIps=...), actions="SUCCESS")` — run twice via the `check_reach()` helper:
  - VS-2 (`@enter(VS-2[Vlan10])`, src `10.0.1.254`) → dst `192.168.200.1` (VS-3 side)
  - VS-3 (`@enter(VS-3[Ethernet16])`, src `192.168.200.2`) → dst `10.0.1.254` (VS-2's `Vlan10`)

## check() assertions

- No explicit `assert` statements are present in this script — results are printed via `print()` for manual/visual inspection, with light conditional logic (`if w.empty`, `if r.empty`) used only to choose a "clean/empty" message versus printing the frame, not to fail the script.
- Intended manual checks per printed section: `[1]` shows no parse warnings; `[2]` shows a compatible `Configured_Status` for every session; `[3]` shows `Established_Status = ESTABLISHED` for every session; `[4]` shows expected prefixes present in the BGP RIB per node with sane `AS_Path`/`Next_Hop_IP` values; `[5]` shows those same prefixes reflected in the main RIB with the correct `Protocol` (e.g. `bgp`); `[6]`/`[7]` each show at least one successful flow/trace for the specified src→dst pair in both directions.

## Pass Condition

- `parseWarning()` returns an empty frame (no warnings) for all three switch configs.
- `bgpSessionCompatibility()` shows a compatible `Configured_Status` for every BGP session across VS-1, VS-2, and VS-3.
- `bgpSessionStatus()` shows `Established_Status = ESTABLISHED` for every session.
- `bgpRib()` shows the expected learned prefixes present on the appropriate nodes.
- `routes()` (main RIB) reflects those same BGP-learned prefixes with the correct next-hop and protocol.
- The `[6]` reachability check (VS-2 `10.0.1.254` → `192.168.200.1`) returns at least one successful flow/trace.
- The `[7]` reachability check (VS-3 `192.168.200.2` → `10.0.1.254` on VS-2's `Vlan10`) returns at least one successful flow/trace.

## Fail condition

- `parseWarning()` returns a non-empty frame, indicating a config parsing issue on one or more switches.
- `bgpSessionCompatibility()` shows an incompatible/mismatched status for any session, indicating an AS or IP addressing mismatch.
- `bgpSessionStatus()` shows any session as `NOT_ESTABLISHED` (or any non-established state), indicating a peering that didn't come up at runtime.
- `bgpRib()` is missing an expected prefix on a node, indicating BGP-learned routes failed to propagate as intended.
- `routes()` doesn't reflect the expected BGP-learned prefixes in the main RIB, indicating a route wasn't installed even though it was learned via BGP.
- Either `check_reach()` call in `[6]` or `[7]` prints "No successful paths found" or raises/prints a query failure, indicating the data plane doesn't actually support the intended cross-switch traffic path despite BGP appearing healthy.
