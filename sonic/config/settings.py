from pathlib import Path
from dotenv import load_dotenv
import os

# Load sonic-specific .env
from pathlib import Path as _Path
_SONIC_ENV = _Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_SONIC_ENV, override=True)

# ── Project root (Agentic-DAO-LAB/sonic/) ────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Repo root (Agentic-DAO-LAB/) — needed for subprocess cwd ─────────────────
REPO_ROOT = BASE_DIR.parent

# ── Paths ─────────────────────────────────────────────────────────────────────
DOCS_DIR             = BASE_DIR / "docs"
SNAPSHOT_DIR         = BASE_DIR / "snapshots"
SNAPSHOT_SONIC_DIR   = SNAPSHOT_DIR / "sonic_configs"
SNAPSHOT_CONFIGS_DIR = SNAPSHOT_SONIC_DIR / "configs"
SNAPSHOT_BATFISH_DIR = SNAPSHOT_SONIC_DIR / "batfish"
ANSIBLE_DIR          = BASE_DIR / "ansible"
BATFISH_DIR          = BASE_DIR / "batfish"
DATASET_DIR          = BASE_DIR / "dataset"

# ── LLM Providers ─────────────────────────────────────────────────────────────
DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "lmstudio")

LMSTUDIO_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/api/v1")
LMSTUDIO_MODEL    = os.getenv("LMSTUDIO_MODEL", "local-model")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o")

FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://localhost:8000/v1")
FASTAPI_MODEL    = os.getenv("FASTAPI_MODEL", "rafiaa/terraform-codellama-7b")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-6")

# ── GNS3 ──────────────────────────────────────────────────────────────────────
GNS3_HOST          = os.getenv("GNS3_HOST", "localhost")
GNS3_PORT          = int(os.getenv("GNS3_PORT", 3080))
GNS3_BASE_URL      = f"http://{GNS3_HOST}:{GNS3_PORT}/v2"
GNS3_USER          = os.getenv("GNS3_USER", "admin")
GNS3_PASSWORD      = os.getenv("GNS3_PASSWORD") or os.getenv("GNS3_PASS", "1234")
GNS3_AUTH          = os.getenv("GNS3_AUTH", "true").lower() == "true"
GNS3_MGMT_INTERFACE = os.getenv("GNS3_MGMT_INTERFACE", "Ethernet8")
GNS3_PROJECT_NAME   = os.getenv("GNS3_PROJECT_NAME", "")
GNS3_PROJECT_ID     = os.getenv("GNS3_PROJECT_ID", "")
GNS3_JUMP_USER     = os.getenv("GNS3_JUMP_USER", "SONICtestbed")
GNS3_JUMP_PASS     = os.getenv("GNS3_JUMP_PASS", "")

# ── SSH ───────────────────────────────────────────────────────────────────────
SSH_USERNAME = os.getenv("NODE_SSH_USER", "admin")
SSH_PASSWORD = os.getenv("NODE_SSH_PASS", "admin")

# ── Batfish ───────────────────────────────────────────────────────────────────
BATFISH_HOST = os.getenv("BATFISH_HOST", "localhost")

# ── Deterministic verification (Ideas 1–3) ───────────────────────────────────
# Retry cap for the HITL loop. A repair loop plateaus: if the same context did
# not fix it by attempt 2, further attempts mostly burn tokens (design doc §5.7).
MAX_RETRY_ROUNDS = int(os.getenv("MAX_RETRY_ROUNDS", "2"))
# Master switch for the scope guard + spec-diff annotation + fixed Batfish checks.
VERIFICATION_ENABLED = os.getenv("VERIFICATION_ENABLED", "true").lower() == "true"

# ── Dataset examples ──────────────────────────────────────────────────────────
USE_DATASET_EXAMPLES = os.getenv("USE_DATASET_EXAMPLES", "true").lower() == "true"

# ── Retrieval ─────────────────────────────────────────────────────────────────
RETRIEVER_TYPE = os.getenv("RETRIEVER_TYPE", "embedding")   # keyword | bm25 | embedding

#── Static MGMT IP for SONiC switches ──────────────────────────────────────────────────────────
MGMT_IPS_FILE = os.path.join(os.path.dirname(__file__), "mgmt_ips.json")

# ── Web console live-fabric poller (sonic/svc/fabric.py) ─────────────────────
# Each poll opens 2-3 SSH sessions per switch, so the interval is a lab-load
# knob, not a cosmetic one.
FABRIC_POLL_SECONDS = int(os.getenv("FABRIC_POLL_SECONDS", 30))
# BGP session state needs a third SSH round-trip per switch (vtysh summary).
# Off by default: configured neighbors render either way, only liveness is lost.
FABRIC_BGP_SUMMARY = os.getenv("FABRIC_BGP_SUMMARY", "false").lower() == "true"
