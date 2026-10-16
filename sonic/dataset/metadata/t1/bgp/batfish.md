---

type : bgp-batfish-example

title : Verify eBGP peering, session establishment, and route propagation across leaf-spine t1 topology

description : Runs PyBatfish queries against the t1 leaf-spine snapshot to confirm configs parse cleanly, BGP process/peer configuration matches intent, sessions are compatible and established, and BGP-learned routes appear in the RIB.

resource : data/t1/bgp/batfish/batfish.py

tags : [bgp, ebgp, session-status, rib]

category : bgp

role : batfish

topology : t1

body-secion : Loads the t1 snapshot and runs seven PyBatfish queries in sequence — parse status, Layer 3 edges, BGP process configuration, BGP peer configuration, BGP session compatibility, BGP session status, and BGP RIB — printing each result frame to validate the eBGP design across VS-1, VS-2, VS-3, and VS-4 before/after config push.

paired_with : metadata/t1/bgp/ansible.md

---

## What it verifies

- All four device configs (VS-1, VS-2, VS-3, VS-4) parse cleanly in Batfish with no errors, confirming the SONiC `show running-config` exports were converted to valid Batfish-readable configs.
- The physical/Layer 3 topology Batfish infers from the configs matches the intended point-to-point links: VS-1↔VS-3, VS-1↔VS-4, VS-2↔VS-3, VS-2↔VS-4.
- Each switch's BGP process is configured with the correct router ID, VRF, and neighbor set.
- Each BGP peer's local/remote AS numbers, local/remote IPs, and neighbor descriptions match what was pushed via Ansible (e.g. VS-1 as AS 65001 peering with VS-3/VS-4 at AS 65000).
- BGP sessions are config-level compatible — AS numbers and IP addressing line up on both ends of each session — before checking runtime status.
- BGP sessions actually establish at runtime (not just configured correctly on paper).
- Routes learned via BGP (e.g. VS-1's advertised loopback `10.1.0.1/32`) show up correctly in the BGP RIB of the receiving switches.

## PyBatfish Queries used

- `bf.q.fileParseStatus()` — reports parse status per config file, flags any that failed or partially parsed.
- `bf.q.layer3Edges()` — returns the Layer 3 adjacency Batfish derived from interface IPs/subnets across all four switches.
- `bf.q.bgpProcessConfiguration()` — returns per-node, per-VRF BGP process details; filtered to `Node`, `VRF`, `Router_ID`, `Neighbors`.
- `bf.q.bgpPeerConfiguration()` — returns per-peer configuration; filtered to `Node`, `VRF`, `Local_AS`, `Remote_AS`, `Local_IP`, `Remote_IP`, `Description`.
- `bf.q.bgpSessionCompatibility()` — config-level check of whether each configured session's AS/IP pairing is mutually consistent; filtered to `Node`, `VRF`, `Remote_AS`, `Remote_IP`, `Configured_Status`.
- `bf.q.bgpSessionStatus()` — runtime-oriented check of whether each session is established; filtered to `Node`, `VRF`, `Remote_AS`, `Remote_IP`, `Established_Status`.
- `bf.q.bgpRib()` — dumps the full BGP RIB across all nodes, showing routes learned via BGP and their attributes.

## check() assertions

- No explicit `bf.q.*().answer().frame()` assertions/`assert` statements are present in this script — every query's result is printed via `print()` for manual/visual inspection rather than programmatically checked.
- Intended manual checks per printed frame: `fileParseStatus` shows no `FAILED`/`PARTIAL` entries; `layer3Edges` contains all four expected links; `bgpProcessConfiguration`/`bgpPeerConfiguration` match the AS numbers, router IDs, and neighbor IPs pushed in the Ansible play; `bgpSessionCompatibility.Configured_Status` reads `UNIQUE_MATCH` (or equivalent compatible status) for every session; `bgpSessionStatus.Established_Status` reads `ESTABLISHED` for every session; `bgpRib` contains an entry for `10.1.0.1/32` on VS-3, VS-4, and VS-2 (learned from VS-1 via the spines).

## Pass Condition

- Every file in `fileParseStatus` parses with no failures.
- `layer3Edges` reflects all four intended point-to-point links with no missing or unexpected adjacencies.
- `bgpProcessConfiguration` and `bgpPeerConfiguration` match the intended design: correct ASNs (65000 spines, 65001 VS-1, 65002 VS-2), correct router IDs, and correct neighbor IP/description pairs.
- `bgpSessionCompatibility.Configured_Status` shows a compatible status for all four sessions (VS-1↔VS-3, VS-1↔VS-4, VS-2↔VS-3, VS-2↔VS-4).
- `bgpSessionStatus.Established_Status` shows `ESTABLISHED` for all four sessions.
- `bgpRib` shows `10.1.0.1/32` present on VS-3, VS-4, and VS-2 with a valid BGP path, confirming end-to-end advertisement from VS-1.

## Fail condition

- Any file in `fileParseStatus` shows `FAILED` or `PARTIAL`, indicating a config conversion or syntax issue.
- `layer3Edges` is missing an expected link or shows an unexpected one, indicating a topology/IP addressing mismatch.
- `bgpProcessConfiguration`/`bgpPeerConfiguration` shows a mismatched ASN, router ID, or neighbor IP versus what Ansible pushed.
- `bgpSessionCompatibility.Configured_Status` shows an incompatible/mismatch status (e.g. AS or IP mismatch) for any session.
- `bgpSessionStatus.Established_Status` shows `NOT_ESTABLISHED` (or any non-established state) for any session, indicating the peering didn't come up at runtime even though config looked compatible.
- `bgpRib` does not contain `10.1.0.1/32` on VS-3, VS-4, or VS-2, indicating the advertised prefix failed to propagate through the spines.
