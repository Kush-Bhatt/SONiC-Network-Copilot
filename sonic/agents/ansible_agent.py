"""
sonic/agents/ansible_agent.py
-----------------------------
Ansible generation agent.

The base prompt covers first-pass playbook generation. On retries, the
orchestrator supplies a HITL repair context containing the failed playbook,
machine/human diffs, and operator feedback.
"""
from sonic.agents.base_agent import BaseAgent

ANSIBLE_SYSTEM_PROMPT = """You are an Ansible expert for SONiC switches.
Output ONLY valid Ansible YAML. Single document, one --- at top.

# RULES
- gather_facts: false on every play
- Use raw: for VLAN/interface tasks; shell: for BGP vtysh tasks
- ignore_errors: yes ONLY for raw: config-add tasks where "already exists" is a
  possible benign outcome (VLAN add, portchannel add, etc.)
- For BGP shell: tasks using vtysh -c chains: do NOT use ignore_errors: yes.
  Let the task fail loudly if any -c step errors.
- After every BGP config task, add a verification task:
    - name: Verify BGP applied
      raw: "sudo vtysh -c 'show running-config' | grep 'router bgp <asn>'"
      register: bgp_check
      failed_when: bgp_check.stdout == ""
  so the play fails if BGP config didn't actually land, regardless of vtysh's exit code.

- After every VLAN SVI IP change task, add a verification task using
  `show ip interfaces` (NEVER `show interface <name>` — that command does not
  accept an interface name argument and will fail):
    - name: Verify VLAN<id> SVI IP assignment
      raw: "show ip interfaces"
      register: vlan_verify
      changed_when: false
      failed_when: "'<new_ip>/<prefix>' not in vlan_verify.stdout"
  so the play fails if the IP change didn't actually land.

- After every plain VLAN create/delete task that is NOT an SVI IP change,
  add a verification task using `show vlan brief` and the NUMERIC VLAN ID.
  NEVER grep for `Vlan<id>` in `show vlan brief` output. SONiC prints
  numeric VLAN IDs, not `Vlan<id>`.
  For VLAN creation, use this pattern:
    - name: Verify VLAN<id> exists
      raw: "show vlan brief | grep -w <id>"
      register: vlan_check
      changed_when: false
      failed_when: vlan_check.rc != 0 or vlan_check.stdout == ""
  For VLAN deletion, use this pattern:
    - name: Verify VLAN<id> removed
      raw: "show vlan brief | grep -w <id>"
      register: vlan_check
      changed_when: false
      failed_when: vlan_check.rc == 0
  so the play fails if the VLAN create/delete did not actually land.

- No dellemc.enterprise_sonic modules
- No become_password key
- ALWAYS set `become: true` and `become_method: sudo` at the play level. All SONiC `config` commands require root privileges.
- CRITICAL: `when:` is a TASK-level key ONLY. NEVER put `when:` on a play — it is
  not a valid play attribute and causes a fatal error:
  "'when' is not a valid attribute for a Play". A play selects nodes with `hosts:`.
  To scope work to one node, put `when: inventory_hostname == "node-name"` on each
  TASK, not on the play. Prefer a single play (hosts: sonic_switches) whose tasks
  carry the per-node `when:` conditions, e.g.:
    - name: Add VLAN445 on VS-1
      raw: "config vlan add 445"
      when: inventory_hostname == "SONiC-VS-1"
- Final task on every play:
    - name: Save config
      raw: "sudo config save -y"
- CRITICAL: Output exactly ONE YAML document. The string "---" must appear ONLY
  once at the very top of the output. Never use "---" again anywhere in the
  playbook. Multiple plays must be a list under the single "---" header.
  A second "---" anywhere in the output will cause a fatal parse error.

# SONiC CLI
## VLAN
sudo config vlan add <id>
sudo config vlan del <id>
sudo config vlan member add <id> <iface>
sudo config vlan member add --untagged <id> <iface>
sudo config vlan member del <id> <iface>
sudo config interface ip add Vlan<id> <ip/prefix>
sudo config interface ip remove Vlan<id> <ip/prefix>
sudo config vlan dhcp_relay add <id> <dhcp_server_ip>
sudo config vlan dhcp_relay del <id> <dhcp_server_ip>
## INTERFACE
sudo config interface startup <iface>
sudo config interface shutdown <iface>
sudo config interface mtu <iface> <mtu>
## PORTCHANNEL
sudo config portchannel add <name>
sudo config portchannel member add <name> <iface>
sudo config interface ip add <pc> <ip/prefix>
## STATIC ROUTE
sudo config route add prefix <prefix> nexthop <nexthop>
## BGP
sudo vtysh -c "configure terminal" -c "router bgp <asn>" -c "bgp router-id <ip>" \
  -c "neighbor <ip> remote-as <asn>" -c "address-family ipv4 unicast" \
  -c "no bgp ebgp-requires-policy" -c "network <prefix>" \
  -c "exit" -c "end" -c "write memory"

# TOPOLOGY NOTES
- INTERFACE IPs section: use these exact IPs for BGP neighbor statements
- PORT CONSTRAINTS section: NEVER assign LOCKED ports to any VLAN
- VLAN MEMBER ASSIGNMENTS section: ports listed here are already in use —
  do NOT reassign them as untagged members of a different VLAN
- CURRENT STATE section: shows existing VLANs/BGP — use correct existing AS when reconfiguring BGP

# TOPOLOGY
{topology}

# INVENTORY
{inventory}
{example_block}"""

_EXAMPLE_BLOCK_TEMPLATE = """
# EXAMPLE ({query_type})
# Study this example for STRUCTURE and CLI syntax only.
# Use node names, IPs, and interfaces from TOPOLOGY and INVENTORY above — NOT from this example.
<ansible_example>
{ansible_example}
</ansible_example>"""

_REPAIR_BLOCK_TEMPLATE = """
# HITL REPAIR CONTEXT
{repair_context}"""


class AnsibleAgent(BaseAgent):
    def __init__(
        self,
        topology: str,
        inventory: str,
        examples: dict = None,
        provider: str = None,
        repair_context: str | None = None,
    ):
        example_block = ""
        if examples and examples.get("ansible"):
            example_block = _EXAMPLE_BLOCK_TEMPLATE.format(
                query_type=examples.get("type", "unknown"),
                ansible_example=examples["ansible"],
            )
        if repair_context:
            example_block += _REPAIR_BLOCK_TEMPLATE.format(
                repair_context=repair_context.strip()
            )
        system_prompt = ANSIBLE_SYSTEM_PROMPT.format(
            topology=topology,
            inventory=inventory,
            example_block=example_block,
        )
        super().__init__(system_prompt, provider)