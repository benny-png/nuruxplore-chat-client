"""App-level tests via FastAPI TestClient.

We never call the live API: ``app.main._load_session`` is monkeypatched to return
a fake session whose client hands back scripted results or raises
:class:`ApiError`. This proves the HTTP→error-kind mapping the UI depends on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m
from nuruxplore.errors import ApiError


class FakeClient:
    def __init__(self):
        self.user = {"name": "Intern", "credits_balance": 150}
        self._behavior = {}

    def set(self, method: str, value):
        self._behavior[method] = value
        return self

    def __getattr__(self, name):
        def _call(*a, **k):
            v = self._behavior.get(name, {})
            if isinstance(v, Exception):
                raise v
            return v
        return _call


class FakeSession:
    def __init__(self, client):
        self.client = client
        self.email = "e"
        self.password = "p"

    def ensure_login(self):
        return self.client


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(m, "_load_session", lambda request: (FakeSession(fake), "sid"))
    monkeypatch.setattr(m, "_sessions", {})
    with TestClient(m.app) as c:
        yield c, fake


# ------------------------------------------------------------- mapping

def test_out_of_credits_maps_to_402(client):
    c, fake = client
    fake.set("generate_complete", ApiError("out_of_credits", "Insufficient credits.", 402))
    r = c.post("/api/local/projects/proj-1/generate-complete", json={"type": "proposal"})
    assert r.status_code == 402
    assert r.json()["kind"] == "out_of_credits"


def test_rate_limit_maps_to_429(client):
    c, fake = client
    fake.set("generate_outline", ApiError("rate_limit", "Limit.", 429))
    r = c.post("/api/local/projects/proj-1/generate-outline")
    assert r.status_code == 429
    assert r.json()["kind"] == "rate_limit"


def test_auth_maps_to_401(client):
    c, fake = client
    fake.set("build_research_profile", ApiError("auth", "Bad creds.", 401))
    r = c.post("/api/local/projects/proj-1/build-research-profile")
    assert r.status_code == 401
    assert r.json()["kind"] == "auth"


def test_network_maps_to_502(client):
    c, fake = client
    fake.set("generate_outline", ApiError("network", "Timeout."))
    r = c.post("/api/local/projects/proj-1/generate-outline")
    assert r.status_code == 502
    assert r.json()["kind"] == "network"


def test_failed_generation_job_flagged(client):
    c, fake = client
    fake.set(
        "generation_status",
        {"success": True, "status": "failed", "progress": 100, "current_step": "Failed", "steps": [], "word_count": 0},
    )
    r = c.get("/api/local/projects/proj-1/generation-status")
    body = r.json()
    assert r.status_code == 200
    assert body["kind"] == "generation_failed"
    assert "refunded" in body["message"]


def test_completed_generation_passes_through(client):
    c, fake = client
    fake.set(
        "generation_status",
        {"success": True, "status": "completed", "progress": 100, "current_step": "Completed", "steps": [], "word_count": 9360},
    )
    r = c.get("/api/local/projects/proj-1/generation-status")
    assert r.json()["status"] == "completed"
    assert "kind" not in r.json()


def test_me_returns_credits(client):
    c, fake = client
    r = c.get("/api/local/me")
    assert r.status_code == 200
    assert r.json()["credits_balance"] == 150


def test_export_pdf_returns_download_url(client):
    c, fake = client
    fake.set("export_document", {"download_url": "https://nuruxplore.com/exports/x.pdf", "message": "ok"})
    r = c.post("/api/local/projects/proj-1/export/pdf")
    assert r.status_code == 200
    assert r.json()["download_url"].startswith("https://nuruxplore.com/exports/")


def test_upload_source_route_forwards_multipart(client):
    c, fake = client
    fake.set("upload_source", {"source": {"id": 106, "has_extracted_text": True}})
    r = c.post(
        "/api/local/projects/proj-1/upload",
        files={"file": ("prop.pdf", b"%PDF-1.7 fake", "application/pdf")},
        data={"document_role": "proposal", "type": "proposal"},
    )
    assert r.status_code == 200
    assert r.json()["source"]["has_extracted_text"] is True


def test_upload_source_route_maps_http_error(client):
    c, fake = client
    fake.set("upload_source", ApiError("http", "422 upload failed", 422))
    r = c.post(
        "/api/local/projects/proj-1/upload",
        files={"file": ("prop.pdf", b"%PDF-1.7 fake", "application/pdf")},
    )
    assert r.status_code == 502
    assert r.json()["kind"] == "http"
