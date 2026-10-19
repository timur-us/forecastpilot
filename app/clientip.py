"""Client address for rate limiting, aware of a trusted reverse proxy.

Behind Azure Container Apps the TCP peer of every request is the ingress,
which appends the caller's address to X-Forwarded-For. Only peers from the
networks listed in TRUSTED_PROXY_CIDRS count as proxies: the header is then
read from the right, trusted hops are skipped, and the first address outside
those networks is the client. A caller that sends its own X-Forwarded-For
only adds entries to the left of the one the ingress appends, so they are
never reached. Without trusted networks (local runs) the header is ignored
and the peer address is the client.
"""

import ipaddress
import os

from starlette.requests import Request


def trusted_proxy_networks() -> list:
    """Networks from TRUSTED_PROXY_CIDRS (comma-separated CIDRs)."""
    networks = []
    for item in os.getenv("TRUSTED_PROXY_CIDRS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(address: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_ip(request: Request) -> str:
    """Rate-limit key: the caller's address as seen by the trusted ingress."""
    peer = request.client.host if request.client else "127.0.0.1"
    networks = trusted_proxy_networks()
    if not networks or not _is_trusted(peer, networks):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
    for hop in reversed([h for h in hops if h]):
        if _is_trusted(hop, networks):
            continue
        try:
            return str(ipaddress.ip_address(hop))
        except ValueError:
            return peer
    return peer
