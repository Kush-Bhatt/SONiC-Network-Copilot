"""
sonic/hitl/normalize.py
------------------------
One canonicalization vocabulary, called from BOTH the expected-facts path
(agents/intent_spec -> hitl/expected) and the actual-facts path
(hitl/expected reading the raw snapshot tier).

Comparison in Idea 1 is string equality on 4-tuples. If the two sides
normalize differently we get false mismatches — the exact false-alarm
problem the deterministic checks exist to remove. So every identity that
crosses the comparison passes through here first.

Pure functions, no I/O, no LLM.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

_INTF_RE = re.compile(r"^(eth(?:ernet)?|po(?:rtchannel)?|port-channel)(\d+)$", re.IGNORECASE)


def norm_device(name: str) -> str:
    """Device names -> lowercase. Batfish lowercases node names, so `SONiC-VS-1`
    from the IntentSpec and `sonic-vs-1` from a Batfish frame are the same box."""
    return str(name).strip().lower()


def norm_intf(iface: str) -> str:
    """Interfaces -> canonical SONiC long form. `eth16`, `Eth16`, `Ethernet 16`
    -> `Ethernet16`; `po1`, `port-channel1` -> `PortChannel1`. Unknown forms are
    returned stripped, unchanged."""
    t = str(iface).strip().replace(" ", "")
    m = _INTF_RE.match(t)
    if not m:
        return str(iface).strip()
    prefix, num = m.group(1).lower(), int(m.group(2))
    if prefix.startswith("eth"):
        return f"Ethernet{num}"
    return f"PortChannel{num}"


def norm_ip(addr: str) -> str:
    """IP addresses -> `ipaddress` object then str(), giving one canonical
    string for equality. (config_db/FRR/Batfish emit canonical dotted-quad;
    leading-zero octets are rejected by `ipaddress`, which is correct — they
    are ambiguous.)"""
    return str(IPv4Address(str(addr).strip()))


def norm_ip_iface(addr: str) -> str:
    """Address-with-prefix (an SVI address like `192.168.77.1/24`) -> canonical."""
    return str(IPv4Interface(str(addr).strip()))


def norm_prefix(prefix: str) -> str:
    """Prefixes -> CIDR. `10.0.0.0 255.255.255.0` -> `10.0.0.0/24`."""
    s = str(prefix).strip()
    if " " in s and "/" not in s:
        addr, mask = s.split()
        s = f"{addr}/{mask}"
    return str(IPv4Network(s, strict=False))


def norm_asn(asn) -> str:
    """ASNs -> int then str(). Never compare `"65001"` to `65001`."""
    return str(int(str(asn).strip()))


def norm_svi(vlan_id) -> str:
    """SVI names -> `Vlan<id>`, no space, matching SONiC's config_db form."""
    return f"Vlan{int(vlan_id)}"
