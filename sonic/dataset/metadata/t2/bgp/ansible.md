---

type : bgp-ansible-example

title : Configure eBGP peering in a four-switch ring topology with loopback advertisement on VS-4

description : Configures eBGP on a ring topology (VS-1–VS-2–VS-4–VS-3–VS-1) — point-to-point link IPs and BGP sessions on all four switches, with a loopback interface and prefix advertisement on VS-4, using block/rescue error handling with cleanup-and-retry logic.

resource : data/t2/bgp/ansible1/site.yml

tags : [bgp, ebgp, loopback]

category : bgp

role : ansible

topology : t2

paired_with : metadata/t2/bgp/batfish.md

topology_path : metadata/t2/index.md

---

## Scenario

Four switches (VS-1 ASN 65001, VS-2 ASN 65002, VS-3 ASN 65003, VS-4 ASN 65004) arranged in a ring: VS-1–VS-2 (`10.0.12.0/30`), VS-1–VS-3 (`10.0.13.0/30`), VS-2–VS-4 (`10.0.24.0/30`), VS-3–VS-4 (`10.0.34.0/30`). Each switch peers eBGP with its two ring neighbors. VS-4 additionally carries a `Loopback0` address (`10.4.4.4/32`) that is advertised into BGP via a `network` statement; the other three switches have no loopback or advertised prefix. Interface configuration on each switch is wrapped in a `block`/`rescue` pair that, on failure, removes the just-attempted IPs (ignoring errors) and retries the same assignment once. BGP push is wrapped in a separate `block`/`rescue` pair that only logs a failure message on error, with no retry. BGP config removal (`no router bgp ...`) is run unconditionally outside any rescue block, since `vtysh` typically doesn't error when no BGP instance exists yet — it simply enters config mode and appends. A final play runs against VS-1, VS-2, and VS-3 to pause for convergence and check that VS-4's loopback route was learned.

## Modules

Only the `shell` module is used for all device configuration (interface IP add/remove, loopback add, `vtysh` BGP push/teardown via chained `-c` flags, `config save -y`, `vtysh -c "write memory"`). No `set_fact` is used — each play's `vars`/values are hardcoded directly into per-host tasks rather than a shared per-host config dict. Interface configuration on each host is wrapped in a `block`/`rescue` pair where the rescue removes both just-attempted IPs (`2>/dev/null || true`) and immediately retries the same `config interface ip add` commands. BGP push on each host is wrapped in a separate `block`/`rescue` pair whose rescue only prints a `debug` failure message (`"BGP config block failed on <host>, check vtysh manually"`) with no retry. A final `hosts: vs-1:vs-2:vs-3` play uses `pause: seconds: 10` to allow BGP convergence, then `shell: ip route show 10.4.4.4/32` registered and printed via `debug` to confirm the loopback route propagated from VS-4.

## Commands run

**VS-1:**
- `config interface ip add Ethernet4 10.0.12.1/30` — assigns the link IP toward VS-2.
- `config interface ip add Ethernet8 10.0.13.1/30` — assigns the link IP toward VS-3.
- (rescue only) `config interface ip remove Ethernet4 10.0.12.1/30`, `config interface ip remove Ethernet8 10.0.13.1/30` (ignored errors), then both `config interface ip add` commands retried.
- `config save -y` — persists the running config.
- `vtysh -c "configure terminal" -c "no router bgp 65001" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh -c "..."` chained flags pushing `router bgp 65001`, `bgp router-id 10.0.12.1`, `bgp log-neighbor-changes`, `no bgp ebgp-requires-policy`, neighbor `remote-as`/`description`/`timers 3 9` for `10.0.12.2` (VS-2, AS 65002) and `10.0.13.2` (VS-3, AS 65003), and an `address-family ipv4 unicast` block activating both with `soft-reconfiguration inbound` — establishes BGP peering with both ring neighbors.
- `vtysh -c "write memory"` — persists FRR config.
- `vtysh -c "show bgp summary"` — captured into `bgp_out` and printed via `debug`.

**VS-2:**
- `config interface ip add Ethernet8 10.0.12.2/30` — assigns the link IP toward VS-1.
- `config interface ip add Ethernet4 10.0.24.1/30` — assigns the link IP toward VS-4.
- (rescue only) removes both IPs (ignored errors), then retries both `config interface ip add` commands.
- `config save -y` — persists the running config.
- `vtysh -c "configure terminal" -c "no router bgp 65002" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh -c "..."` chained flags pushing `router bgp 65002`, `bgp router-id 10.0.12.2`, neighbor config for `10.0.12.1` (VS-1, AS 65001) and `10.0.24.2` (VS-4, AS 65004), both activated with `soft-reconfiguration inbound` — establishes BGP peering with both ring neighbors.
- `vtysh -c "write memory"` — persists FRR config.
- `vtysh -c "show bgp summary"` — captured into `bgp_out` and printed via `debug`.

**VS-3:**
- `config interface ip add Ethernet8 10.0.13.2/30` — assigns the link IP toward VS-1.
- `config interface ip add Ethernet4 10.0.34.1/30` — assigns the link IP toward VS-4.
- (rescue only) removes both IPs (ignored errors), then retries both `config interface ip add` commands.
- `config save -y` — persists the running config.
- `vtysh -c "configure terminal" -c "no router bgp 65003" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh -c "..."` chained flags pushing `router bgp 65003`, `bgp router-id 10.0.13.2`, neighbor config for `10.0.13.1` (VS-1, AS 65001) and `10.0.34.2` (VS-4, AS 65004), both activated with `soft-reconfiguration inbound` — establishes BGP peering with both ring neighbors.
- `vtysh -c "write memory"` — persists FRR config.
- `vtysh -c "show bgp summary"` — captured into `bgp_out` and printed via `debug`.

**VS-4 (with loopback advertisement):**
- `config interface ip add Ethernet20 10.0.24.2/30` — assigns the link IP toward VS-2.
- `config interface ip add Ethernet16 10.0.34.2/30` — assigns the link IP toward VS-3.
- `config interface ip add Loopback0 10.4.4.4/32` — assigns the loopback address.
- (rescue only) removes all three IPs (ignored errors), then retries all three `config interface ip add` commands.
- `config save -y` — persists the running config.
- `vtysh -c "configure terminal" -c "no router bgp 65004" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh -c "..."` chained flags pushing `router bgp 65004`, `bgp router-id 10.0.24.2`, neighbor config for `10.0.24.1` (VS-2, AS 65002) and `10.0.34.1` (VS-3, AS 65003), an `address-family ipv4 unicast` block with `network 10.4.4.4/32` plus both neighbors activated with `soft-reconfiguration inbound` — establishes BGP peering and advertises the loopback prefix.
- `vtysh -c "write memory"` — persists FRR config.
- `vtysh -c "show bgp summary"` — captured into `bgp_out` and printed via `debug`.

**Final verification (VS-1, VS-2, VS-3):**
- `pause: seconds: 10` — waits for BGP convergence.
- `ip route show 10.4.4.4/32` — captured into `route_check` and printed via `debug`, to confirm VS-4's loopback route was learned.

## Config changed

- **VS-1**: `Ethernet4` → `10.0.12.1/30`, `Ethernet8` → `10.0.13.1/30`; BGP ASN 65001, router-id `10.0.12.1`, eBGP neighbors `10.0.12.2` (remote-as 65002, VS-2) and `10.0.13.2` (remote-as 65003, VS-3).
- **VS-2**: `Ethernet8` → `10.0.12.2/30`, `Ethernet4` → `10.0.24.1/30`; BGP ASN 65002, router-id `10.0.12.2`, eBGP neighbors `10.0.12.1` (remote-as 65001, VS-1) and `10.0.24.2` (remote-as 65004, VS-4).
- **VS-3**: `Ethernet8` → `10.0.13.2/30`, `Ethernet4` → `10.0.34.1/30`; BGP ASN 65003, router-id `10.0.13.2`, eBGP neighbors `10.0.13.1` (remote-as 65001, VS-1) and `10.0.34.2` (remote-as 65004, VS-4).
- **VS-4**: `Ethernet20` → `10.0.24.2/30`, `Ethernet16` → `10.0.34.2/30`; `Loopback0` → `10.4.4.4/32`; BGP ASN 65004, router-id `10.0.24.2`, eBGP neighbors `10.0.24.1` (remote-as 65002, VS-2) and `10.0.34.1` (remote-as 65003, VS-3); `network 10.4.4.4/32` advertised under `address-family ipv4 unicast`.
- All four devices: config saved with `config save -y` and FRR config saved with `vtysh -c "write memory"`.
