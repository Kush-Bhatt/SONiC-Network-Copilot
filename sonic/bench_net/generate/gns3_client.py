"""
sonic/bench_net/generate/gns3_client.py
------------------------------------------
Minimal standalone GNS3 REST client — only the 4 calls bench_net
actually uses. Independent copy of sonic/gns3/api_client.py; not
imported from there on purpose (see module docstrings elsewhere in
bench_net for why).
"""
import requests
from requests.auth import HTTPBasicAuth

from sonic.bench_net.config import GNS3_BASE_URL, GNS3_USER, GNS3_PASSWORD, GNS3_AUTH

AUTH = HTTPBasicAuth(GNS3_USER, GNS3_PASSWORD) if GNS3_AUTH else None


def _post(path: str, **kwargs) -> requests.Response:
    url = f"{GNS3_BASE_URL}{path}"
    r = requests.post(url, auth=AUTH, timeout=60, **kwargs)
    r.raise_for_status()
    return r


def close_project(project_id: str) -> dict:
    resp = _post(f"/projects/{project_id}/close")
    print(f"[gns3] Closed project {project_id}")
    return resp.json() if resp.content else {}


def open_project(project_id: str) -> dict:
    resp = _post(f"/projects/{project_id}/open")
    print(f"[gns3] Opened project {project_id}")
    return resp.json()


def export_project(project_id: str, dst_path: str) -> None:
    """Downloads a portable .gns3project archive (topology definition
    only — NOT VM disk images)."""
    url = f"{GNS3_BASE_URL}/projects/{project_id}/export"
    with requests.get(url, auth=AUTH, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dst_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    print(f"[gns3] Exported project {project_id} -> {dst_path}")


def start_all_nodes(project_id: str) -> None:
    _post(f"/projects/{project_id}/nodes/start")
    print(f"[gns3] Started all nodes in project {project_id}")
