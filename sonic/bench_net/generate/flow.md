# sonic/bench_net — dataset creation flow

## 0. One-time setup (per intern, per assigned topology)

Edit `sonic/bench_net/config.py`:
- `PID`, `TOPOLOGY_NAME`, `TAP_IFACE`, `TAP_IP`, `mgmt_ips` — your assigned topology
- `GNS3_JUMP_USER` / `GNS3_JUMP_PASS` / `SSH_USERNAME` / `SSH_PASSWORD` — lab credentials
  (fill these in yourself, don't commit real passwords)

```bash
python -m sonic.bench_net.generate.capture_topology   # once per topology
python -m sonic.bench_net.generate.write_hosts_ini     # once per topology
```

`capture_topology.py` closes the GNS3 project, exports it, reopens it, restores
the tap interface **on the remote GNS3 host** (10.68.0.36 by default — not your
local machine), and restarts all nodes. Wait for the printed "Done" message,
then verify reachability yourself before continuing:
```bash
ping -c 3 <each mgmt IP in your topology>
```

## 1. Per dataset item

### a) Confirm you actually know the current state before capturing it as S0/next-step
Don't assume — check:
```bash
ssh admin@<switch mgmt ip> "show vlan brief"
# or show ip bgp summary, show ip interfaces, etc. — whatever the task's
# stated init_state claims should be true
```
If a previous chain step already ran, your init_state name should reflect that
(e.g. `<topology>-s1` = S0 + step 1, `<topology>-s2` = S0 + step 1 + step 2 ...).
Capturing a state that doesn't match what you think it is silently corrupts
every downstream item's ground truth.

### b) Capture the init_state
```bash
python -m sonic.bench_net.generate.capture_init_state --name <init_state_name>
```
Name explicitly, one distinct starting point per name. Reuse a name across
items only when they genuinely share the same starting point.

### c) Write the playbook for this item
Edit `sonic/bench_net/generate/playbook.yaml` by hand or with AI help.
- Scope every task with `when: inventory_hostname == "<switch>"` — never let
  a playbook touch switches the task doesn't mention.
- Add a verify task (no `ignore_errors`) after every real change, so a
  silent failure in the change task can't produce a false "success."
- `ignore_errors: true` on the change task itself is fine (and expected) —
  the verify task is what actually catches failures.

### d) Apply
```bash
python -m sonic.bench_net.generate.apply_ansible
```
Read the PLAY RECAP. `failed=0` for the target switch and `ok`/`changed` as
expected is success. Any `failed=1` (even with `ignored=1` elsewhere) means
something didn't actually happen — do not proceed to build_dataset_item on
a failed apply. Common cause: SONiC's INTERFACE-table stub bug ("Error: X is
a router interface!") — see "Known gotchas" below.

### e) Build the dataset item
```bash
python -m sonic.bench_net.generate.build_dataset_item \
    --id <ITEM-ID> \
    --query "<the natural-language prompt an agent would receive>" \
    --category vlan|bgp \
    --init-state <init_state_name_from_step_b>
```
This diffs the **saved init_state** (not a fresh "before" capture) against a
fresh capture of current live state, via Batfish structured facts, and
writes `sonic/bench_net/dataset/<ITEM-ID>.json`.

### f) Sanity-check the output before moving on
- Fact count and `node`/`table` values match what the task's "expected
  changes" description says — nothing extra, nothing missing.
- If the task has an explicit "preservation check" (e.g. "BGP unchanged"),
  confirm zero facts of that type appear.
- `expected_diff: []` (zero facts) after an apply that reported `changed`
  usually means the apply silently failed — go back and check (d).

## 2. Chaining multi-step tasks

Each step's init_state = the previous step's post-apply state:
```
S0                    → capture_init_state --name <topo>-s0
  [step 1 playbook + apply]
S0 + step1             → capture_init_state --name <topo>-s1
  [step 2 playbook + apply]
S0 + step1 + step2      → capture_init_state --name <topo>-s2
  ...
```
Each item's `--init-state` argument points at the state *before* that
item's own change.
