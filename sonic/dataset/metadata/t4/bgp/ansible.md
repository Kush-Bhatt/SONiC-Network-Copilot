---

type : bgp-ansible-example

title : Idempotent BGP configuration and verification across SONiC switches with SSH host-key cleanup

description : Clears stale SSH host keys, then configures BGP (router, router-id, neighbors, advertised networks) idempotently across all SONiC switches one at a time, using inventory-driven variables and grep-based existence checks, followed by a wait and multi-command verification.

resource : data/t4/bgp/ansible1/site.yml

tags : [bgp, idempotent, ssh-cleanup]

category : bgp

role : ansible

topology : t4

paired_with : metadata/t4/bgp/batfish.md

topology_path : metadata/t4/index.md

---

## Scenario

This playbook has two plays. The first runs against `localhost` and clears stale SSH `known_hosts` entries for two IPs (`10.0.23.1`, `10.0.23.2`) from the controller's SSH key store, so subsequent SSH/Ansible connections to those addresses don't fail on host-key mismatch. The second play runs against the `sonic_switches` group with `serial: 1` (one switch configured at a time, not in parallel) and configures BGP per switch using inventory-supplied variables — `bgp_asn`, `bgp_router_id`, `bgp_neighbors` (a list of `peer_ip`/`remote_as` pairs), and `bgp_networks` (a list of prefixes to advertise) — none of which are hardcoded in this file, so actual ASNs, router IDs, neighbor IPs, and prefixes are defined per-host elsewhere (e.g. `host_vars`/`group_vars`). Unlike the block/rescue-style playbooks, this one relies on `grep`-based existence checks inside each shell command to decide whether to apply a change or skip it, making every task naturally idempotent without needing rescue blocks. After configuration, the play pauses 15 seconds for BGP convergence, then runs three verification commands per switch and prints labeled output.

## Modules

Only the `ansible.builtin.shell` module is used for all device configuration and verification (`vtysh` BGP teardown/push via chained `-c` flags, `ip route`, `ssh-keygen -R`). `ansible.builtin.pause` is used once for a 15-second BGP convergence wait. `ansible.builtin.debug` is used once per switch with a `loop` over `verify_out.results`, printing a formatted header, a per-command label, and the command's `stdout`. Idempotency is achieved without `block`/`rescue`: each configuration task first greps `show running-config` (or checks for an existing `router bgp <asn>` line) to decide whether to apply a change, appending `&& echo changed` or `echo ok` to the shell output, and `changed_when: "'changed' in r.stdout"` reflects that back to Ansible's change-tracking. The neighbor and network-advertisement tasks are looped over `bgp_neighbors` and `bgp_networks` respectively; the network-advertisement task additionally has a `when: bgp_networks | length > 0` guard so it's skipped entirely on switches with nothing to advertise. `serial: 1` ensures switches are configured one at a time rather than in parallel, likely to avoid simultaneous BGP flaps disrupting adjacent peers.

## Commands run

**Localhost (SSH cleanup):**
- `ssh-keygen -f "/home/SONICtestbed/.ssh/known_hosts" -R "<ip>"` (looped over `10.0.23.1`, `10.0.23.2`, errors ignored) — removes stale host-key entries for both IPs.

**Each SONiC switch (`sonic_switches`, one at a time via `serial: 1`):**
- `sudo vtysh -c "show running-config" | grep -c "^router bgp <bgp_asn>"` then, if a match is found, `sudo vtysh -c "configure terminal" -c "no router bgp <bgp_asn>" -c "end" -c "write memory"` — conditionally tears down an existing BGP instance for that ASN only if one is already present.
- `sudo vtysh -c "configure terminal" -c "router bgp <bgp_asn>" -c "bgp router-id <bgp_router_id>" -c "no bgp ebgp-requires-policy" -c "end" -c "write memory"` — unconditionally (re)creates the BGP process with the router ID.
- Per neighbor in `bgp_neighbors`: `sudo vtysh -c "show running-config" | grep -c "neighbor <peer_ip> remote-as <remote_as>"` then, if not already present, `sudo vtysh -c "configure terminal" -c "router bgp <bgp_asn>" -c "neighbor <peer_ip> remote-as <remote_as>" -c "end" -c "write memory"` — adds each BGP neighbor only if not already configured.
- Per prefix in `bgp_networks` (skipped if the list is empty): `sudo vtysh -c "show running-config" | grep -c "network <prefix>"` then, if not already present, `sudo vtysh -c "configure terminal" -c "router bgp <bgp_asn>" -c "address-family ipv4 unicast" -c "network <prefix>" -c "exit" -c "end" -c "write memory"` — advertises each network prefix only if not already advertised.
- `pause: seconds: 15` — waits for BGP convergence.
- `sudo vtysh -c 'show ip bgp summary'`, `sudo vtysh -c 'show ip bgp neighbors'`, `ip route` — three verification commands run per switch, each captured into `verify_out` and printed via `debug` with a per-switch header and per-command label.

## Config changed

- **All switches in `sonic_switches`** (per-host values supplied by inventory, not hardcoded in this playbook): BGP process created/updated with ASN `bgp_asn` and router ID `bgp_router_id`; each entry in `bgp_neighbors` added as a BGP neighbor (`peer_ip`/`remote_as`) if not already present; each prefix in `bgp_networks` advertised under `address-family ipv4 unicast` if not already present and if the list is non-empty.
- **Controller (`localhost`)**: stale SSH `known_hosts` entries removed for `10.0.23.1` and `10.0.23.2`.
- FRR config saved with `write memory` after every applied change (router creation, each new neighbor, each new advertised network).
</br>
> **Note:** This playbook has no fixed ASNs, IPs, or prefixes in the file itself — those come from `bgp_asn`, `bgp_router_id`, `bgp_neighbors`, and `bgp_networks` defined in inventory (`host_vars`/`group_vars`) per switch. If you'd like the `.md` to list the actual per-switch values, share the corresponding `host_vars`/`group_vars` files or inventory and I'll fill them in.
