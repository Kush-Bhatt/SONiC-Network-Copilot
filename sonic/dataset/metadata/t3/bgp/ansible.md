---

type : bgp-ansible-example

title : Configure eBGP on single switch VS-1 with static neighbor peering and prefix advertisement

description : Configures eBGP on a single standalone switch (VS-1, ASN 65001) — pushes a BGP session to a statically defined neighbor and advertises a prefix, with block/rescue error handling on the BGP push.

resource : data/t3/bgp/ansible1/site.yml

tags : [bgp, ebgp, single-switch]

category : bgp

role : ansible

topology : t3

paired_with : metadata/t3/bgp/batfish.md

topology_path : metadata/t3/index.md

---

## Scenario

A single switch, VS-1 (ASN 65001), is configured with an eBGP session toward a neighbor at `10.0.0.1` (ASN 65002, described `to-vs-3`) — the neighbor itself is not configured by this playbook, only referenced as VS-1's peer. VS-1's router ID is set to `192.168.100.2`, and the prefix `192.168.10.0/24` is advertised into BGP via a `network` statement. Unlike the multi-switch playbooks, this play has no interface IP assignment step — it only manages the BGP process itself. BGP config removal (`no router bgp ...`) is run unconditionally outside any rescue block, since `vtysh` typically doesn't error when no BGP instance exists yet — it simply enters config mode and appends. The BGP push itself is wrapped in a `block`/`rescue` pair whose rescue only logs a failure message, with no retry.

## Modules

Only the `shell` module is used for all device configuration (`vtysh` BGP teardown/push via chained `-c` flags, `vtysh -c "write memory"`, `config save -y`). No `set_fact` is used — a single flat `vars` block at the play level (`bgp_asn`, `router_id`, `neighbor_ip`, `neighbor_asn`, `neighbor_desc`, `advertise_prefix`) supplies all templated values. The BGP push is wrapped in a `block`/`rescue` pair whose rescue only prints a `debug` failure message (`"BGP config block failed on VS-1, check vtysh manually"`), with no cleanup or retry. `debug` is also used once for post-run verification, printing captured `show bgp summary` output.

## Commands run

- `vtysh -c "configure terminal" -c "no router bgp 65001" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance for idempotency.
- `vtysh -c "..."` chained flags pushing `router bgp 65001`, `bgp router-id 192.168.100.2`, `bgp log-neighbor-changes`, `no bgp ebgp-requires-policy`, `neighbor 10.0.0.1 remote-as 65002`, `neighbor 10.0.0.1 description to-vs-3`, `neighbor 10.0.0.1 timers 3 9`, and an `address-family ipv4 unicast` block with `network 192.168.10.0/24` plus `neighbor 10.0.0.1 activate` and `neighbor 10.0.0.1 soft-reconfiguration inbound` — establishes the BGP session and advertises the prefix.
- `vtysh -c "write memory"` — persists FRR config.
- `config save -y` — persists the running SONiC config.
- `vtysh -c "show bgp summary"` — captured into `bgp_out` and printed via `debug`, for post-run verification.

## Config changed

- **VS-1**: BGP ASN 65001, router-id `192.168.100.2`, eBGP neighbor `10.0.0.1` (remote-as 65002, description `to-vs-3`, timers `3 9`); `network 192.168.10.0/24` advertised under `address-family ipv4 unicast`.
- FRR config saved with `vtysh -c "write memory"`; SONiC config saved with `config save -y`.
