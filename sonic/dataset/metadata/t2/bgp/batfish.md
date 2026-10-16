---

type : bgp-batfish-example

title : Verify eBGP peering, session establishment, and loopback route propagation across ring topology

description : Runs PyBatfish queries against the t2 ring-topology snapshot to confirm configs parse cleanly, BGP process/peer configuration matches intent, sessions are compatible and established, and VS-4's advertised loopback appears in the BGP RIB of the other nodes.

resource : data/t2/bgp/batfish/batfish.py

tags : [bgp, ebgp, ring, session-status, rib]

category : bgp

role : batfish

topology : t2

paired_with : metadata/t2/bgp/ansible.md

topology_path : metadata/t2/index.md

---

## What it verifies

- All four device configs (VS-1, VS-2, VS-3, VS-4) parse cleanly in Batfish with no errors, confirming the SONiC `show running-config` exports were converted to valid Batfish-readable configs.
- The physical/Layer 3 topology Batfish infers from the configs matches the intended ring links: VS-1↔VS-2, VS-1↔VS-3, VS-2↔VS-4, VS-3↔VS-4.
- Each switch's BGP process is configured with the correct router ID, VRF, and neighbor set for its two ring neighbors.
- Each BGP peer's local/remote AS numbers, local/remote IPs, and neighbor descriptions match what was pushed via Ansible (e.g. VS-1 as AS 65001 peering with VS-2 at AS 65002 and VS-3 at AS 65003).
- BGP sessions are config-level compatible — AS numbers and IP addressing line up on both ends of each ring link — before checking runtime status.
- BGP sessions actually establish at runtime across all four ring links (not just configured correctly on paper).
- Routes learned via BGP — specifically VS-4's advertised loopback `10.4.4.4/32` — show up correctly in the BGP RIB of VS-1, VS-2, and VS-3.

## PyBatfish Queries used

- `bf.q.fileParseStatus()` — reports parse status per config file, flags any that failed or partially parsed.
- `bf.q.layer3Edges()` — returns the Layer 3 adjacency Batfish derived from interface IPs/subnets across all four switches; BGP session correctness depends on this being accurate.
- `bf.q.bgpProcessConfiguration()` — returns per-node, per-VRF BGP process details; filtered to `Node`, `VRF`, `Router_ID`, `Neighbors`.
- `bf.q.bgpPeerConfiguration()` — returns per-peer configuration; filtered to `Node`, `VRF`, `Local_AS`, `Remote_AS`, `Local_IP`, `Remote_IP`, `Description`.
- `bf.q.bgpSessionCompatibility()` — config-level check of whether each configured session's AS/IP pairing is mutually consistent; filtered to `Node`, `VRF`, `Remote_AS`, `Remote_IP`, `Configured_Status`.
- `bf.q.bgpSessionStatus()` — the runtime check of whether each ring session is actually established; filtered to `Node`, `VRF`, `Remote_AS`, `Remote_IP`, `Established_Status`.
- `bf.q.bgpRib()` — dumps the full BGP RIB across all nodes, showing routes learned via BGP and their attributes.

## check() assertions

- No explicit `bf.q.*().answer().frame()` assertions/`assert` statements are present in this script — every query's result is printed via `print()` for manual/visual inspection rather than programmatically checked.
- Intended manual checks per printed frame: `fileParseStatus` shows no `FAILED`/`PARTIAL` entries; `layer3Edges` contains all four expected ring links; `bgpProcessConfiguration`/`bgpPeerConfiguration` match the AS numbers, router IDs, and neighbor IPs pushed in the Ansible play; `bgpSessionCompatibility.Configured_Status` reads `UNIQUE_MATCH` (or equivalent compatible status) for every session; `bgpSessionStatus.Established_Status` reads `ESTABLISHED` for every session; `bgpRib` contains an entry for `10.4.4.4/32` on VS-1, VS-2, and VS-3 (learned from VS-4 around the ring).

## Pass Condition

- Every file in `fileParseStatus` parses with no failures.
- `layer3Edges` reflects all four intended ring links (VS-1↔VS-2, VS-1↔VS-3, VS-2↔VS-4, VS-3↔VS-4) with no missing or unexpected adjacencies.
- `bgpProcessConfiguration` and `bgpPeerConfiguration` match the intended design: correct ASNs (65001 VS-1, 65002 VS-2, 65003 VS-3, 65004 VS-4), correct router IDs, and correct neighbor IP/description pairs.
- `bgpSessionCompatibility.Configured_Status` shows a compatible status for all four ring sessions.
- `bgpSessionStatus.Established_Status` shows `ESTABLISHED` for all four sessions.
- `bgpRib` shows `10.4.4.4/32` present on VS-1, VS-2, and VS-3 with a valid BGP path, confirming propagation of VS-4's loopback around the ring.

## Fail condition

- Any file in `fileParseStatus` shows `FAILED` or `PARTIAL`, indicating a config conversion or syntax issue.
- `layer3Edges` is missing an expected ring link or shows an unexpected one, indicating a topology/IP addressing mismatch.
- `bgpProcessConfiguration`/`bgpPeerConfiguration` shows a mismatched ASN, router ID, or neighbor IP versus what Ansible pushed.
- `bgpSessionCompatibility.Configured_Status` shows an incompatible/mismatch status (e.g. AS or IP mismatch) for any ring session.
- `bgpSessionStatus.Established_Status` shows `NOT_ESTABLISHED` (or any non-established state) for any session, indicating a ring peering didn't come up at runtime even though config looked compatible.
- `bgpRib` does not contain `10.4.4.4/32` on VS-1, VS-2, or VS-3, indicating the advertised loopback failed to propagate around the ring.
