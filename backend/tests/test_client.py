"""Tests for the NuruXplore client — all HTTP is mocked.

These never hit the live API and spend zero credits. A fake session stands in
for ``requests.Session`` so we can force every failure mode we care about
(401, 429, out of credits, network/timeout) deterministically.
"""

from __future__ import annotations

import pytest
import requests

from nuruxplore.client import NuruXploreClient
from nuruxplore.errors import ApiError

BASE = "https://nuruxplore.com"  # host root; documented endpoints carry the /api prefix


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    @property
    def ok(self):
        return 200 <= self.status_code < 300


class FakeSession:
    """Returns a scripted list of responses; records every request made."""

    def __init__(self, *responses_or_exc):
        self._script = list(responses_or_exc)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._script:
            raise AssertionError("FakeSession ran out of scripted responses")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(*script, **kw) -> tuple[NuruXploreClient, FakeSession]:
    session = FakeSession(*script)
    client = NuruXploreClient(BASE, session=session, timeout=kw.pop("timeout", 30))
    client.sleep_fn = lambda _s: None  # keep tests fast and deterministic
    return client, session


LOGIN_OK = FakeResponse(200, {"token": "1|abc123", "user": {"id": 42, "credits_balance": 25}})
SEND_OK = FakeResponse(
    200,
    {
        "success": True,
        "action": "chat",
        "message": "A histogram plus a sorted table works well.",
        "credits_remaining": 24,
        "assistant_message": {"role": "assistant", "content": "A histogram..."},
    },
)
PROJECT_OK = FakeResponse(
    201,
    {"project": {"uuid": "proj-1"}, "uuid": "proj-1", "message": "Project created successfully"},
)


# ------------------------------------------------------------------ login

def test_login_stores_token_and_user():
    client, session = make_client(LOGIN_OK)
    result = client.login("a@b.c", "pw")
    assert result["token"] == "1|abc123"
    assert client.token == "1|abc123"
    assert client.user["id"] == 42
    assert session.calls[0]["url"] == f"{BASE}/api/auth/login"


def test_login_with_bad_credentials_is_auth_error():
    client, _session = make_client(FakeResponse(401, text="Unauthorized"))
    with pytest.raises(ApiError) as e:
        client.login("a@b.c", "wrong")
    assert e.value.kind == "auth"
    assert e.value.status == 401


def test_login_without_token_in_body_is_http_error():
    client, _session = make_client(FakeResponse(200, {"hello": "world"}))
    with pytest.raises(ApiError) as e:
        client.login("a@b.c", "pw")
    assert e.value.kind == "http"


# ---------------------------------------------------------------- project

def test_create_project_stores_uuid():
    client, session = make_client(PROJECT_OK)
    client.token = "1|abc123"
    uuid = client.create_project("My session")
    assert uuid == "proj-1"
    assert client.project_uuid == "proj-1"
    assert session.calls[0]["json"]["type"] == "chat"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer 1|abc123"


def test_ensure_project_reuses_remembered_uuid_without_http():
    client, session = make_client()
    client.token = "1|abc123"
    client.project_uuid = "already-have-me"
    assert client.ensure_project() == "already-have-me"
    assert session.calls == []  # no request made -> no credits/project used


def test_ensure_project_creates_when_missing():
    client, _session = make_client(PROJECT_OK)
    client.token = "1|abc123"
    assert client.ensure_project() == "proj-1"


# ---------------------------------------------------------------- messages

def test_send_message_returns_reply_and_credits():
    client, session = make_client(SEND_OK)
    client.token = "1|abc123"
    result = client.send_message("proj-1", "Hi")
    assert result["reply"] == "A histogram plus a sorted table works well."
    assert result["credits_remaining"] == 24
    assert session.calls[0]["json"] == {"message": "Hi"}


def test_out_of_credits_raises_dedicated_error():
    client, _session = make_client(FakeResponse(200, {"message": "..", "credits_remaining": 0}))
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "out_of_credits"


def test_out_of_credits_via_402():
    client, _session = make_client(FakeResponse(402, text="Insufficient credits"))
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "out_of_credits"


# ------------------------------------------------------------ rate limiting

def test_429_retries_with_backoff_then_fails_cleanly():
    too_many = FakeResponse(429, text="Too Many Requests")
    client, session = make_client(too_many, too_many, too_many, too_many)
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "rate_limit"
    # 1 initial attempt + MAX_RATE_LIMIT_ATTEMPTS retries:
    assert len(session.calls) == 1 + 3


def test_429_then_success_recovers():
    client, _session = make_client(
        FakeResponse(429, text="Too Many Requests"), SEND_OK
    )
    client.token = "1|abc123"
    result = client.send_message("proj-1", "Hello")
    assert result["reply"] == "A histogram plus a sorted table works well."


# ------------------------------------------- auth expiry / network handling

def test_expired_token_triggers_relogin_once_then_succeeds():
    # send -> 401, then login -> 200, then retried send -> 200
    client, session = make_client(
        FakeResponse(401, text="Unauthorized"), LOGIN_OK, SEND_OK
    )
    client.email, client.password = "a@b.c", "pw"
    client.token = "1|stale"
    result = client.send_message("proj-1", "Hello")
    assert result["reply"] == "A histogram plus a sorted table works well."
    assert client.token == "1|abc123"  # refreshed

    urls = [c["url"] for c in session.calls]
    assert urls[0].endswith("/messages")
    assert urls[1].endswith("/auth/login")
    assert urls[2].endswith("/messages")


def test_401_with_invalid_creds_after_relogin_fails_as_auth():
    # send -> 401, re-login -> 401 too -> should NOT loop forever
    client, _session = make_client(
        FakeResponse(401, text="Unauthorized"), FakeResponse(401, text="Unauthorized")
    )
    client.email, client.password = "a@b.c", "bad"
    client.token = "1|stale"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "auth"


def test_timeout_is_network_error():
    client, _session = make_client(requests.exceptions.Timeout("slow"))
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "network"


def test_connection_error_is_network_error():
    client, _session = make_client(requests.exceptions.ConnectionError("refused"))
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "network"


def test_unexpected_5xx_is_http_error():
    client, _session = make_client(FakeResponse(500, text="boom"))
    client.token = "1|abc123"
    with pytest.raises(ApiError) as e:
        client.send_message("proj-1", "Hello")
    assert e.value.kind == "http"
    assert e.value.status == 500
