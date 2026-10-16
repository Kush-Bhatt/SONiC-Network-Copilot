---

type : vlan-ansible-example

title : Configure VLAN 100 SVI on leaves with static inter-leaf routing via spines

description : Configures a leaf-spine SONiC topology — VLAN 100 + SVI on leaves VS-1/VS-2, point-to-point link IPs, and static inter-leaf routing on spines VS-3/VS-4, with block/rescue error handling.

resource : data/t1/vlan/ansible1/site.yml

tags : [vlan, svi, static-route]

category : vlan

role : ansible

topology : t1

paired_with : metadata/t1/vlan/batfish.md

topology_path : metadata/t1/vlan/index.md

---

## Scenario

Two spine switches (VS-3, VS-4) and two leaf switches (VS-1, VS-2). Each leaf owns VLAN 100 with its own subnet — VS-1: `10.0.1.0/24`, VS-2: `10.0.2.0/24` — with `Ethernet4` as the untagged access port into that VLAN. Leaves connect to both spines via point-to-point `/30` links. Static routes on leaves and spines let VS-1's VLAN subnet reach VS-2's VLAN subnet (and vice versa) through the spines.

## Modules

Only the `shell` module is used for all device configuration (`config vlan ...`, `config interface ip ...`, `config route ...`, `config save -y`). `set_fact` is used to select per-host variables (`spine_config`/`leaf_config` keyed by `inventory_hostname`). Configuration on both spines and leaves is wrapped in five separate `block`/`rescue` pairs (spine link config, spine route add, leaf pre-clean, leaf VLAN/uplink config, leaf route add) so that a failure in one stage is caught and logged rather than halting the play. `debug` is used six times total: five of these are rescue-block failure messages (one per block/rescue pair, printing `"<stage> block failed on {{ inventory_hostname }}, continuing"`), and the sixth is the final verification task that prints the captured `show vlan brief` output.

## Commands run

**Spines (VS-3, VS-4):**
- `show ip interfaces | grep <iface> ...; config interface ip remove <iface> <ip>` — clears any pre-existing IP off the link interface before reassigning it.
- `config interface ip add <iface> <ip>` — assigns the spine-side IP of each point-to-point link.
- `ip route show <prefix> | grep -v "proto kernel"; config route del prefix <prefix> nexthop <nexthop>` (`ignore_errors: true`) — removes a matching static route if one already exists, for idempotency.
- `config route add prefix <prefix> nexthop <nexthop>` — adds the static route toward the far leaf's VLAN subnet.
- `config save -y` — persists the running config to SONiC's startup config.

**Leaves (VS-1, VS-2):**
- `show vlan brief | grep <access_port>; config vlan member del <vid> <access_port>` — removes the access port from any VLAN it currently belongs to.
- `show ip interfaces | grep ^<access_port>; config interface ip remove <access_port> <ip>` — clears any IP directly assigned to the access port.
- `show ip interfaces | grep ^Vlan<id>; config interface ip remove Vlan<id> <ip>` — clears an existing SVI IP for VLAN 100 if present.
- `show vlan brief | grep <id>; config vlan del <id>` — deletes VLAN 100 if it already exists, to start clean.
- `config vlan add <id>` — creates VLAN 100.
- `config vlan member add -u <id> <access_port>` — adds `Ethernet4` as an untagged (access) member of VLAN 100.
- `config interface ip add Vlan<id> <ip>` — assigns the SVI (gateway) IP for VLAN 100.
- `config interface ip add <iface> <ip>` (looped over links) — assigns IPs on the leaf's uplinks to both spines.
- `ip route show <prefix>...; config route del prefix ...` (`ignore_errors: true`) — removes an existing matching static route if present.
- `config route add prefix <prefix> nexthop <nexthop>` — adds static routes (one per spine uplink) toward the other leaf's VLAN subnet.
- `config save -y` — persists the running config.
- `show vlan brief` — captured into `vlan_output` and printed via `debug`, for post-run verification.

## Config changed

- **VS-3**: `Ethernet4` → `10.0.13.2/30`, `Ethernet12` → `10.0.23.2/30`; static routes `10.0.1.0/24` via `10.0.13.1`, `10.0.2.0/24` via `10.0.23.1`.
- **VS-4**: `Ethernet4` → `10.0.24.2/30`, `Ethernet12` → `10.0.14.2/30`; static routes `10.0.1.0/24` via `10.0.14.1`, `10.0.2.0/24` via `10.0.24.1`.
- **VS-1**: VLAN 100 created; SVI `Vlan100` → `10.0.1.1/24`; `Ethernet4` added as untagged VLAN 100 member; `Ethernet12` → `10.0.13.1/30`, `Ethernet16` → `10.0.14.1/30`; static routes to `10.0.2.0/24` via `10.0.13.2` and via `10.0.14.2`.
- **VS-2**: VLAN 100 created; SVI `Vlan100` → `10.0.2.1/24`; `Ethernet4` added as untagged VLAN 100 member; `Ethernet12` → `10.0.24.1/30`, `Ethernet16` → `10.0.23.1/30`; static routes to `10.0.1.0/24` via `10.0.24.2` and via `10.0.23.2`.
- All four devices: config saved with `config save -y`.
