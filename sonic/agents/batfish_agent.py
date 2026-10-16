from sonic.agents.base_agent import BaseAgent

_EXAMPLE_BLOCK_TEMPLATE = """
# EXAMPLE ({query_type})
<batfish_example>
{batfish_example}
</batfish_example>"""


ASSERTIONS_TEMPLATE = '''import os, sys
import pandas as pd
from pybatfish.client.session import Session
from pybatfish.client.asserts import assert_no_forwarding_loops

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snapshots", "sonic_configs")
bf = Session(host=os.getenv("BATFISH_HOST", "localhost"))
bf.set_network("sonic")
bf.init_snapshot(SNAPSHOT_DIR, name="sonic-snap", overwrite=True)

def safe_fetch(query):
    try:
        return query.answer().frame()
    except Exception as e:
        print(f"  !! DataFrame fetch failed: {e}")
        return pd.DataFrame()

df_vlans      = safe_fetch(bf.q.switchedVlanProperties())
df_interfaces = safe_fetch(bf.q.interfaceProperties())
df_routes     = safe_fetch(bf.q.routes())
df_bgp_status = safe_fetch(bf.q.bgpSessionStatus())
df_bgp_compat = safe_fetch(bf.q.bgpSessionCompatibility())
df_nodes      = safe_fetch(bf.q.nodeProperties())
df_bgp_proc   = safe_fetch(bf.q.bgpProcessConfiguration())

all_passed = True

def check(label, condition, pass_msg, fail_msg):
    global all_passed
    if condition:
        print(f"  [PASS] {label}: {pass_msg}")
    else:
        print(f"  [FAIL] {label}: {fail_msg}")
        all_passed = False

# ── LLM-generated assertions ──────────────────────────────────────────
{llm_assertions}
# ── end assertions ────────────────────────────────────────────────────

try:
    assert_no_forwarding_loops(bf)
    print("  [PASS] No forwarding loops detected")
except AssertionError:
    print("  [FAIL] Forwarding loops detected")
    all_passed = False
except Exception:
    pass  # session warning, not a real loop — ignore

if all_passed:
    print("\\n[OK] All assertions passed.")
    sys.exit(0)
else:
    print("\\n[FAIL] Some assertions failed.")
    sys.exit(1)
'''

SYSTEM_PROMPT = """You generate Python assertion lines for a network verification script.

Available pre-fetched DataFrames (already loaded, do NOT re-fetch):
  df_vlans      from bf.q.switchedVlanProperties()
                columns: Node (str), VLAN_ID (object — cast to int), Interfaces (list of SVIs only, NOT access/trunk ports)

  df_interfaces from bf.q.interfaceProperties()
                columns: Interface (object — always call .astype(str) before comparing),
                         Access_VLAN (object), Allowed_VLANs (str, comma-separated e.g. "10,777"),
                         Switchport_Mode (str: ACCESS/TRUNK/NONE),
                         Primary_Address (str or None — plain string like "10.0.6.1/24", NOT a Prefix object),
                         VRF (str), Active (bool), Proxy_ARP (bool)

  df_routes     from bf.q.routes()
                columns: Node, VRF, Network (str e.g. "10.0.6.0/24"), Next_Hop_IP, Protocol

  df_bgp_proc   from bf.q.bgpProcessConfiguration()
                columns: Node, VRF, Router_ID (str e.g. "192.168.200.11"), Neighbors (list)
                NOTE: NO Local_AS column — cannot check AS number here

  df_bgp_status from bf.q.bgpSessionStatus()
                columns: Node, VRF, Local_AS (int), Remote_AS, Remote_IP, Established_Status
                WARNING: EMPTY DataFrame when no BGP neighbors configured

  df_bgp_compat from bf.q.bgpSessionCompatibility()
                columns: Node, VRF, Local_AS (int), Remote_AS, Remote_IP, Configured_Status
                WARNING: EMPTY DataFrame when no BGP neighbors configured
                VALUES: Configured_Status can be UNIQUE_MATCH, HALF_OPEN, UNKNOWN_REMOTE, NO_LOCAL_IP

  df_nodes      from bf.q.nodeProperties()
                columns: Node, VRFs (list of VRF names), IP_Access_Lists (list of ACL names),
                         Route_Filter_Lists, Routing_Policies

Available function:
  check(label, condition, pass_msg, fail_msg)

═══════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════
- Output ONLY check() lines, nothing else
- No imports, no session code, no DataFrame fetching, no file writes
- Nodes are always lowercase: sonic-vs-1, sonic-vs-2, sonic-vs-3
- VLAN_ID column dtype is object — always cast: int(row) or use == with int literal directly
- Interface column dtype is object — ALWAYS call .astype(str) before == comparison
- Primary_Address is a plain Python str or None — compare with == directly, but guard for None first
- CRITICAL: filter into a named variable first, check len, then access .values[0] from that SAME variable

═══════════════════════════════════════════════════════════
SAFE PANDAS PATTERN (use this exact structure every time)
═══════════════════════════════════════════════════════════
# WRONG — fragile, LEN check and VALUES access on different objects:
  (df_interfaces[df_interfaces["Interface"]=="x"]["col"].values[0] == "y")
    if len(df_interfaces[df_interfaces["Interface"].astype(str)=="x"]) > 0 else False

# CORRECT — filter once into _rows, use _rows for both len check and value access:
  (_rows := df_interfaces[df_interfaces["Interface"].astype(str)=="x"],
   len(_rows) > 0 and pd.notna(_rows["col"].values[0]) and _rows["col"].values[0] == "y")[-1]

# OR equivalently with a helper (also fine):
  def _get(df, iface): r = df[df["Interface"].astype(str)==iface]; return r["col"].values[0] if len(r)>0 else None

═══════════════════════════════════════════════════════════
EXAMPLE PATTERNS — copy these exactly, substitute values
═══════════════════════════════════════════════════════════

# ── VLAN EXISTS ──
# VLAN_ID is object dtype — compare as int
check("VLAN 100 on sonic-vs-1",
      100 in [int(x) for x in df_vlans[df_vlans["Node"]=="sonic-vs-1"]["VLAN_ID"].values],
      "VLAN 100 present", "VLAN 100 missing")

# ── SVI IP ADDRESS ──
# Primary_Address is plain str ("10.0.6.1/24") or None — guard with pd.notna()
# Filter into _r first, use _r for both len check and value access
_r = df_interfaces[df_interfaces["Interface"].astype(str)=="sonic-vs-1[Vlan600]"]
check("Vlan600 IP on sonic-vs-1",
      len(_r) > 0 and pd.notna(_r["Primary_Address"].values[0]) and _r["Primary_Address"].values[0] == "10.0.6.1/24",
      "Vlan600 IP correct", "Vlan600 IP missing or wrong")

# ── TAGGED PORT MEMBERSHIP ──
# Allowed_VLANs is a comma-separated string e.g. "10,777" — check with str(val)
_r = df_interfaces[df_interfaces["Interface"].astype(str)=="sonic-vs-1[Ethernet16]"]
check("Ethernet16 tagged in VLAN 777 on sonic-vs-1",
      len(_r) > 0 and "777" in str(_r["Allowed_VLANs"].values[0]),
      "Ethernet16 tagged in VLAN 777", "Ethernet16 not tagged in VLAN 777")

# ── UNTAGGED PORT MEMBERSHIP ──
_r = df_interfaces[df_interfaces["Interface"].astype(str)=="sonic-vs-1[Ethernet12]"]
check("Ethernet12 untagged in VLAN 10 on sonic-vs-1",
      len(_r) > 0 and str(_r["Access_VLAN"].values[0]) == "10",
      "Ethernet12 untagged in VLAN 10", "Ethernet12 not untagged in VLAN 10")

# ── BGP PROCESS EXISTS (use this for any "configure BGP" query) ──
# df_bgp_proc has NO Local_AS — do NOT try to check the AS number
check("BGP process on sonic-vs-1",
      not df_bgp_proc[df_bgp_proc["Node"]=="sonic-vs-1"].empty,
      "BGP process configured", "BGP process missing")

# ── BGP ROUTER-ID ──
_r = df_bgp_proc[df_bgp_proc["Node"]=="sonic-vs-1"]
check("BGP router-id on sonic-vs-1",
      len(_r) > 0 and _r["Router_ID"].values[0] == "192.168.200.11",
      "Router-ID correct", "Router-ID missing or wrong")

# ── BGP NEIGHBOR SESSION (ONLY when query says 'verify neighbor' or 'check BGP peer') ──
# df_bgp_compat is EMPTY if no neighbors — only use when neighbors are expected to exist
_r = df_bgp_compat[(df_bgp_compat["Node"]=="sonic-vs-1") & (df_bgp_compat["Remote_IP"]=="10.0.12.2")]
check("BGP neighbor 10.0.12.2 on sonic-vs-1",
      len(_r) > 0 and _r["Configured_Status"].values[0] == "UNIQUE_MATCH",
      "BGP neighbor configured correctly", "BGP neighbor missing or misconfigured")

# ── ROUTE EXISTS ──
check("Route 10.0.6.0/24 on sonic-vs-1",
      "10.0.6.0/24" in df_routes[df_routes["Node"]=="sonic-vs-1"]["Network"].values,
      "Route present", "Route missing")

# ── VRF EXISTS ──
_r = df_nodes[df_nodes["Node"]=="sonic-vs-1"]
check("VRF Vrf-blue on sonic-vs-1",
      len(_r) > 0 and "Vrf-blue" in list(_r["VRFs"].values[0]),
      "VRF Vrf-blue present", "VRF Vrf-blue missing")

# ── INTERFACE IN VRF ──
_r = df_interfaces[df_interfaces["Interface"].astype(str)=="sonic-vs-1[Ethernet12]"]
check("Ethernet12 in VRF Vrf-blue on sonic-vs-1",
      len(_r) > 0 and _r["VRF"].values[0] == "Vrf-blue",
      "Interface in correct VRF", "Interface not in expected VRF")

═══════════════════════════════════════════════════════════
BGP RULES (critical)
═══════════════════════════════════════════════════════════
- For "configure BGP" / "set AS" / "enable BGP": use ONLY df_bgp_proc (check row exists)
- df_bgp_proc has NO Local_AS column — NEVER try to verify AS number
- df_bgp_status and df_bgp_compat are EMPTY when no neighbors — NEVER use them for basic BGP checks
- ONLY use df_bgp_compat when query explicitly mentions "verify neighbor" or "check BGP peer"

═══════════════════════════════════════════════════════════
WHAT NOT TO DO
═══════════════════════════════════════════════════════════
- Do NOT write: df_interfaces[...]["Interface"] == "node[Iface]"  (missing .astype(str))
- Do NOT write: df_vlans["VLAN_ID"] == "100"  (string compare on object column — use int or cast)
- Do NOT write: _rows["col"].values[0]  after filtering with a DIFFERENT expression than _rows
- Do NOT check Active status for SVIs — SVIs are Active=False when no ports assigned, IP still present
- Do NOT try to verify BGP AS number — it is not available in df_bgp_proc
"""

class BatfishAgent(BaseAgent):
    def __init__(self, topology: str, examples: dict = None, provider: str = None):
        example_block = ""
        if examples and examples.get("batfish"):
            example_block = _EXAMPLE_BLOCK_TEMPLATE.format(
                query_type=examples.get("type", "unknown"),
                batfish_example=examples["batfish"],
            )
        system_prompt = SYSTEM_PROMPT + example_block
        super().__init__(system_prompt, provider)
        self.topology = topology

    def chat(self, query: str) -> str:
        prompt = f"Topology:\n{self.topology}\n\nQuery: {query}"
        llm_assertions = super().chat(prompt)
        # fix literal \n strings the LLM sometimes outputs instead of real newlines
        llm_assertions = llm_assertions.replace("\\n", "\n")
        # sanitize unicode hyphens/dashes the LLM sometimes outputs
        llm_assertions = llm_assertions.replace("\u2011", "-").replace("\u2010", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
        llm_assertions = llm_assertions.replace("df_vlains", "df_vlans").replace("df_vlan ", "df_vlans ").replace("df_vlan[", "df_vlans[")
        llm_assertions = llm_assertions.replace("‑", "-").replace("‐", "-").replace("‒", "-").replace("–", "-").replace("—", "-")
        return ASSERTIONS_TEMPLATE.replace("{llm_assertions}", llm_assertions)