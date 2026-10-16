# sonic/svc/preflight.py
"""
sonic/svc/preflight.py
--------------------------
One-time infrastructure health checks. Run ONCE per process/session start
by each svc/*.py entrypoint (run.py, eval.py, api.py) — NEVER called
inside run_pipeline() itself, since that runs on every single query and
we don't want to re-check GNS3/Batfish/SSH connectivity 24 times in a
benchmark run.

Checks:
  1. LM Studio  — server reachable + configured model actually loaded
  2. GNS3       — server reachable + project resolvable + status=opened
  3. Switches   — SSH reachable (two-hop, same path the pipeline uses)
  4. Batfish    — port 9996 reachable

Each check function returns (ok: bool, detail: str) so failures carry
a human-readable reason, not just True/False.
"""

import socket
import time

import requests

from sonic.config.settings import (
    LMSTUDIO_BASE_URL, LMSTUDIO_MODEL,
    GNS3_BASE_URL, BATFISH_HOST,
)
from sonic.gns3.api_client import get_all_projects, get_active_project_id
from sonic.switch.ssh_client import _open_jump_channel


# ═══════════════════════════════════════════════════════════════════════════
# Individual checks
# ═══════════════════════════════════════════════════════════════════════════

def check_lmstudio() -> tuple[bool, str]:
    """LM Studio server reachable + configured model actually loaded."""
    url = f"{LMSTUDIO_BASE_URL.rstrip('/')}/models"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        models = [m.get("id") for m in r.json().get("data", [])]
    except Exception as e:
        return False, f"LM Studio unreachable at {url}: {e}"

    if LMSTUDIO_MODEL not in models:
        return False, (
            f"LM Studio reachable but model {LMSTUDIO_MODEL!r} not loaded. "
            f"Loaded models: {models}"
        )
    return True, f"OK — {LMSTUDIO_MODEL!r} loaded ({len(models)} total models available)"


def check_gns3(project_id: str | None = None) -> tuple[bool, str]:
    """GNS3 server reachable + target project resolvable + status=opened."""
    try:
        projects = get_all_projects()
    except Exception as e:
        return False, f"GNS3 unreachable at {GNS3_BASE_URL}: {e}"

    if project_id:
        match = next((p for p in projects if p.get("project_id") == project_id), None)
        if not match:
            available = [(p.get("name"), p.get("project_id")) for p in projects]
            return False, f"project_id={project_id!r} not found on GNS3 server. Available: {available}"
        if match.get("status") != "opened":
            return False, f"Project {match.get('name')!r} found but status={match.get('status')!r} (expected 'opened')"
        return True, f"OK — project {match.get('name')!r} is opened"

    # No project_id given — just confirm server is up and something is resolvable
    try:
        active_id = get_active_project_id()
    except Exception as e:
        return False, f"GNS3 reachable but no active project resolvable: {e}"
    return True, f"OK — server reachable, active project resolves to {active_id}"


def check_switches(mgmt_ips: dict, timeout_per_switch: float = 10.0) -> tuple[bool, str]:
    """SSH reachability for every switch in mgmt_ips, via the same two-hop path the pipeline uses."""
    if not mgmt_ips:
        return False, "mgmt_ips is empty — nothing to check"

    results = {}
    for name, ip in mgmt_ips.items():
        start = time.time()
        try:
            channel = _open_jump_channel(ip, 22)
            channel.close()
            results[name] = (True, time.time() - start, "")
        except Exception as e:
            results[name] = (False, time.time() - start, str(e))

    failed = {n: r for n, r in results.items() if not r[0]}
    if failed:
        detail = "; ".join(f"{n} ({r[2][:60]})" for n, r in failed.items())
        return False, f"{len(failed)}/{len(results)} switches unreachable: {detail}"

    return True, f"OK — {len(results)}/{len(results)} switches reachable"


def check_batfish(port: int = 9996, timeout: float = 3.0) -> tuple[bool, str]:
    """Raw socket check on Batfish's port — matches the actual failure mode
    seen live (ConnectionRefusedError on localhost:9996)."""
    try:
        with socket.create_connection((BATFISH_HOST, port), timeout=timeout):
            return True, f"OK — {BATFISH_HOST}:{port} accepting connections"
    except Exception as e:
        return False, f"Batfish unreachable at {BATFISH_HOST}:{port}: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# Combined runner
# ═══════════════════════════════════════════════════════════════════════════

def run_preflight(project_id: str | None = None, mgmt_ips: dict | None = None) -> dict:
    """
    Runs all checks. Does NOT raise — returns a result dict so the caller
    (svc/run.py, svc/eval.py, svc/api.py) decides what to do with failures
    (e.g. terminal might warn-and-continue, benchmark harness might abort).

    Args:
      project_id : optional — if given, GNS3 check also verifies THIS
                   specific project is opened, not just that the server is up
      mgmt_ips   : optional — if given, switch SSH reachability is checked;
                   if omitted, switch check is skipped (caller may not know
                   mgmt_ips yet at the point preflight runs)

    Returns:
      {
        "ok": bool,                       # True only if ALL checks passed
        "checks": {
          "lmstudio": {"ok": bool, "detail": str},
          "gns3":     {"ok": bool, "detail": str},
          "switches": {"ok": bool, "detail": str} | None,   # None if mgmt_ips not given
          "batfish":  {"ok": bool, "detail": str},
        }
      }
    """
    print("\n" + "=" * 60)
    print("  Preflight checks")
    print("=" * 60)

    checks = {}

    ok, detail = check_lmstudio()
    checks["lmstudio"] = {"ok": ok, "detail": detail}
    print(f"  [{'OK' if ok else 'FAIL'}] LM Studio   : {detail}")

    ok, detail = check_gns3(project_id)
    checks["gns3"] = {"ok": ok, "detail": detail}
    print(f"  [{'OK' if ok else 'FAIL'}] GNS3        : {detail}")

    if mgmt_ips:
        ok, detail = check_switches(mgmt_ips)
        checks["switches"] = {"ok": ok, "detail": detail}
        print(f"  [{'OK' if ok else 'FAIL'}] Switches    : {detail}")
    else:
        checks["switches"] = None
        print(f"  [SKIP] Switches    : mgmt_ips not provided")

    ok, detail = check_batfish()
    checks["batfish"] = {"ok": ok, "detail": detail}
    print(f"  [{'OK' if ok else 'FAIL'}] Batfish     : {detail}")

    overall_ok = all(
        c["ok"] for c in checks.values() if c is not None
    )

    print("=" * 60)
    print(f"  Preflight: {'ALL PASSED' if overall_ok else 'SOME CHECKS FAILED'}")
    print("=" * 60 + "\n")

    return {"ok": overall_ok, "checks": checks}


if __name__ == "__main__":
    from sonic.gns3.api_client import GNS3_PROJECT_ID
    result = run_preflight(project_id=GNS3_PROJECT_ID)
    print(result)