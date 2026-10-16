"""
sonic/svc/api.py
----------------
FastAPI surface that exposes the existing SONiC agentic pipeline to the web
frontend (sonic/frontend). It changes NO backend code — it wraps the same
entry points that sonic/svc/run.py drives:

  - PlannerAgent.plan()            -> Decompose stage
  - orchestrator.run_plan()        -> Gather context / Generate / Twin rehearsal
  - review_callback (HITL gate)    -> Review & finalize (accept / request changes)
  - switch.ssh_client.get_sonic_config() -> live Inventory

Progress is streamed to the browser over Server-Sent Events. Because the
backend emits progress only via print(), a stdout tee parses the known stage
markers into live SSE events; correctness never depends on the tee — stages
also advance on guaranteed checkpoints (planner returned, review_callback
fired, run_plan returned).

Run it from the repo root:
    uvicorn sonic.svc.api:app --reload
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# The frontend is a Vite build; `npm run build` in sonic/frontend emits dist/.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Fixed, honest pipeline stages (what the live backend actually emits).
STAGE_ORDER = ["decompose", "gate1", "generate", "rehearse", "review"]
STAGE_TITLES = {
    "decompose": "Decompose intent",
    "gate1": "Review IR",
    "generate": "Generate configuration",
    "rehearse": "Twin rehearsal",
    "review": "Review & finalize",
}

app = FastAPI(title="Infracopilot — SONiC Agentic Pipeline")


# ──────────────────────────────────────────────────────────────────────────
# In-memory run store (no persistence was requested; single active run).
# ──────────────────────────────────────────────────────────────────────────


class RunRecord:
    def __init__(self, run_id: str, query: str):
        self.id = run_id
        self.query = query
        self.status = "starting"  # starting|decomposing|gathering|generating|rehearsing|awaiting_review|done|failed
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.project_id: Optional[str] = None
        self.topology_name: Optional[str] = None

        # Ordered stage records, each accumulating its own content.
        self.stages: dict[str, dict] = {
            key: {"key": key, "title": STAGE_TITLES[key], "status": "idle", "payload": {}}
            for key in STAGE_ORDER
        }

        # Append-only event log (replayed to any SSE listener, live or late).
        self.events: list[dict] = []
        self._lock = threading.Lock()

        # HITL gate plumbing.
        self.review_event = threading.Event()
        self.review_decision: Optional[dict] = None
        self.current_round = 1

        # IntentSpec gate plumbing — same shape as the review gate: the pipeline
        # thread blocks on the Event until the operator answers over HTTP.
        self.approval_event = threading.Event()
        self.approval_decision: Optional[dict] = None

        # Human-agent feedback loop: one entry per review round.
        self.rounds: list[dict] = []

    # -- event helpers -----------------------------------------------------

    def _append_event(self, event: dict) -> None:
        with self._lock:
            event["seq"] = len(self.events)
            self.events.append(event)

    def set_status(self, status: str) -> None:
        self.status = status
        self._append_event({"type": "run_status", "status": status})

    def stage(self, key: str, status: str, payload: Optional[dict] = None,
              merge: bool = True) -> None:
        rec = self.stages[key]
        rec["status"] = status
        if payload:
            if merge:
                rec["payload"].update(payload)
            else:
                rec["payload"] = payload
        self._append_event({
            "type": "stage",
            "stage": key,
            "status": status,
            "title": rec["title"],
            "payload": rec["payload"],
        })

    def error(self, message: str) -> None:
        self.set_status("failed")
        self._append_event({"type": "error", "message": message})

    def push_round(self, rec: dict) -> None:
        """Record a new review round (config + diff, decision still pending)."""
        self.rounds.append(rec)
        self._append_event({"type": "rounds", "rounds": self.rounds})

    def resolve_round(self, round_number: int, accepted: bool, feedback: str) -> None:
        """Fill in the operator's decision for a round once it's made."""
        for r in self.rounds:
            if r["round"] == round_number:
                r["accepted"] = accepted
                r["feedback"] = feedback
                break
        self._append_event({"type": "rounds", "rounds": self.rounds})

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status,
            "created_at": self.created_at,
            "project_id": self.project_id,
            "topology_name": self.topology_name,
            "stages": [self.stages[k] for k in STAGE_ORDER],
            "rounds": self.rounds,
        }

    def events_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] >= seq]


RUNS: dict[str, RunRecord] = {}
ACTIVE_LOCK = threading.Lock()
ACTIVE_RUN_ID: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# stdout tee: refine live stage granularity from the backend's print markers.
# ──────────────────────────────────────────────────────────────────────────


class _StageTee(io.TextIOBase):
    """Writes through to the real stdout and feeds complete lines to a handler."""

    def __init__(self, real, on_line):
        self._real = real
        self._on_line = on_line
        self._buf = ""

    def write(self, s):
        self._real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                self._on_line(line)
            except Exception:
                pass
        return len(s)

    def flush(self):
        self._real.flush()


def _make_marker_handler(run: RunRecord):
    """Maps known backend print markers to live stage transitions.

    Updated for the new graph nodes' actual print statements
    (agentic_graph_nodes.py) — the old markers ("Running step",
    "gather_state", "Generating ansible_playbook") no longer appear
    anywhere in the new pipeline's output.
    """

    def handle(line: str) -> None:
        if "[route]" in line:
            run.set_status("decomposing")
            run.stage("decompose", "active")
        elif "[cumulate]" in line:
            run.stage("decompose", "done")
        elif "[codegen]" in line:
            run.set_status("generating")
            run.stage("generate", "active")
        elif "[apply_node] Applying playbook" in line:
            run.set_status("rehearsing")
            run.stage("rehearse", "active")

    return handle


def _read_playbook() -> str:
    from sonic.config.settings import ANSIBLE_DIR

    path = ANSIBLE_DIR / "ansible_playbook.yaml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ──────────────────────────────────────────────────────────────────────────
# The run worker — mirrors run_pipeline's thin setup, delegates to run_plan.
# ──────────────────────────────────────────────────────────────────────────


def _bundle_payload(round_number: int, bundle, playbook: str) -> dict:
    """Serialize a DiffBundle for the review UI."""
    from sonic.hitl.feedback import facts_to_dicts

    ansible_result = getattr(bundle, "ansible_result", None) or {}
    return {
        "round": round_number,
        "human_diff": bundle.human_diff or "",
        "machine_diff": bundle.machine_diff or {},
        "facts": facts_to_dicts(bundle.facts),
        "has_changes": bundle.has_changes(),
        "playbook": playbook,
        # Apply outcome for this round, so the UI can alert when the playbook
        # was generated but did not apply (empty diff ≠ success).
        "ansible_success": ansible_result.get("success"),
        "ansible_stderr": (ansible_result.get("stderr") or "")[-2000:],
        "verification": _verification_payload(bundle),
    }


def _fact_dict(fact) -> dict:
    return {
        "device": fact.device, "kind": fact.kind, "key": fact.key,
        "value": fact.value, "expect": fact.expect,
    }


def _verification_payload(bundle) -> dict:
    """AnnotatedResult + Batfish CheckResults -> JSON for the review panel.
    pass=None means the checks did not run (out of scope, disabled, or a failed
    apply) — distinct from pass=False, which means they ran and found a defect."""
    annotated = getattr(bundle, "annotated", None)
    checks = getattr(bundle, "checks", None) or []
    return {
        "pass": getattr(bundle, "verification_pass", None),
        "satisfied": getattr(annotated, "satisfied", None),
        "ok": getattr(annotated, "ok", None),
        "matches": [_fact_dict(f) for f in getattr(annotated, "matches", [])],
        "missing": [_fact_dict(f) for f in getattr(annotated, "missing", [])],
        "unexpected": [_fact_dict(f) for f in getattr(annotated, "unexpected", [])],
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
    }


class IntentSpecRejected(Exception):
    """Operator cancelled at the IntentSpec gate — a clean stop, not a failure."""


def _intent_payload(spec, baseline_dir) -> dict:
    """Everything the Intent Review panel renders, computed once."""
    from sonic.hitl.expected import current_vlan_view
    from sonic.hitl.render import render_intent_spec
    from sonic.netfql.ir import lower
    from sonic.netfql.render import render_netfql

    current = current_vlan_view(spec, baseline_dir) if spec.category == "vlan" else None
    return {
        "category": spec.category,
        "action": spec.action,
        "targets": spec.switches,
        "vlan_ids": spec.vlan_ids,
        "statements": [s.model_dump(mode="json") for s in spec.statements],
        "rendered": render_intent_spec(spec),
        "netfql": render_netfql(lower(spec)),
        "current_view": {str(k): v for k, v in (current or {}).items()},
    }


def _run_worker(run: RunRecord) -> None:
    global ACTIVE_RUN_ID
    try:
        _execute_pipeline(run)
    except IntentSpecRejected as e:
        # Operator's own decision — a completed interaction, not an error.
        run.set_status("cancelled")
        print(f"[api] {e}")
    except Exception as e:  # surfaced to the UI, never silently swallowed
        run.error(f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        with ACTIVE_LOCK:
            ACTIVE_RUN_ID = None


def _execute_pipeline(run: RunRecord) -> None:
    from sonic.gns3.api_client import GNS3_PROJECT_ID
    from sonic.orchestrator.agentic_orchestrator import run_agentic_hitl_pipeline
    from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project

    project_id = GNS3_PROJECT_ID
    if not project_id:
        raise RuntimeError("GNS3_PROJECT_ID is not set in .env — cannot run the pipeline.")

    run.project_id = project_id
    mgmt_ips, topology_name = _load_mgmt_ips_for_project(project_id)
    run.topology_name = topology_name

    run.set_status("decomposing")
    run.stage("decompose", "active")

    def gate1_callback(result: dict) -> bool:
        run.stage("decompose", "done", {
            "combined_ir": [str(o) for o in result.get("combined_ir", [])],
            "combined_notes": result.get("combined_notes", ""),
        })
        run.stage("gate1", "awaiting_approval", {
            "combined_ir": [str(o) for o in result.get("combined_ir", [])],
            "combined_notes": result.get("combined_notes", ""),
        }, merge=False)
        run.set_status("awaiting_gate1")

        run.approval_decision = None
        run.approval_event.clear()
        run.approval_event.wait()  # blocks pipeline thread until operator answers

        approved = bool((run.approval_decision or {}).get("approved"))
        run.stage("gate1", "done" if approved else "cancelled", {"approved": approved})
        return approved

    def gate2_callback(round_number: int, bundle) -> dict:
        run.current_round = round_number
        playbook = _read_playbook()

        from sonic.svc.fabric import FABRIC
        FABRIC.request_refresh()

        run.stage("generate", "done", {"playbook": playbook, "round": round_number})

        payload = _bundle_payload(round_number, bundle, playbook)
        run.stage("rehearse", "done", {
            "round": round_number,
            "human_diff": payload["human_diff"],
            "machine_diff": payload["machine_diff"],
            "has_changes": payload["has_changes"],
            "ansible_success": payload["ansible_success"],
            "ansible_stderr": payload["ansible_stderr"],
            "verification": payload["verification"],
        })
        run.stage("review", "awaiting_review", payload, merge=False)
        run.set_status("awaiting_review")

        run.push_round({
            "round": round_number,
            "playbook": playbook,
            "human_diff": payload["human_diff"],
            "machine_diff": payload["machine_diff"],
            "has_changes": payload["has_changes"],
            "ansible_success": payload["ansible_success"],
            "accepted": None,
            "feedback": "",
        })

        from sonic.hitl.feedback import build_review_display
        print("\n" + "=" * 72)
        print(f"  HITL Review - Round {round_number} (decide in the web UI)")
        print("=" * 72)
        print(build_review_display(bundle))
        print("=" * 72)

        run.review_decision = None
        run.review_event.clear()
        run.review_event.wait()

        decision = run.review_decision or {"accepted": False, "feedback": ""}
        run.resolve_round(
            round_number,
            bool(decision.get("accepted")),
            str(decision.get("feedback", "")),
        )
        if not decision.get("accepted"):
            run.stage("review", "changes_requested", {"feedback": decision.get("feedback", "")})
        return {"accepted": bool(decision.get("accepted")), "feedback": str(decision.get("feedback", ""))}

    handler = _make_marker_handler(run)
    tee = _StageTee(_real_stdout(), handler)
    with contextlib.redirect_stdout(tee):
        final_state = run_agentic_hitl_pipeline(
            query=run.query,
            project_id=project_id,
            mgmt_ips=mgmt_ips,
            gate1_callback=gate1_callback,
            gate2_callback=gate2_callback,
        )

    _finalize(run, final_state)


def _real_stdout():
    import sys
    return sys.__stdout__ or sys.stdout


def _examples_from_selection(selected_example: dict, dataset_dir: Path) -> dict:
    """Same conversion run_pipeline does — load the ansible example file text."""
    examples: dict = {}
    ansible_ex = selected_example.get("ansible")
    if ansible_ex and ansible_ex.get("resource"):
        path = dataset_dir / ansible_ex["resource"]
        if path.exists():
            examples["ansible"] = path.read_text(encoding="utf-8")
            examples["type"] = ansible_ex.get("category")
    return examples


def _finalize(run: RunRecord, final_state: dict) -> None:
    """Fill stage panels from the returned AgenticPipelineState and close the run.

    Note: status can also be 'cancelled' (Gate #1 rejected) or
    'max_rounds_reached' (Gate #2 exhausted retries) — handled explicitly
    since neither has an ansible_result/diff_bundle to render.
    """
    status = final_state.get("status")

    if status == "cancelled":
        run.stage("gate1", "cancelled", {"approved": False})
        run.set_status("cancelled")
        return

    if status == "max_rounds_reached":
        run.stage("review", "changes_requested", {
            "rounds": final_state.get("round_number"),
            "reason": "max_rounds_reached",
        }, merge=False)
        run.set_status("done")
        return

    ansible_result = final_state.get("ansible_result") or {}
    bundle = final_state.get("diff_bundle")
    gate2_accepted = final_state.get("gate2_accepted")
    gate2_feedback = final_state.get("gate2_feedback", "")
    round_number = final_state.get("round_number")

    if final_state.get("playbook_yaml"):
        run.stage("generate", "done", {"playbook": final_state["playbook_yaml"]})

    run.stage("rehearse", "done", {
        "human_diff": getattr(bundle, "human_diff", "") or "",
        "machine_diff": getattr(bundle, "machine_diff", {}) or {},
    })

    run.stage("review", "done" if gate2_accepted else "changes_requested", {
        "accepted": bool(gate2_accepted),
        "feedback": gate2_feedback,
        "rounds": round_number,
        "ansible_success": ansible_result.get("success"),
        "ansible_stderr": (ansible_result.get("stderr") or "")[-2000:],
    }, merge=False)

    run.set_status("done")


# ──────────────────────────────────────────────────────────────────────────
# API models
# ──────────────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    query: str


class ReviewRequest(BaseModel):
    accepted: bool
    feedback: str = ""


class ApprovalRequest(BaseModel):
    approved: bool


# ──────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────


@app.get("/api/project")
def get_project():
    from sonic.gns3.api_client import GNS3_PROJECT_ID
    from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project

    if not GNS3_PROJECT_ID:
        raise HTTPException(500, "GNS3_PROJECT_ID is not set in .env")
    try:
        mgmt_ips, topology_name = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {
        "project_id": GNS3_PROJECT_ID,
        "topology_name": topology_name,
        "switches": [{"name": n, "mgmt_ip": ip} for n, ip in mgmt_ips.items()],
        # Same-origin proxied GNS3 web UI (see _proxy_gns3) — embedding the
        # :3080 URL directly is blocked by its X-Frame-Options/CSP headers.
        "gns3_web_url": f"/static/web-ui/server/1/project/{GNS3_PROJECT_ID}",
    }


@app.get("/api/preflight")
def get_preflight():
    from sonic.gns3.api_client import GNS3_PROJECT_ID
    from sonic.orchestrator.run_pipeline import _load_mgmt_ips_for_project
    from sonic.svc.preflight import run_preflight

    try:
        mgmt_ips, _ = _load_mgmt_ips_for_project(GNS3_PROJECT_ID)
        return run_preflight(project_id=GNS3_PROJECT_ID, mgmt_ips=mgmt_ips)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.on_event("startup")
def _start_fabric_poller() -> None:
    from sonic.svc.fabric import FABRIC

    FABRIC.start()


@app.get("/api/fabric")
def get_fabric():
    from sonic.svc.fabric import FABRIC

    return FABRIC.snapshot()


@app.post("/api/fabric/refresh")
def refresh_fabric():
    from sonic.svc.fabric import FABRIC

    FABRIC.request_refresh()
    return {"ok": True}


@app.get("/api/fabric/events")
def fabric_events():
    """Pushes a new snapshot whenever the poller produces one."""
    from sonic.svc.fabric import FABRIC

    def stream():
        last = -1
        while True:
            snap = FABRIC.snapshot()
            if snap["generation"] != last:
                last = snap["generation"]
                yield _sse({"type": "fabric", "fabric": snap})
            time.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/runs")
def start_run(req: RunRequest):
    global ACTIVE_RUN_ID
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "Intent (query) must not be empty.")

    with ACTIVE_LOCK:
        if ACTIVE_RUN_ID is not None:
            raise HTTPException(
                409,
                "A run is already in progress. The pipeline configures shared "
                "switches, so only one run can be active at a time.",
            )
        run_id = uuid.uuid4().hex[:12]
        run = RunRecord(run_id, query)
        RUNS[run_id] = run
        ACTIVE_RUN_ID = run_id

    threading.Thread(target=_run_worker, args=(run,), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs():
    runs = sorted(RUNS.values(), key=lambda r: r.created_at, reverse=True)
    return [
        {"id": r.id, "query": r.query, "status": r.status, "created_at": r.created_at}
        for r in runs
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    return run.snapshot()


@app.post("/api/runs/{run_id}/intent")
def submit_intent_approval(run_id: str, req: ApprovalRequest):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.status != "awaiting_approval":
        raise HTTPException(409, "This run is not currently awaiting intent approval.")
    run.approval_decision = {"approved": req.approved}
    run.approval_event.set()
    return {"ok": True}


@app.post("/api/runs/{run_id}/review")
def submit_review(run_id: str, req: ReviewRequest):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.status != "awaiting_review":
        raise HTTPException(409, "This run is not currently awaiting review.")
    run.review_decision = {"accepted": req.accepted, "feedback": req.feedback}
    run.review_event.set()
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found.")

    def event_stream():
        sent = 0
        # Prime the client with the current stage snapshot.
        yield _sse({"type": "snapshot", "run": run.snapshot()})
        while True:
            new = run.events_since(sent)
            if new:
                sent = new[-1]["seq"] + 1
                for ev in new:
                    yield _sse(ev)
            if run.status in ("done", "failed", "cancelled") and not run.events_since(sent):
                yield _sse({"type": "closed"})
                break
            time.sleep(0.25)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


# ──────────────────────────────────────────────────────────────────────────
# GNS3 web-UI reverse proxy
# ──────────────────────────────────────────────────────────────────────────
# The GNS3 server (:3080) ships its web UI with X-Frame-Options / CSP
# frame-ancestors headers that block cross-origin embedding, so the topology
# iframe renders blank. We proxy the web UI (and its /v2 API) through this
# same-origin app, strip the framing headers, and inject GNS3's Basic Auth
# server-side so the embedded UI never has to prompt for credentials.

# Headers we must not pass through: framing blocks, and hop-by-hop / length
# headers that no longer match after httpx has decoded the body.
_STRIP_RESP_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-encoding",
    "transfer-encoding",
    "content-length",
    "connection",
}


async def _proxy_gns3(request: Request) -> Response:
    from sonic.config.settings import (
        GNS3_AUTH,
        GNS3_HOST,
        GNS3_PASSWORD,
        GNS3_PORT,
        GNS3_USER,
    )

    upstream = f"http://{GNS3_HOST}:{GNS3_PORT}{request.url.path}"
    auth = (GNS3_USER, GNS3_PASSWORD) if GNS3_AUTH else None
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream_resp = await client.request(
                request.method,
                upstream,
                params=dict(request.query_params),
                content=body or None,
                headers={"accept": request.headers.get("accept", "*/*")},
                auth=auth,
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GNS3 proxy error: {type(e).__name__}: {e}")

    headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in _STRIP_RESP_HEADERS
    }
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=headers,
        media_type=upstream_resp.headers.get("content-type"),
    )


@app.api_route("/static/web-ui/{path:path}", methods=["GET", "HEAD"])
async def proxy_web_ui(path: str, request: Request) -> Response:
    return await _proxy_gns3(request)


@app.api_route("/v2/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE"])
async def proxy_gns3_api(path: str, request: Request) -> Response:
    return await _proxy_gns3(request)


# ──────────────────────────────────────────────────────────────────────────
# Static frontend (mounted last so /api/* wins)
# ──────────────────────────────────────────────────────────────────────────


@app.get("/")
def index():
    if not (FRONTEND_DIR / "index.html").exists():
        raise HTTPException(
            503,
            "Frontend is not built. Run: cd sonic/frontend && npm install && npm run build",
        )
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")