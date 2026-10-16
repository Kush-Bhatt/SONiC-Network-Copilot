"""
src/gns3/api_client.py
-----------------------
GNS3 REST API client.

Project selection priority:
  1. GNS3_PROJECT_ID   — exact project_id from .env
  2. GNS3_PROJECT_NAME — matched by name from .env
  3. Auto: only opened project (if exactly one)
  4. Fallback: first opened project (with a warning)
"""

import time
import os
import requests
from requests.auth import HTTPBasicAuth

from sonic.config.settings import (
    GNS3_BASE_URL, GNS3_AUTH, GNS3_USER, GNS3_PASSWORD,
)

# Optional project targeting — read directly from env so settings.py
# doesn't need updating if user hasn't added them yet
GNS3_PROJECT_ID   = os.getenv("GNS3_PROJECT_ID",   "").strip()
GNS3_PROJECT_NAME = os.getenv("GNS3_PROJECT_NAME", "").strip()

AUTH = HTTPBasicAuth(GNS3_USER, GNS3_PASSWORD) if GNS3_AUTH else None


def _get(path: str, **kwargs) -> requests.Response:
    url = f"{GNS3_BASE_URL}{path}"
    r = requests.get(url, auth=AUTH, timeout=10, **kwargs)
    r.raise_for_status()
    return r


def get_all_projects() -> list[dict]:
    """Return all projects on the GNS3 server."""
    return _get("/projects").json()


def get_active_project_id() -> str:
    """
    Resolve which project to use.

    Priority:
      1. GNS3_PROJECT_ID env var
      2. GNS3_PROJECT_NAME env var (matched by name)
      3. Single opened project (auto-select)
      4. First opened project (fallback, prints warning)
    """
    projects = get_all_projects()

    # 1. Explicit ID
    if GNS3_PROJECT_ID:
        ids = {p["project_id"] for p in projects}
        if GNS3_PROJECT_ID in ids:
            return GNS3_PROJECT_ID
        raise ValueError(
            f"GNS3_PROJECT_ID='{GNS3_PROJECT_ID}' not found on server. "
            f"Available: {[p['project_id'] for p in projects]}"
        )

    # 2. By name
    if GNS3_PROJECT_NAME:
        match = [p for p in projects if p["name"] == GNS3_PROJECT_NAME]
        if match:
            return match[0]["project_id"]
        raise ValueError(
            f"GNS3_PROJECT_NAME='{GNS3_PROJECT_NAME}' not found. "
            f"Available names: {[p['name'] for p in projects]}"
        )

    # 3/4. Auto from opened projects
    opened = [p for p in projects if p.get("status") == "opened"]

    if not opened:
        raise ValueError("No opened GNS3 projects found. Open a project in GNS3 first.")

    if len(opened) == 1:
        return opened[0]["project_id"]

    # Multiple opened — warn and pick first, but print hint
    print(
        f"[!] WARNING: Multiple opened GNS3 projects found:\n"
        + "\n".join(f"    [{p['name']}]  id={p['project_id']}" for p in opened)
        + f"\n    Selecting first: '{opened[0]['name']}'\n"
        + f"    To pin your project, add to .env:\n"
        + f"      GNS3_PROJECT_ID={opened[0]['project_id']}\n"
        + f"    OR\n"
        + f"      GNS3_PROJECT_NAME={opened[0]['name']}"
    )
    return opened[0]["project_id"]


def get_nodes(project_id: str = None) -> list[dict]:
    """Fetch all nodes for the active (or specified) project."""
    if project_id is None:
        project_id = get_active_project_id()
    return _get(f"/projects/{project_id}/nodes").json()


def get_links(project_id: str = None) -> list[dict]:
    """Fetch all links for the active (or specified) project."""
    if project_id is None:
        project_id = get_active_project_id()
    return _get(f"/projects/{project_id}/links").json()



def _post(path: str, **kwargs) -> requests.Response:
    url = f"{GNS3_BASE_URL}{path}"
    r = requests.post(url, auth=AUTH, timeout=60, **kwargs)
    r.raise_for_status()
    return r


def export_project(project_id: str, dst_path: str) -> None:
    """
    Downloads a portable .gns3project archive (topology definition only —
    NOT VM disk images, unless include_images=true is added as a query
    param, which we deliberately don't do here).
    """
    url = f"{GNS3_BASE_URL}/projects/{project_id}/export"
    with requests.get(url, auth=AUTH, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dst_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    print(f"[gns3] Exported project {project_id} -> {dst_path}")


def import_project(archive_path: str, new_project_id: str = None, name: str = None) -> dict:
    """
    Imports a .gns3project archive as a NEW project (does not touch the
    original). Returns the created project's metadata dict.
    """
    import uuid
    pid = new_project_id or str(uuid.uuid4())
    params = {}
    if name:
        params["name"] = name

    with open(archive_path, "rb") as f:
        resp = _post(f"/projects/{pid}/import", params=params,
                      data=f, headers={"Content-Type": "application/octet-stream"})
    print(f"[gns3] Imported as new project_id={pid}")
    return resp.json()

def close_project(project_id: str = None) -> dict:
    if project_id is None:
        project_id = get_active_project_id()
    resp = _post(f"/projects/{project_id}/close")
    print(f"[gns3] Closed project {project_id}")
    return resp.json() if resp.content else {}


def open_project(project_id: str) -> dict:
    resp = _post(f"/projects/{project_id}/open")
    print(f"[gns3] Opened project {project_id}")
    return resp.json()

def start_all_nodes(project_id: str) -> None:
    resp = _post(f"/projects/{project_id}/nodes/start")
    print(f"[gns3] Started all nodes in project {project_id}")


def stop_all_nodes(project_id: str) -> None:
    resp = _post(f"/projects/{project_id}/nodes/stop")
    print(f"[gns3] Stopped all nodes in project {project_id}")


# BUILD TOPOLOGY

def create_project(name: str) -> str:
    resp = _post("/projects", json={"name": name, "auto_close": False})
    return resp.json()["project_id"]


def create_node(project_id: str, name: str, template_id: str, x: int, y: int,
                 compute_id: str = None) -> dict:
    payload = {"x": x, "y": y}
    if compute_id:
        payload["compute_id"] = compute_id
    resp = _post(f"/projects/{project_id}/templates/{template_id}", json=payload)
    node = resp.json()
    print(f"[gns3] Created node {name} ({node['node_id']})")
    return node



def rename_node(project_id: str, node_id: str, name: str) -> dict:
    import requests
    url = f"{GNS3_BASE_URL}/projects/{project_id}/nodes/{node_id}"
    r = requests.put(url, auth=AUTH, json={"name": name}, timeout=30)
    r.raise_for_status()
    return r.json()




def create_link(project_id: str, node_a: str, port_a: int, node_b: str, port_b: int,
                 adapter_a: int = 0, adapter_b: int = 0, retries: int = 3) -> dict:
    payload = {
        "nodes": [
            {"node_id": node_a, "adapter_number": adapter_a, "port_number": port_a},
            {"node_id": node_b, "adapter_number": adapter_b, "port_number": port_b},
        ]
    }
    last_exc = None
    for attempt in range(retries):
        try:
            resp = _post(f"/projects/{project_id}/links", json=payload)
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                print(f"    [info] link already exists (409) — treating as success")
                return {"already_exists": True}
            last_exc = e
            print(f"    [retry {attempt+1}/{retries}] link creation failed: {e}")
            time.sleep(2)
    raise last_exc

def get_node_console(project_id: str, node_id: str) -> dict:
    """Returns console host/port for telnet access to a node's serial console."""
    node = _get(f"/projects/{project_id}/nodes/{node_id}").json()
    return {"host": node.get("console_host"), "port": node.get("console")}