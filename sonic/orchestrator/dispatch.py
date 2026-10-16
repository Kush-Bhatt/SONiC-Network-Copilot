from sonic.gns3.inventory_builder import build_inventory
from sonic.ansible.runner import run_ansible_playbook
from sonic.agents.switch_data_agent import gather_switch_data
from sonic.agents.ansible_agent import AnsibleAgent

INTENT_TO_TOOL = {
    "build_inventory" : build_inventory,
}

INTENT_TO_AGENT = {
    "gather_state" : (gather_switch_data, None),
    "ansible_apply" : (AnsibleAgent, run_ansible_playbook)
}