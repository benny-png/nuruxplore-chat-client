"""Tests for the proposal/thesis generation flow — all HTTP is mocked.

These never touch the live API and spend zero credits. A fake session stands in
for ``requests.Session`` so we can script the whole multi-step flow and every
failure mode deterministically, mirroring the real 12-endpoint contract.
"""

from __future__ import annotations

import pytest

from tests.test_client import BASE, FakeResponse, FakeSession, make_client
from nuruxplore.client import NuruXploreClient
from nuruxplore.errors import ApiError

PROJ = "proj-abc"
TYPE = "proposal"


def build_ok():
    return FakeResponse(
        200,
        {
            "success": True,
            "profile": {
                "title": "Impact of mobile money on smallholder farmers",
                "background": ["...", "..."],
                "methodology": {"design": "descriptive cross-sectional"},
                "document_type": "proposal",
            },
            "credits_remaining": 147,
            "message": "Research profile generated.",
        },
    )


def approve_ok():
    return FakeResponse(200, {"success": True, "message": "Research profile approved."})


def outline_ok():
    return FakeResponse(
        200,
        {
            "success": True,
            "outline": [{"title": "Introduction", "subsections": []}],
            "sections": [{"id": 1, "title": "Introduction"}],
            "credits_remaining": 142,
            "message": "Outline generated successfully with 6 chapters.",
        },
    )


def gen_complete_queued():
    return FakeResponse(
        202, {"success": True, "queued": True, "project_uuid": PROJ, "message": "Document generation started."}
    )


def status_response(status, progress=0, current_step="", steps=None, word_count=0):
    return FakeResponse(
        200,
        {
            "success": True,
            "status": status,
            "progress": progress,
            "current_step": current_step,
            "steps": steps or [],
            "content_ready": status == "completed",
            "word_count": word_count,
        },
    )


# ---------------------------------------------------------- happy path

def test_full_proposal_flow_end_to_end():
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {"credits_balance": 150}}),  # login
        FakeResponse(201, {"project": {"uuid": PROJ}}),  # create project
        build_ok(),  # build profile
        approve_ok(),  # approve
        outline_ok(),  # outline
        gen_complete_queued(),  # generate complete -> queued
        status_response("queued"),
        status_response("generating", 40, "Writing Literature Review", [
            {"step": "profile", "status": "completed", "message": "Profile ready."},
            {"step": "sections", "status": "processing", "message": "Writing..."},
        ]),
        status_response("completed", 100, "Completed", [], word_count=9360),
        FakeResponse(200, {"download_url": "https://nuruxplore.com/exports/x.pdf"}),
    )
    client.login("a@b.c", "pw")
    assert client.create_project("My proposal", type="proposal") == PROJ

    built = client.build_research_profile(PROJ)
    assert built["success"] and built["profile"]["document_type"] == "proposal"

    approved = client.approve_research_profile(PROJ, research_profile=built["profile"])
    assert approved["success"]

    outline = client.generate_outline(PROJ)
    assert outline["outline"][0]["title"] == "Introduction"

    queued = client.generate_complete(PROJ, TYPE)
    assert queued["queued"] is True

    # poll to completion
    statuses = []
    while True:
        s = client.generation_status(PROJ)
        statuses.append(s["status"])
        if s["status"] in ("completed", "failed"):
            assert s["status"] == "completed"
            assert s["word_count"] == 9360
            break
    assert "generating" in statuses  # we actually saw intermediate progress

    exp = client.export_document(PROJ, "pdf")
    assert exp["download_url"].startswith("https://nuruxplore.com/exports/")


def test_approve_profile_without_edits_sends_empty_body():
    session = FakeSession(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        FakeResponse(200, {"success": True}),
    )
    client = NuruXploreClient(BASE, session=session)
    client.sleep_fn = lambda _s: None
    client.login("a@b.c", "pw")
    client.approve_research_profile(PROJ)
    sent = session.calls[-1]
    assert sent["json"] == {}


def test_generate_complete_charges_upfront_and_queues():
    client, session = make_client(
        FakeResponse(200, {"token": "1|t", "user": {"credits_balance": 150}}),
        gen_complete_queued(),
    )
    client.login("a@b.c", "pw")
    res = client.generate_complete(PROJ, "thesis")
    assert res["queued"] is True
    assert session.calls[-1]["json"] == {"type": "thesis"}


# ------------------------------------------------------ failure modes

def test_generate_complete_402_surfaces_out_of_credits():
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {"credits_balance": 500}}),
        FakeResponse(402, None, "Insufficient credits."),
    )
    client.login("a@b.c", "pw")
    with pytest.raises(ApiError) as exc:
        client.generate_complete(PROJ, "thesis")
    assert exc.value.kind == "out_of_credits"
    assert exc.value.status == 402


def test_generate_outline_429_surfaces_rate_limit():
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        FakeResponse(429, None, "Too many requests"),  # initial
        FakeResponse(429, None, "Too many requests"),  # retry #1
        FakeResponse(429, None, "Too many requests"),  # retry #2
        FakeResponse(429, None, "Too many requests"),  # retry #3
    )
    client.login("a@b.c", "pw")
    with pytest.raises(ApiError) as exc:
        client.generate_outline(PROJ)
    assert exc.value.kind == "rate_limit"


def test_build_profile_401_surfaces_auth():
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        FakeResponse(401, None, "Unauthorized"),
    )
    # No stored credentials -> the client cannot auto-reauth, so a 401 surfaces
    # as ApiError("auth") instead of silently retrying.
    client.email = None
    client.password = None
    client.login("a@b.c", "pw")
    with pytest.raises(ApiError) as exc:
        client.build_research_profile(PROJ)
    assert exc.value.kind == "auth"


def test_generation_status_failed_is_returned_for_ui_to_show():
    session = FakeSession(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        status_response("failed", 100, "Failed", [], word_count=0),
    )
    client = NuruXploreClient(BASE, session=session)
    client.sleep_fn = lambda _s: None
    client.login("a@b.c", "pw")
    s = client.generation_status(PROJ)
    assert s["status"] == "failed"  # the UI maps this to a distinct banner


def test_network_timeout_surfaces_network():
    import requests
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        requests.exceptions.Timeout("slow"),
    )
    client.login("a@b.c", "pw")
    with pytest.raises(ApiError) as exc:
        client.build_research_profile(PROJ)
    assert exc.value.kind == "network"


# ------------------------------------------------------------- upload

def _pdf(tmp_path, name="proposal.pdf") -> str:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.7 fake content")
    return str(p)


def upload_ok():
    return FakeResponse(
        200,
        {"source": {"id": 106, "has_extracted_text": True, "extraction_status": "completed"}},
    )


def test_upload_proposal_posts_multipart_source(tmp_path):
    client, session = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        upload_ok(),
    )
    client.login("a@b.c", "pw")
    res = client.upload_proposal(PROJ, _pdf(tmp_path), title="My proposal")
    assert res["source"]["has_extracted_text"] is True
    call = session.calls[-1]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/sources/upload")
    assert call["data"]["project_uuid"] == PROJ
    assert call["data"]["document_role"] == "proposal"
    assert call["data"]["type"] == "proposal"
    assert "file" in call["files"]  # the multipart file is attached


def test_upload_dataset_sets_dataset_role(tmp_path):
    client, session = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        upload_ok(),
    )
    client.login("a@b.c", "pw")
    client.upload_source(PROJ, _pdf(tmp_path, "survey.xlsx"), type="dataset", document_role="dataset")
    call = session.calls[-1]
    assert call["data"]["project_uuid"] == PROJ
    assert call["data"]["document_role"] == "dataset"
    assert call["data"]["type"] == "dataset"


def test_upload_source_422_surfaces_error(tmp_path):
    client, _ = make_client(
        FakeResponse(200, {"token": "1|t", "user": {}}),
        FakeResponse(422, None, "No extracted proposal/source text found."),
    )
    client.login("a@b.c", "pw")
    with pytest.raises(ApiError) as exc:
        client.upload_proposal(PROJ, _pdf(tmp_path))
    assert exc.value.kind == "http"
    assert exc.value.status == 422
