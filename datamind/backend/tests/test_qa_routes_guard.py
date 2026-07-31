"""The /qa router mutates billing state. These tests exist to prove it cannot
mount or serve outside an explicitly-configured dev box.

Every test here is a security test. If one starts failing, the QA routes are
one misconfiguration away from being live in production — treat it as a stop.
"""
import pytest
from fastapi import HTTPException

import qa_routes


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("QA_ROUTES_ENABLED", "QA_ROUTES_EMAILS", "FORCE_HTTPS",
                "DATAMIND_DB_HOST"):
        monkeypatch.delenv(var, raising=False)


def _enable(monkeypatch, emails="dev@example.com"):
    monkeypatch.setenv("QA_ROUTES_ENABLED", "true")
    monkeypatch.setenv("QA_ROUTES_EMAILS", emails)
    monkeypatch.setenv("DATAMIND_DB_HOST", "localhost")


# ── lock 1: explicit opt-in ───────────────────────────────────────────────────

def test_disabled_by_default():
    assert qa_routes.is_qa_enabled() is False


def test_enabled_only_by_explicit_true(monkeypatch):
    _enable(monkeypatch)
    assert qa_routes.is_qa_enabled() is True
    for falsy in ("false", "0", "no", "", "TRUE_ISH"):
        monkeypatch.setenv("QA_ROUTES_ENABLED", falsy)
        assert qa_routes.is_qa_enabled() is False, falsy


# ── lock 2: production signals ────────────────────────────────────────────────

def test_refuses_to_mount_when_force_https(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("FORCE_HTTPS", "true")
    assert qa_routes.is_qa_enabled() is False


@pytest.mark.parametrize("host", [
    "prod-db.internal",
    "datamind.cluster.rds.amazonaws.com",
    "my-cloudsql-instance",
    "db.salesplaypos.com",
])
def test_refuses_to_mount_on_production_looking_db_host(monkeypatch, host):
    _enable(monkeypatch)
    monkeypatch.setenv("DATAMIND_DB_HOST", host)
    assert qa_routes.is_qa_enabled() is False


# ── lock 3: mandatory allowlist ───────────────────────────────────────────────

def test_refuses_to_mount_with_empty_allowlist(monkeypatch):
    """There is deliberately no 'allow everyone' mode."""
    _enable(monkeypatch, emails="")
    assert qa_routes.is_qa_enabled() is False


def test_caller_not_in_allowlist_is_rejected(monkeypatch):
    _enable(monkeypatch, emails="dev@example.com")
    with pytest.raises(HTTPException) as exc:
        qa_routes.qa_user(user={"email": "attacker@evil.com"})
    assert exc.value.status_code == 403


def test_allowlisted_caller_passes(monkeypatch):
    _enable(monkeypatch, emails="dev@example.com,other@example.com")
    user = {"email": "Dev@Example.com"}      # case-insensitive
    assert qa_routes.qa_user(user=user) is user


# ── per-request re-check (revocation without restart) ─────────────────────────

def test_request_is_404_if_flag_flipped_off_after_mount(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("QA_ROUTES_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        qa_routes.qa_user(user={"email": "dev@example.com"})
    assert exc.value.status_code == 404


def test_request_is_404_if_production_signal_appears_after_mount(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("FORCE_HTTPS", "true")
    with pytest.raises(HTTPException) as exc:
        qa_routes.qa_user(user={"email": "dev@example.com"})
    assert exc.value.status_code == 404


# ── the router must not be mounted by default ─────────────────────────────────

def test_main_does_not_mount_qa_routes_by_default():
    """The real assertion: importing the app with no QA env vars must leave
    /qa completely absent, not merely protected."""
    import main
    # app.routes mixes Route objects with _IncludedRouter wrappers, so read
    # .path defensively rather than assuming every entry has one.
    paths = {p for p in (getattr(r, "path", None) for r in main.app.routes) if p}
    leaked = sorted(p for p in paths if p.startswith("/qa"))
    assert not leaked, f"QA routes leaked into the app: {leaked}"
