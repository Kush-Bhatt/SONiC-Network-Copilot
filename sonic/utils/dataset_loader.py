"""
src/utils/dataset_loader.py
----------------------------
Classifies user query into a known type (vlan, bgp, other)
and loads matching example files from the dataset/ directory.

Dataset layout expected:
  dataset/
    vlan/
      ansible/
        site.yml
        roles/sonic_base/tasks/main.yml   ← role tasks (merged in)
      batfish/
        vlanbatfishcode.py
    bgp/
      ansible/
        bgp_configure.yml
      batfish/
        bgpbatfishcode.py

Usage:
    from sonic.utils.dataset_loader import classify_query, load_examples

    qtype = classify_query("Create VLAN 100 on both switches")  # → "vlan"
    examples = load_examples(qtype)
    # examples = {"ansible": "...yaml...", "batfish": "...python..."}
    # examples = {} if USE_DATASET_EXAMPLES=false or type="other"
"""

from pathlib import Path
from sonic.config.settings import BASE_DIR, USE_DATASET_EXAMPLES

# ── keyword lists ──────────────────────────────────────────────────────────────

_VLAN_KEYWORDS = {
    "vlan", "vlan member", "access port", "trunk port", "tagged",
    "untagged", "switchport", "l2", "layer 2", "layer2",
    "svi", "vlan interface", "broadcast domain",
}

_BGP_KEYWORDS = {
    "bgp", "border gateway", "autonomous system", "asn",
    "neighbor", "ebgp", "ibgp", "bgp neighbor", "routing protocol",
    "route advertisement", "bgp session", "bgp peer",
}

# ── classifier ─────────────────────────────────────────────────────────────────

def classify_query(query: str) -> str:
    """
    Returns 'vlan', 'bgp', or 'other'.
    Checks for keyword presence (case-insensitive).
    BGP takes priority over VLAN if both match (e.g. "BGP over VLAN").
    """
    q = query.lower()

    bgp_score  = sum(1 for kw in _BGP_KEYWORDS  if kw in q)
    vlan_score = sum(1 for kw in _VLAN_KEYWORDS if kw in q)

    if bgp_score > 0 and bgp_score >= vlan_score:
        return "bgp"
    if vlan_score > 0:
        return "vlan"
    return "other"


# ── file discovery ─────────────────────────────────────────────────────────────

DATASET_DIR = BASE_DIR / "dataset"

# Maps query_type → list of candidate files in priority order
_ANSIBLE_CANDIDATES = {
    "vlan": [
        "vlan/ansible/site.yml",
        "vlan/ansible/roles/sonic_base/tasks/main.yml",   # merged as appendix
    ],
    "bgp": [
        "bgp/ansible/bgp_configure.yml",
    ],
}

_BATFISH_CANDIDATES = {
    "vlan": ["vlan/batfish/vlanbatfishcode.py"],
    "bgp":  ["bgp/batfish/bgpbatfishcode.py"],
}


def _read(path: Path) -> str:
    """Read file content, return empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_examples(query_type: str) -> dict:
    """
    Load dataset examples for the given query type.

    Returns:
        {
            "ansible": "<yaml content>",
            "batfish": "<python content>",
            "type":    "vlan" | "bgp" | "other"
        }
        Returns empty dict if USE_DATASET_EXAMPLES=false or type="other"
        or if no files found.
    """
    if not USE_DATASET_EXAMPLES:
        return {}

    if query_type == "other":
        return {}

    # ── Ansible ───────────────────────────────────────────────────────────────
    ansible_parts = []
    for rel_path in _ANSIBLE_CANDIDATES.get(query_type, []):
        content = _read(DATASET_DIR / rel_path)
        if content:
            ansible_parts.append(f"# --- {rel_path} ---\n{content}")

    ansible_example = "\n\n".join(ansible_parts)

    # ── Batfish ───────────────────────────────────────────────────────────────
    batfish_parts = []
    for rel_path in _BATFISH_CANDIDATES.get(query_type, []):
        content = _read(DATASET_DIR / rel_path)
        if content:
            batfish_parts.append(f"# --- {rel_path} ---\n{content}")

    batfish_example = "\n\n".join(batfish_parts)

    if not ansible_example and not batfish_example:
        print(f"[!] dataset_loader: no example files found for type='{query_type}'")
        return {}

    return {
        "type":    query_type,
        "ansible": ansible_example,
        "batfish": batfish_example,
    }


# ── convenience: classify + load in one call ──────────────────────────────────

def get_examples_for_query(query: str) -> tuple[str, dict]:
    """
    Classify query and load matching examples.

    Returns:
        (query_type, examples_dict)
        query_type: 'vlan' | 'bgp' | 'other'
        examples_dict: {} if no examples or disabled
    """
    qtype = classify_query(query)
    examples = load_examples(qtype)

    if examples:
        print(f"[*] Query classified as '{qtype}' — loading dataset examples")
    else:
        if USE_DATASET_EXAMPLES and qtype != "other":
            print(f"[!] Query type='{qtype}' but no example files found in dataset/")
        elif qtype == "other":
            print(f"[*] Query type='{qtype}' — no dataset examples available")

    return qtype, examples