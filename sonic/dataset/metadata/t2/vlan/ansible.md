---

type : vlan-ansible-example

title : Configure VLAN10 access switch on SONiC-VS-4 for Alpine node connectivity

description : Configures VLAN 10 on a single access switch (VS-4) — creates the VLAN, adds two untagged member ports, and assigns an SVI IP, with block/rescue error handling and cleanup-and-retry logic.

resource : data/t2/vlan/ansible1/site.yml

tags : [vlan, svi, access]

category : vlan

role : ansible

topology : t2

paired_with : metadata/t2/vlan/batfish.md

topology_path : metadata/t2/index.md

---

## Scenario

A single access switch, VS-4, serves as the access point for Alpine Linux nodes. VLAN 10 (`Vlan10`) is created with subnet `10.0.2.0/24`, and two ports — `Ethernet4` and `Ethernet8` — are added as untagged (access) members so that connected Alpine nodes land on this VLAN. The SVI (`Vlan10`) is assigned the gateway IP `10.0.2.1/24`. All VLAN/member/SVI configuration is wrapped in a single `block`/`rescue` pair: if any step fails (most likely because VLAN 10 already exists from a prior run), the rescue block tears down the SVI IP, both VLAN memberships, and the VLAN itself (each with `2>/dev/null || true` to ignore errors), then retries the full configuration sequence from scratch in one shot.

## Modules

Only the `ansible.builtin.shell` module is used for all device configuration (`config vlan add`, `config vlan member add -u`, `config interface ip add`, `config save -y`). `become: yes` is set at the play level. VLAN creation, member assignment, and SVI IP assignment are wrapped in one `block`/`rescue` pair; the rescue block performs cleanup (SVI IP removal, member removal for both ports, VLAN deletion — each error-tolerant) followed by a single retry task that re-runs all four configuration commands together. `debug` is used twice for post-run verification: once to print captured `show vlan brief` output, and once to print captured `show ip interfaces` output.

## Commands run

- `config vlan add 10` — creates VLAN 10.
- `config vlan member add 10 Ethernet4 -u` — adds `Ethernet4` as an untagged (access) member of VLAN 10.
- `config vlan member add 10 Ethernet8 -u` — adds `Ethernet8` as an untagged (access) member of VLAN 10.
- `config interface ip add Vlan10 10.0.2.1/24` — assigns the SVI (gateway) IP for VLAN 10.
- (rescue only, error-tolerant) `config interface ip remove Vlan10 10.0.2.1/24`, `config vlan member del 10 Ethernet4`, `config vlan member del 10 Ethernet8`, `config vlan del 10` — cleans up a partially or fully existing VLAN10 config.
- (rescue only, retry) `config vlan add 10`, `config vlan member add 10 Ethernet4 -u`, `config vlan member add 10 Ethernet8 -u`, `config interface ip add Vlan10 10.0.2.1/24` — re-runs the full configuration sequence after cleanup.
- `config save -y` — persists the running config to SONiC's startup config.
- `show vlan brief` — captured into `vlan_brief` and printed via `debug`, for post-run verification.
- `show ip interfaces` — captured into `ip_intf` and printed via `debug`, for post-run verification.

## Config changed

- **VS-4**: VLAN 10 created; `Ethernet4` and `Ethernet8` added as untagged VLAN 10 members; SVI `Vlan10` → `10.0.2.1/24`.
- Config saved with `config save -y`.
