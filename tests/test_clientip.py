"""Offline tests for app.clientip — resolving the caller behind a proxy."""

from types import SimpleNamespace

from app.clientip import client_ip

INGRESS = "100.100.0.9"  # inside 100.64.0.0/10, the Container Apps ingress range


def _request(peer, forwarded_for=None):
    headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_without_trusted_networks_the_header_is_ignored(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)

    assert client_ip(_request(INGRESS, "203.0.113.5")) == INGRESS


def test_trusted_ingress_yields_the_forwarded_client(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")

    assert client_ip(_request(INGRESS, "203.0.113.5")) == "203.0.113.5"


def test_client_supplied_entries_left_of_the_ingress_entry_are_ignored(monkeypatch):
    # The caller sent "X-Forwarded-For: 198.51.100.7"; the ingress appended
    # the real address, so the rightmost untrusted entry wins.
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")

    assert client_ip(_request(INGRESS, "198.51.100.7, 203.0.113.5")) == "203.0.113.5"


def test_chained_trusted_proxies_are_skipped(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10, 10.0.0.0/8")

    assert client_ip(_request(INGRESS, "203.0.113.5, 10.1.2.3")) == "203.0.113.5"


def test_untrusted_peer_keeps_its_own_address(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")

    assert client_ip(_request("198.51.100.7", "203.0.113.5")) == "198.51.100.7"


def test_unparseable_forwarded_entry_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")

    assert client_ip(_request(INGRESS, "not-an-address")) == INGRESS


def test_only_trusted_entries_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")

    assert client_ip(_request(INGRESS, "100.100.0.1")) == INGRESS


def test_invalid_cidr_entries_are_ignored(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "garbage, 100.64.0.0/10")

    assert client_ip(_request(INGRESS, "203.0.113.5")) == "203.0.113.5"
