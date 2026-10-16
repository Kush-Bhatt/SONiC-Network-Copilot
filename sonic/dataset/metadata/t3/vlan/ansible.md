---

type : vlan-ansible-example

title : Configure VLAN10 on SONiC-VS-1 access switch for Alpine PC connectivity

description : Configures VLAN 10 on a single access switch (VS-1) — creates the VLAN, adds five untagged member ports, and assigns an SVI gateway IP, with block/rescue error handling, cleanup-and-retry logic, and config persistence across SONiC, FRR, and netfilter.

resource : data/t3/vlan/ansible1/site.yml

tags : [vlan, svi, access]

category : vlan

role : ansible

topology : t3

paired_with : metadata/t3/vlan/batfish.md

topology_path : metadata/t3/index.md

---

## Scenario

A single access switch, VS-1, serves as the access point for Alpine Linux PCs. VLAN 10 is created with subnet `192.168.10.0/24`, and five ports — `Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24` — are added as untagged (access) members so that connected Alpine PCs land on this VLAN. The SVI (`Vlan10`) is assigned the gateway IP `192.168.10.1/24`. VLAN creation, member assignment (looped over all five ports), and SVI IP assignment are wrapped in a single `block`/`rescue` pair scoped with its own `become: yes`: if any step fails (most likely because VLAN 10 already exists from a prior run), the rescue block tears down the SVI IP and loops over all five ports to remove their VLAN membership, then deletes the VLAN itself (each step error-tolerant), and retries the full configuration sequence — VLAN create, all five port memberships via a Jinja `for` loop, and SVI IP assignment — in one shot. After configuration, the play persists state across three layers: SONiC running config, FRR config, and netfilter/iptables rules.

## Modules

`ansible.builtin.command` is used for the primary configuration tasks (`config vlan add`, `config vlan member add -u`, `config interface ip add`, `config save -y`, `vtysh -c "write memory"`, `netfilter-persistent save`, `show vlan brief`); `ansible.builtin.shell` is used only inside the `rescue` block, where multi-line loops (a `for p in ...` bash loop for cleanup, and a Jinja `{% for %}` template loop for retry) are needed. `become: yes` is scoped per-task rather than at the play level — set on the VLAN `block`, and individually on the save/persist/verify tasks. VLAN creation, member looping (`loop: "{{ vlan_ports }}"`), and SVI IP assignment are wrapped in one `block`/`rescue` pair; the rescue performs cleanup (SVI IP removal, a bash loop removing all five port memberships, VLAN deletion — each error-tolerant) followed by a retry task that re-runs the full sequence via a Jinja template loop. `debug` is used once at the end to print `show vlan brief` output as `stdout_lines` (a list of lines, rather than a single joined string).

## Commands run

- `config vlan add 10` — creates VLAN 10.
- `config vlan member add -u 10 <port>` (looped via `loop: "{{ vlan_ports }}"`) — adds each of `Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24` as untagged (access) members of VLAN 10.
- `config interface ip add Vlan10 192.168.10.1/24` — assigns the SVI (gateway) IP for VLAN 10.
- (rescue only, error-tolerant) `config interface ip remove Vlan10 192.168.10.1/24`; a bash `for` loop running `config vlan member del 10 $p` for each of the five ports; `config vlan del 10` — cleans up a partially or fully existing VLAN10 config.
- (rescue only, retry) `config vlan add 10`; a Jinja `{% for %}` loop emitting `config vlan member add -u 10 <port>` for each of the five ports; `config interface ip add Vlan10 192.168.10.1/24` — re-runs the full configuration sequence after cleanup.
- `config save -y` — persists the running SONiC config.
- `vtysh -c "write memory"` — persists FRR config.
- `netfilter-persistent save` (`ignore_errors: yes`) — persists iptables/netfilter rules.
- `show vlan brief` — captured into `vlan_brief` and printed via `debug` as `stdout_lines`, for post-run verification.

## Config changed

- **VS-1**: VLAN 10 created; `Ethernet8`, `Ethernet12`, `Ethernet16`, `Ethernet20`, `Ethernet24` added as untagged VLAN 10 members; SVI `Vlan10` → `192.168.10.1/24`.
- SONiC config saved with `config save -y`; FRR config saved with `vtysh -c "write memory"`; netfilter/iptables rules persisted with `netfilter-persistent save`.
