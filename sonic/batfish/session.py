from pybatfish.client.session import Session
from pybatfish.datamodel import *
from sonic.config.settings import BATFISH_HOST

SNAPSHOT_PATH = "snapshots/sonic_configs"
NETWORK_NAME  = "sonic-network"
SNAPSHOT_NAME = "sonic-snapshot"

def get_session(overwrite=True) -> Session:
    """
    Connects to Batfish server, loads snapshot.
    Returns bf object ready for querying.
    """
    bf = Session(host=BATFISH_HOST)
    bf.set_network(NETWORK_NAME)
    bf.init_snapshot(SNAPSHOT_PATH, name=SNAPSHOT_NAME, overwrite=overwrite)
    print("[+] Batfish session ready")
    return bf