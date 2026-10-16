---

type : vlan-ansible-example

title : Idempotent full-topology SONiC provisioning with VLAN, PortChannels, loopback, routed interfaces, and static routes

description : Idempotently provisions a mixed leaf/spine SONiC topology — PortChannels, loopback, VLAN with SVI on VS-1/VS-2, routed interfaces on VS-3, static routes, and rp_filter disabling — using raw commands with pre-clean via CONFIG_DB and per-switch conditional blocks.

resource : data/t4/vlan/ansible1/site.yml

tags : [vlan, portchannel, routed-interface, idempotent]

category : vlan

role : ansible

topology : t4

paired_with : metadata/t4/vlan/batfish.md

topology_path : metadata/t4/index.md

---

## Scenario

This playbook has two plays. The first runs against `localhost` and clears stale SSH `known_hosts` entries for three IPs (`192.168.100.2`, `192.168.100.3`, `192.168.100.4`) so subsequent connections don't fail on host-key mismatch. The second play runs against the `sonic_switches` group with `serial: 1` (one switch at a time) and provisions a full mixed topology where VLAN configuration applies only to VS-1 and VS-2, while routed interfaces apply only to VS-3 — gated by `when: vlan is defined` and `when: routed_interfaces is defined` respectively, so each switch only runs the blocks relevant to its role based on inventory-supplied variables. Before any interface configuration, a pre-clean step directly queries and deletes stale `INTERFACE|<name>` keys from CONFIG_DB via `sonic-db-cli`, removing any lingering IPs so router-port-to-VLAN-member conversion doesn't collide with an existing INTERFACE key. PortChannels, loopback, VLAN, routed interfaces, and static routes are all configured via `ansible.builtin.raw` with `ignore_errors: true` (rather than block/rescue), making individual command failures non-fatal without any retry logic. The play also disables `rp_filter` (reverse-path filtering) globally and per-interface, and persists that setting into `/etc/sysctl.conf` for reboot survival. None of the actual VLAN ID, member ports, IPs, PortChannel names, loopback IP, routed interfaces, or static routes are hardcoded in this file — all come from inventory variables (`interfaces_to_clean`, `portchannels`, `loopback`, `vlan`, `routed_interfaces`, `static_routes`), defined per-host in `host_vars`/`group_vars`.

## Modules

`ansible.builtin.shell` is used for the CONFIG_DB pre-clean loop (which requires shell logic — a `for` loop over `sonic-db-cli` keys, conditional deletion, and echo-based change detection) and for the two `rp_filter` tasks (which require multi-line shell logic — a `for` loop over `/proc/sys/net/ipv4/conf/*` and `grep`/`sed` idempotent sysctl.conf editing). `ansible.builtin.raw` is used for every actual `config`/`show` command sent to the switch — PortChannel creation/membership/IP, loopback creation/IP, VLAN creation/membership/MTU/SVI IP, routed interface IP/MTU, static route addition, config save, and all verification `show` commands — with `ignore_errors: true` on nearly every raw task instead of block/rescue. `when: vlan is defined` gates the four VLAN-related tasks (VLAN create, member-add, MTU-set, SVI IP) so they only run on hosts where a `vlan` variable is supplied (VS-1, VS-2). `when: routed_interfaces is defined` gates the two routed-interface tasks (IP assign, MTU-set) so they only run on hosts where `routed_interfaces` is supplied (VS-3). `loop_control: label:` is used throughout to produce readable per-item task labels in output (e.g. `"{{ item.port }}"` instead of the full dict). `debug` is used once per switch with a `loop` over `verify_out.results`, printing a formatted header, per-command label, and `stdout`.

## Commands run

**Localhost (SSH cleanup):**
- `ssh-keygen -f "/home/SONICtestbed/.ssh/known_hosts" -R "<ip>"` (looped over `192.168.100.2`, `192.168.100.3`, `192.168.100.4`, errors ignored) — removes stale host-key entries.

**Each SONiC switch (`sonic_switches`, one at a time via `serial: 1`):**
- Pre-clean loop over `interfaces_to_clean`: for each interface, iterates `sonic-db-cli CONFIG_DB keys "INTERFACE|<item>|*"`, extracts each assigned IP, runs `sudo config interface ip remove <item> <ip>` for each, then checks `sonic-db-cli CONFIG_DB exists "INTERFACE|<item>"` and if present runs `sonic-db-cli CONFIG_DB del "INTERFACE|<item>"` — clears stale INTERFACE keys before router-port-to-VLAN conversion.
- `sudo config portchannel add <name>` (looped over `portchannels`) — creates each PortChannel.
- `sudo config portchannel member add <name> <member>` (looped) — adds each member port.
- `sudo config interface ip add <name> <ip>` (looped) — assigns each PortChannel's IP.
- `sudo config loopback add <loopback.name>` — creates the loopback interface.
- `sudo config interface ip add <loopback.name> <loopback.ip>` — assigns the loopback IP.
- (VS-1, VS-2 only, `when: vlan is defined`) `sudo config vlan add <vlan.id>` — creates the VLAN.
- (VS-1, VS-2 only) `sudo config vlan member add <vlan.id> <port> --untagged` (looped over `vlan.members`) — adds each member port as untagged.
- (VS-1, VS-2 only) `sudo config interface mtu <port> 1500` (looped over `vlan.members`) — sets MTU 1500 on each VLAN member port.
- (VS-1, VS-2 only) `sudo config interface ip add Vlan<vlan.id> <vlan.ip>` — assigns the SVI (gateway) IP.
- (VS-3 only, `when: routed_interfaces is defined`) `sudo config interface ip add <name> <ip>` (looped over `routed_interfaces`) — assigns IPs to routed interfaces.
- (VS-3 only) `sudo config interface mtu <name> 1500` (looped) — sets MTU 1500 on each routed interface.
- `sudo config route add prefix <prefix> nexthop <nexthop>` (looped over `static_routes`) — adds each static route.
- `echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter`, `echo 0 > /proc/sys/net/ipv4/conf/default/rp_filter`, and a loop writing `0` to `rp_filter` under every interface in `/proc/sys/net/ipv4/conf/` — disables reverse-path filtering globally and per-interface at runtime.
- `grep`/`sed`-based idempotent edit (or append) of `net.ipv4.conf.all.rp_filter = 0` and `net.ipv4.conf.default.rp_filter = 0` into `/etc/sysctl.conf` — persists the rp_filter setting across reboots.
- `sudo config save -y` — persists the running config.
- Verification loop: `show ip interfaces`, `show ip route`, `show vlan brief`, `show interface portchannel`, `sudo vtysh -c 'show running-config'`, `cat /proc/sys/net/ipv4/conf/all/rp_filter` — each captured into `verify_out` and printed per-switch via `debug` with a header and per-command label.

## Config changed

- **All switches in `sonic_switches`** (per-host values supplied by inventory, not hardcoded in this playbook): stale `INTERFACE|*` CONFIG_DB keys cleaned per `interfaces_to_clean`; PortChannels created per `portchannels` (name, member, IP); loopback interface created with `loopback.name`/`loopback.ip`; static routes added per `static_routes` (prefix/nexthop); global and per-interface `rp_filter` disabled at runtime and persisted in `/etc/sysctl.conf`.
- **VS-1, VS-2 only** (where `vlan` is defined): VLAN `vlan.id` created; each port in `vlan.members` added as untagged member with MTU set to 1500; SVI `Vlan<vlan.id>` assigned `vlan.ip`.
- **VS-3 only** (where `routed_interfaces` is defined): each interface in `routed_interfaces` assigned its IP and set to MTU 1500.
- **Controller (`localhost`)**: stale SSH `known_hosts` entries removed for `192.168.100.2`, `192.168.100.3`, `192.168.100.4`.
- Config saved per switch with `config save -y`.

> **Note:** This playbook has no fixed VLAN ID, IPs, PortChannel names, loopback IP, routed interfaces, or static routes in the file itself — all come from `interfaces_to_clean`, `portchannels`, `loopback`, `vlan`, `routed_interfaces`, and `static_routes` defined in inventory (`host_vars`/`group_vars`) per switch. If you'd like the `.md` to list the actual per-switch values, share the corresponding inventory files and I'll fill them in.
