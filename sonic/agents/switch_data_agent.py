"""
sonic/agents/switch_data_agent.py
-----------------------------------
One-shot LangChain tool-calling agent.

LLM decides WHICH tools to call (tool names + commands).
Python handles per-switch expansion — runs each tool on ALL switches.
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from sonic.switch.tools import get_config_db, get_frr_config, get_show_command
from sonic.config.settings import LMSTUDIO_BASE_URL, LMSTUDIO_MODEL

# ── tool registry ─────────────────────────────────────────────────────────────
SWITCH_TOOLS = [get_config_db, get_frr_config, get_show_command]

# ── LLM client ────────────────────────────────────────────────────────────────
def _get_llm():
    return ChatOpenAI(
        base_url=LMSTUDIO_BASE_URL,
        api_key="lm-studio",
        model=LMSTUDIO_MODEL,
        temperature=0,
    ).bind_tools(SWITCH_TOOLS)


# ── system prompt ─────────────────────────────────────────────────────────────
_SYSTEM_TEMPLATE = """You are a network data collection agent for SONiC switches.

Your job: given a network configuration query, decide which tools and commands
are needed to gather ALL information required to fulfill the query.

Use mgmt_ip="192.168.200.x" as placeholder — the caller expands to all switches.

Rules:
- For VLAN queries: call get_config_db
- For BGP queries: call get_frr_config
- For MCLAG queries: call get_config_db AND get_show_command with command="show mclag brief"
- For VRF queries: call get_config_db AND get_show_command with command="show vrf"
- For EVPN queries: call get_frr_config AND get_show_command with command="show bgp l2vpn evpn summary"
- For ERSPAN/mirror queries: call get_config_db AND get_show_command with command="show mirror_session"
- For LAG/portchannel queries: call get_config_db AND get_show_command with command="show interfaces portchannel"
- When unsure: call get_config_db
- Never repeat the same tool+command combination
For a VLAN query, you must call: get_config_db
For a BGP query, you must call: get_frr_config
For a MCLAG query, you must call: get_config_db and get_show_command with command="show mclag brief"
For a VRF query, you must call: get_config_db and get_show_command with command="show vrf"
For an EVPN query, you must call: get_frr_config and get_show_command with command="show bgp l2vpn evpn summary"
For an ERSPAN query, you must call: get_config_db and get_show_command with command="show mirror_session"
For a LAG query, you must call: get_config_db and get_show_command with command="show interfaces portchannel"

Always use mgmt_ip="192.168.200.x" as the IP value.
Do not write text responses. Only make tool calls.
"""


def _build_system_prompt(mgmt_ips: dict) -> str:
    return _SYSTEM_TEMPLATE


# ── tool execution ─────────────────────────────────────────────────────────────
def run_tool_calls(tool_calls: list) -> dict:
    """
    Executes all tool calls.
    Returns:
      {
        "get_config_db":   {"192.168.200.11": {...}, "192.168.200.12": {...}},
        "get_frr_config":  {"192.168.200.11": "...", ...},
        "get_show_command": {"192.168.200.11:show vlan brief": "...", ...},
      }
    """
    tool_map = {t.name: t for t in SWITCH_TOOLS}
    results = {}

    for call in tool_calls:
        name = call["name"]
        args = call["args"]

        if name not in tool_map:
            print(f"[!] Unknown tool: {name}")
            continue

        mgmt_ip = args.get("mgmt_ip", "unknown")
        command = args.get("command", "")
        key = f"{mgmt_ip}:{command}" if command else mgmt_ip

        print(f"[*] Calling {name}({args})")
        try:
            result = tool_map[name].invoke(args)
            results.setdefault(name, {})[key] = result
            print(f"[+] {name}({key}) — OK")
        except Exception as e:
            results.setdefault(name, {})[key] = {"error": str(e)}
            print(f"[!] {name}({key}) — FAILED: {e}")

    return results


# ── main entry point ──────────────────────────────────────────────────────────
# ── supplemental show commands per query type ─────────────────────────────────
_SUPPLEMENTAL_COMMANDS = {
    "mclag":     ["show mclag brief"],
    "vrf":       ["show vrf"],
    "evpn":      ["show bgp l2vpn evpn summary"],
    "erspan":    ["show mirror_session"],
    "lag":       ["show interfaces portchannel"],
    "bgp":       ["show ip bgp summary"],
}

def _get_query_type(query: str) -> str:
    """Simple keyword classifier — same logic as dataset_loader but inline."""
    q = query.lower()
    if any(w in q for w in ["mclag", "multi-chassis", "peer-link"]):
        return "mclag"
    if any(w in q for w in ["evpn", "vxlan", "vni", "l2vpn"]):
        return "evpn"
    if any(w in q for w in ["erspan", "mirror", "span"]):
        return "erspan"
    if any(w in q for w in ["vrf", "virtual routing"]):
        return "vrf"
    if any(w in q for w in ["lag", "portchannel", "port-channel", "port channel", "uplink lag"]):
        return "lag"
    if any(w in q for w in ["bgp", "neighbor", "autonomous system", "ebgp", "ibgp"]):
        return "bgp"
    if any(w in q for w in ["vlan", "trunk", "access port", "tagged", "untagged", "svi"]):
        return "vlan"
    return "other"


def gather_switch_data(query: str, mgmt_ips: dict) -> dict:
    """
    One-shot: ask LLM which primary tools to call, expand to all switches,
    add deterministic supplemental show commands, execute everything.

    Args:
      query    : user network configuration query
      mgmt_ips : {node_name: mgmt_ip} from build_inventory()

    Returns:
      gathered data dict keyed by tool name then switch IP
    """
    llm = _get_llm()

    messages = [
        SystemMessage(content=_build_system_prompt(mgmt_ips)),
        HumanMessage(content=query),
    ]

    print(f"[*] SwitchDataAgent: deciding tool calls for: {query!r}")
    response = llm.invoke(messages)

    tool_calls = response.tool_calls
    if not tool_calls:
        print("[!] SwitchDataAgent: no tool calls — falling back to get_config_db on all switches")
        tool_calls = [{"name": "get_config_db", "args": {"mgmt_ip": "192.168.200.x"}}]

    # expand primary tool calls to all switches
    expanded = []
    seen = set()
    for tc in tool_calls:
        for node_name, ip in sorted(mgmt_ips.items()):
            new_args = dict(tc["args"])
            new_args["mgmt_ip"] = ip
            key = (tc["name"], ip, new_args.get("command", ""))
            if key not in seen:
                seen.add(key)
                expanded.append({"name": tc["name"], "args": new_args})

    # add deterministic supplemental show commands
    query_type = _get_query_type(query)
    supplemental = _SUPPLEMENTAL_COMMANDS.get(query_type, [])
    for command in supplemental:
        for node_name, ip in sorted(mgmt_ips.items()):
            key = ("get_show_command", ip, command)
            if key not in seen:
                seen.add(key)
                expanded.append({
                    "name": "get_show_command",
                    "args": {"mgmt_ip": ip, "command": command}
                })

    print(f"[*] SwitchDataAgent: query_type={query_type!r}")
    print(f"[*] SwitchDataAgent: {len(tool_calls)} primary + {len(supplemental)} supplemental → {len(expanded)} total calls")
    for tc in expanded:
        print(f"    → {tc['name']}({tc['args']})")

    raw_results = run_tool_calls(expanded)
    from sonic.switch.parser import parse
    return parse(raw_results, mgmt_ips, query_type)
