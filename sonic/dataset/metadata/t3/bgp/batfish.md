---

type : bgp-batfish-example

title : Verify BGP process and peer configuration on single switch VS-1

description : Runs PyBatfish queries against the t3 single-switch snapshot to confirm the config parses cleanly, the BGP process is configured with the correct AS number and router ID, and the neighbor/peer is configured correctly.

resource : data/t3/bgp/batfish/batfish.py

tags : [bgp, ebgp, single-switch]

category : bgp

role : batfish

topology : t3

paired_with : metadata/t3/bgp/ansible.md

topology_path : metadata/t3/index.md

---

## What it verifies

- The VS-1 config parses cleanly in Batfish with no errors, confirming the SONiC `show running-config` export was converted to a valid Batfish-readable config.
- A BGP process exists on VS-1 with the correct AS number (65001) and router ID (`192.168.100.2`).
- The BGP neighbor/peer is configured correctly on VS-1 — remote AS, remote IP, and description matching what was pushed via Ansible (neighbor `10.0.0.1`, remote-as 65002, description `to-vs-3`).

## PyBatfish Queries used

- `bf.q.parseStatus()` — reports parse status of the loaded config, flags any that failed or partially parsed. (Note: this script uses `parseStatus()`, not `fileParseStatus()` as in the other examples.)
- `bf.q.bgpProcessConfiguration(nodes="VS-1")` — returns BGP process details for VS-1; no column filter, full frame printed.
- `bf.q.bgpPeerConfiguration(nodes="VS-1")` — returns per-peer configuration for VS-1; filtered to `Node`, `VRF`, `Local_AS`, `Remote_AS`, `Remote_IP`, `Description`.

## check() assertions

- No explicit `bf.q.*().answer().frame()` assertions/`assert` statements are present in this script — every query's result is printed via `print()` for manual/visual inspection rather than programmatically checked.
- Intended manual checks per printed frame: `parseStatus` shows no `FAILED`/`PARTIAL` entries; `bgpProcessConfiguration` shows a BGP process present on VS-1 with AS 65001 and router ID `192.168.100.2`; `bgpPeerConfiguration` shows one peer row with `Local_AS = 65001`, `Remote_AS = 65002`, `Remote_IP = 10.0.0.1`, `Description = to-vs-3`.

## Pass Condition

- The config parses with no failures in `parseStatus`.
- `bgpProcessConfiguration` shows a BGP process on VS-1 with AS number 65001 and router ID `192.168.100.2`.
- `bgpPeerConfiguration` shows the neighbor `10.0.0.1` with `Remote_AS = 65002`, `Local_AS = 65001`, and `Description = to-vs-3`.

## Fail condition

- `parseStatus` shows `FAILED` or `PARTIAL`, indicating a config conversion or syntax issue.
- `bgpProcessConfiguration` is missing a BGP process on VS-1, or shows an incorrect AS number or router ID.
- `bgpPeerConfiguration` is missing the expected peer, or shows a mismatched `Remote_AS`, `Remote_IP`, or `Description`.
