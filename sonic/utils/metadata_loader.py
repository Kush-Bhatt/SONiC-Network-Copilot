"""
sonic/utils/metadata_loader.py
--------------------------------
Loads OKF metadata (.md frontmatter + body) for ansible/batfish examples,
producing the metadata_overview list that both the retriever and
PlannerAgent use.

Frontmatter fields are used by PlannerAgent to pick the best matching
example. The body text (Scenario/Modules/Commands/etc) is ADDITIONALLY
captured and exposed as a "body" key, purely to give retrievers richer
text to score against — it is NEVER sent to AnsibleAgent/BatfishAgent,
which only ever load the real .yml/.py resource file directly via
_load_examples_from_selection() in run_pipeline.py.
"""

from pathlib import Path

# Fields PlannerAgent actually reads — see sonic/agents/planner_agent.py __init__.
# Anything else in frontmatter (tags, role, topology, topology_path) is ignored
# on purpose; the planner doesn't use them. "body" is added separately below,
# it is NOT a frontmatter field.
PLANNER_FIELDS = ("type", "title", "description", "category", "resource", "paired_with")

# Only these category folders have real (non-stub) metadata today.
# intervlan/iptables/vrf under t4 are empty placeholder files — skip them
# until real content exists, to avoid the loader emitting confusing warnings.
ALLOWED_CATEGORIES = {"vlan", "bgp"}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parses 'key : value' lines between --- markers into a dict, and
    returns everything AFTER the closing --- as the body text (used
    only for retrieval scoring — never sent to AnsibleAgent/BatfishAgent,
    which only ever read the real .yaml/.py resource file).
    Tolerant of a missing OPENING '---' (some files start directly with
    'type : ...' and only close with a single trailing '---').
    """
    lines = text.splitlines()
    start = 1 if lines and lines[0].strip() == "---" else 0

    front_lines = []
    body_start_idx = None
    for i, line in enumerate(lines[start:], start=start):
        if line.strip() == "---":
            body_start_idx = i + 1
            break
        front_lines.append(line)

    if not front_lines:
        raise ValueError("File doesn't have a frontmatter block")

    fields = {}
    for line in front_lines:
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    body = "\n".join(lines[body_start_idx:]).strip() if body_start_idx else ""
    return fields, body


def _load_examples(metadata_root: str, exclude_topology: str | None) -> list[dict]:
    """
    Shared walk logic for both public loader functions below.
    exclude_topology=None means "include everything" (load_metadata_overview).
    """
    root = Path(metadata_root)
    overview = []

    for md_file in root.rglob("*.md"):
        if md_file.name == "index.md":
            continue
        if md_file.parent.name not in ALLOWED_CATEGORIES:
            continue

        text = md_file.read_text(encoding="utf-8")
        try:
            fields, body = _parse_frontmatter(text)
        except ValueError:
            print(f"[metadata_loader] Skipping {md_file} — no frontmatter block")
            continue

        if exclude_topology and fields.get("topology") == exclude_topology:
            continue

        missing = [f for f in PLANNER_FIELDS if f not in fields]
        if missing:
            print(f"[metadata_loader] WARNING {md_file} missing fields {missing}, skipping")
            continue

        overview.append({
            **{k: fields[k] for k in PLANNER_FIELDS},
            "body": body,
            "metadata_path": str(md_file),
        })

    return overview


def load_metadata_overview(metadata_root: str) -> list[dict]:
    """
    Walks metadata_root, reads every ansible.md / batfish.md file inside
    'vlan' and 'bgp' category folders, and returns a list of dicts shaped
    exactly like PlannerAgent's metadata_overview parameter expects.

    No topology filtering — every vlan/bgp example across every topology
    (t1-t4) is included, so the planner sees the full pattern library and
    can generalize to topologies not present in the dataset, rather than
    just copying whichever example happens to match the current topology.

    Args:
        metadata_root: path to sonic/dataset/metadata

    Returns:
        list of dicts, each with keys: type, title, description, category,
        resource, paired_with, body, metadata_path
    """
    return _load_examples(metadata_root, exclude_topology=None)


def load_metadata_overview_excluding(metadata_root: str, exclude_topology: str) -> list[dict]:
    """
    Same as load_metadata_overview(), but drops every example whose
    'topology' frontmatter field matches exclude_topology.

    Used for leave-one-topology-out (LOTO) testing: when planning for
    topology X, the planner never sees X's own worked examples — only
    the other topologies'. Tests whether the planner generalizes the
    underlying config pattern rather than copying the example written
    specifically for X.

    Args:
        metadata_root: path to sonic/dataset/metadata
        exclude_topology: topology name to hold out, e.g. "t1"

    Returns:
        list of dicts, same shape as load_metadata_overview(), minus any
        example whose frontmatter 'topology' field == exclude_topology.
    """
    return _load_examples(metadata_root, exclude_topology=exclude_topology)