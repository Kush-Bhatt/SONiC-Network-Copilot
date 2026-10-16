import os

# ── GNS3 / lab host connection (moved in from sonic.config.settings so
#    bench_net has zero dependency on the rest of sonic/) ──────────────
GNS3_HOST      = os.getenv("GNS3_HOST", "localhost")
GNS3_PORT      = int(os.getenv("GNS3_PORT", 3080))
GNS3_BASE_URL  = f"http://{GNS3_HOST}:{GNS3_PORT}/v2"
GNS3_USER      = os.getenv("GNS3_USER", "")
GNS3_PASSWORD  = os.getenv("GNS3_PASSWORD", "")
GNS3_AUTH      = os.getenv("GNS3_AUTH", "false").lower() == "true"

GNS3_JUMP_USER = os.getenv("GNS3_JUMP_USER", "")
GNS3_JUMP_PASS = os.getenv("GNS3_JUMP_PASS", "")

SSH_USERNAME = os.getenv("NODE_SSH_USER", "admin")
SSH_PASSWORD = os.getenv("NODE_SSH_PASS", "YourPaSsWoRd")

BATFISH_HOST = os.getenv("BATFISH_HOST", "localhost")

# ── Per-topology values — each intern edits this section for their own
#    assigned topology ────────────────────────────────────────────────
PID = "<pid>"
TOPOLOGY_NAME = "2-spine-6-leaf"
TAP_IFACE = "tap6"
TAP_IP = "/24"

mgmt_ips = {
    "SONiC-VS-1": "192.168.10.1",
    "SONiC-VS-2": "192.168.10.7",
    "SONiC-VS-5": "192.168.10.3",
    "SONiC-VS-6": "192.168.10.4",
}

# Dataset queries use the task sheet's labels (L1, S1, L1-H1); the AI pipeline
# only knows GNS3 node names and port names, so the eval run appends this map
# to every query. Host ports are the ones the reference playbooks use.
QUERY_LABELS = {
    "L1": "SonicVS-1",
    "L2": "SonicVS-2",
    "S1": "SonicVS-5",
    "S2": "SonicVS-6",
    "L1-H1": "Ethernet0 on SonicVS-1",
    "L2-H2": "Ethernet0 on SonicVS-2",
}
