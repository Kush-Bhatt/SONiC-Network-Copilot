---

type : bgp-ansible-example

title : Configure eBGP peering between leaf-spine SONiC topology with loopback advertisement on VS-1

description : Configures eBGP on a leaf-spine SONiC topology — point-to-point link IPs and BGP sessions on spines VS-3/VS-4 and leaves VS-1/VS-2, with a loopback interface and prefix advertisement on VS-1, using block/rescue error handling.

resource : data/t1/bgp/ansible1/site.yml

tags : [bgp, ebgp, loopback]

category : bgp

role : ansible

topology : t1

paired_with : metadata/t1/bgp/batfish.md

topology_path : metadata/t1/index.md

---

## Scenario

Two spine switches (VS-3, VS-4, both ASN 65000) and two leaf switches (VS-1 ASN 65001, VS-2 ASN 65002). Spines and leaves connect via point-to-point `/30` links: VS-3–VS-1 (`10.0.13.0/30`), VS-3–VS-2 (`10.0.23.0/30`), VS-4–VS-1 (`10.0.14.0/30`), VS-4–VS-2 (`10.0.24.0/30`). eBGP sessions are established between each leaf and each spine over these links. VS-1 additionally carries a `Loopback0` address (`10.1.0.1/32`) that is advertised into BGP via a `network` statement; VS-2 has no loopback or advertised prefix. Configuration on all three plays is wrapped in `block`/`rescue` pairs for interface/loopback setup and for the BGP push itself, so a failure in one stage is caught and logged rather than halting the play. BGP config removal (`no router bgp ...`) is run unconditionally outside the rescue blocks, since `vtysh` typically doesn't error when no BGP instance exists yet.

## Modules

Only the `shell` module is used for all device configuration (interface IP add/remove, loopback add/remove, `vtysh` BGP push/teardown, `config save -y`). `set_fact` is used on the spine play to select per-host variables (`spine_config` keyed by `inventory_hostname`); the leaf plays use static host-level `vars` instead since each leaf has only one config. Configuration is wrapped in `block`/`rescue` pairs: one block/rescue per host for link (and, on VS-1, loopback) setup, and a second block/rescue per host for the BGP push. `debug` is used for rescue-block failure messages (one per block/rescue pair, printing a `"<stage> block failed on <host>, continuing"`-style message) and for post-run verification, printing `show ip interfaces`, `show bgp summary`, and (on VS-1 only) `ip route` output.

## Commands run

**Spines (VS-3, VS-4):**
- `show ip interfaces | grep <iface> ...; config interface ip remove <iface> <ip>` — clears any pre-existing IP off each point-to-point link interface before reassigning it.
- `config interface ip add <iface> <ip>` — assigns the spine-side IP of each point-to-point link.
- `vtysh -c "configure terminal" -c "no router bgp <asn>" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance for idempotency.
- `vtysh` heredoc pushing `router bgp <asn>`, `bgp router-id`, `bgp log-neighbor-changes`, `no bgp ebgp-requires-policy`, per-neighbor `remote-as`/`description`/`timers 3 9`, and an `address-family ipv4 unicast` block activating both neighbors with `soft-reconfiguration inbound`, followed by `end` and `write memory` — establishes BGP peering with both leaves.
- `config save -y` — persists the running config to SONiC's startup config.
- `show ip interfaces` and `vtysh -c "show bgp summary"` — captured and printed via `debug`, for post-run verification.

**VS-1 (leaf, with loopback advertisement):**
- `show ip interfaces | grep <iface> ...; config interface ip remove <iface> <ip>` — clears any pre-existing IP off each uplink interface before reassigning it.
- `config interface ip add <iface> <ip>` — assigns the leaf-side IP of each uplink to VS-3 and VS-4.
- Loop over `show ip interfaces | grep "Loopback0" | awk '{print $3}'` followed by `config interface ip remove Loopback0 <ip>` (`ignore_errors: true`) — removes any/all existing `Loopback0` IPs for idempotency.
- `config interface ip add Loopback0 10.1.0.1/32` — assigns the loopback address.
- `vtysh -c "configure terminal" -c "no router bgp 65001" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh` heredoc pushing `router bgp 65001`, `bgp router-id 10.1.0.1`, `bgp log-neighbor-changes`, `no bgp ebgp-requires-policy`, per-neighbor `remote-as`/`description`/`timers 3 9` toward VS-3 and VS-4, `address-family ipv4 unicast` with `network 10.1.0.1/32` and both neighbors activated with `soft-reconfiguration inbound`, followed by `end` and `write memory` — establishes BGP peering and advertises the loopback prefix.
- `config save -y` — persists the running config.
- `show ip interfaces`, `vtysh -c "show bgp summary"`, and `ip route` — captured and printed via `debug`, for post-run verification.

**VS-2 (leaf, no loopback):**
- `show ip interfaces | grep <iface> ...; config interface ip remove <iface> <ip>` — clears any pre-existing IP off each uplink interface before reassigning it.
- `config interface ip add <iface> <ip>` — assigns the leaf-side IP of each uplink to VS-3 and VS-4.
- Loop over `show ip interfaces | grep "Loopback0" | awk '{print $3}'` followed by `config interface ip remove Loopback0 <ip>` (`ignore_errors: true`) — clears any stray `Loopback0` IPs, though none are assigned afterward.
- `vtysh -c "configure terminal" -c "no router bgp 65002" -c "end"` (`ignore_errors: true`) — unconditionally tears down any existing BGP instance.
- `vtysh` heredoc pushing `router bgp 65002`, `bgp router-id 10.0.23.1`, `bgp log-neighbor-changes`, `no bgp ebgp-requires-policy`, per-neighbor `remote-as`/`description`/`timers 3 9` toward VS-4 and VS-3, `address-family ipv4 unicast` with both neighbors activated with `soft-reconfiguration inbound` (no `network` statement, since VS-2 advertises nothing), followed by `end` and `write memory` — establishes BGP peering only.
- `config save -y` — persists the running config.
- `show ip interfaces` and `vtysh -c "show bgp summary"` — captured and printed via `debug`, for post-run verification.

## Config changed

- **VS-3**: `Ethernet4` → `10.0.13.2/30`, `Ethernet12` → `10.0.23.2/30`; BGP ASN 65000, router-id `10.0.0.3`, eBGP neighbors `10.0.13.1` (remote-as 65001, VS-1) and `10.0.23.1` (remote-as 65002, VS-2).
- **VS-4**: `Ethernet4` → `10.0.24.2/30`, `Ethernet12` → `10.0.14.2/30`; BGP ASN 65000, router-id `10.0.0.4`, eBGP neighbors `10.0.14.1` (remote-as 65001, VS-1) and `10.0.24.1` (remote-as 65002, VS-2).
- **VS-1**: `Ethernet12` → `10.0.13.1/30`, `Ethernet16` → `10.0.14.1/30`; `Loopback0` → `10.1.0.1/32`; BGP ASN 65001, router-id `10.1.0.1`, eBGP neighbors `10.0.13.2` (remote-as 65000, VS-3) and `10.0.14.2` (remote-as 65000, VS-4); `network 10.1.0.1/32` advertised under `address-family ipv4 unicast`.
- **VS-2**: `Ethernet12` → `10.0.24.1/30`, `Ethernet16` → `10.0.23.1/30`; no loopback assigned; BGP ASN 65002, router-id `10.0.23.1`, eBGP neighbors `10.0.24.2` (remote-as 65000, VS-4) and `10.0.23.2` (remote-as 65000, VS-3); no `network` statement advertised.
- All four devices: config saved with `config save -y`.
