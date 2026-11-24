"""
Integration conformance.

These tests answer one question: when the consultancy finally pastes a WhatsApp token or
an xAI key, will the code actually work? Until now the honest answer was "we think so" -
every integration test mocked the HTTP layer, so the request we would really put on the
wire had never been examined by anything.

Here the real client functions run against `tests/conformance_server.py`, which speaks the
vendors' documented formats. That cannot prove a credential is valid or that Meta behaves
as documented, but it does prove our half: the URL, the auth header, the body shape, the
response parsing, the failover order and the error handling.
"""
from __future__ import annotations

import pytest
import asyncio

from fastapi.testclient import TestClient

from tests.conformance_server import RECEIVED, VALID_TOKEN, app as harness_app


@pytest.fixture
def harness(monkeypatch):
    """Point every integration at the local harness instead of the real vendors."""
    client = TestClient(harness_app)
    RECEIVED.clear()

    import httpx

    real_client_cls = httpx.Client
    real_async_cls = httpx.AsyncClient

    def _route(url: str) -> str:
        return url.replace("http://conformance", "")

    class RoutedClient(real_client_cls):          # type: ignore[misc]
        def request(self, method, url, **kw):     # noqa: ANN001
            if str(url).startswith("http://conformance"):
                return client.request(method, _route(str(url)), **{
                    k: v for k, v in kw.items() if k in {"json", "headers", "params", "content"}})
            return super().request(method, url, **kw)

    class RoutedAsyncClient(real_async_cls):      # type: ignore[misc]
        async def request(self, method, url, **kw):   # noqa: ANN001
            if str(url).startswith("http://conformance"):
                return client.request(method, _route(str(url)), **{
                    k: v for k, v in kw.items() if k in {"json", "headers", "params", "content"}})
            return await super().request(method, url, **kw)

    monkeypatch.setattr(httpx, "Client", RoutedClient)
    monkeypatch.setattr(httpx, "AsyncClient", RoutedAsyncClient)
    return client


# ------------------------------------------------------------------------ WhatsApp
def test_whatsapp_send_puts_the_right_request_on_the_wire(harness, monkeypatch):
    """
    The most common WhatsApp Cloud API mistake is omitting messaging_product, which Meta
    rejects with error 100. A mocked test would never catch it; the harness does.
    """
    from app.core.config import get_settings
    from app.services import whatsapp

    settings = get_settings()
    monkeypatch.setattr(settings, "meta_graph_base_url", "http://conformance", raising=False)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123456", raising=False)
    monkeypatch.setattr(settings, "whatsapp_access_token", VALID_TOKEN, raising=False)

    result = asyncio.run(whatsapp.send_message("+8801700000000",
                                               "Your appointment is confirmed."))

    sent = [r for r in RECEIVED if r["kind"] == "whatsapp_send"]
    assert sent, "the client never reached the endpoint"
    body = sent[0]["body"]
    assert body["messaging_product"] == "whatsapp", "Meta rejects a body without this"
    assert body["to"] == "+8801700000000"
    assert sent[0]["authorization"] == f"Bearer {VALID_TOKEN}"
    assert result["status"] == 200


def test_whatsapp_rejects_a_bad_token_without_raising(harness, monkeypatch):
    """A wrong credential must surface as a handled failure, never a 500 for the student."""
    from app.core.config import get_settings
    from app.services import whatsapp

    settings = get_settings()
    monkeypatch.setattr(settings, "meta_graph_base_url", "http://conformance", raising=False)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123456", raising=False)
    monkeypatch.setattr(settings, "whatsapp_access_token", "wrong-token", raising=False)

    outcome = asyncio.run(whatsapp.send_message("+8801700000000", "hello"))
    assert outcome["status"] == 401, "a rejected token must be reported, not swallowed"


# ----------------------------------------------------------------------- LLM layer
def _configure_llm(monkeypatch, **keys):
    from app.core.config import get_settings
    settings = get_settings()
    for provider in ("xai", "mistral", "gemini"):
        monkeypatch.setattr(settings, f"{provider}_base_url", "http://conformance", raising=False)
        monkeypatch.setattr(settings, f"{provider}_api_key", keys.get(provider, ""), raising=False)
        monkeypatch.setattr(settings, f"{provider}_model", f"{provider}-test-model", raising=False)
    monkeypatch.setattr(settings, "llm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider_order", "grok,mistral,gemini", raising=False)
    return settings


def test_a_real_completion_round_trip(harness, monkeypatch):
    from app.core import llm

    _configure_llm(monkeypatch, xai="good-key")
    result = llm.complete("You are a counsellor.", "What IELTS score does Aalto need?")

    assert result is not None, "a configured, responding provider returned nothing"
    assert "CONFORMANCE REPLY" in result.text
    assert result.provider == "grok"

    calls = [r for r in RECEIVED if r["kind"] == "chat_completions"]
    assert calls[0]["authorization"] == "Bearer good-key"
    assert calls[0]["body"]["model"] == "xai-test-model"
    assert calls[0]["body"]["messages"][-1]["content"].startswith("What IELTS")


def test_failover_moves_to_the_next_provider(harness, monkeypatch):
    """
    The provider order is the whole value of configuring three keys. If the first one is
    rejected the student must still get an answer, from the second.
    """
    from app.core import llm

    _configure_llm(monkeypatch, xai="rejected-key", mistral="good-key")
    result = llm.complete("You are a counsellor.", "Tell me about Spain.")

    assert result is not None, "failover did not happen"
    assert result.provider == "mistral", "it did not fall through to the second provider"
    assert len([r for r in RECEIVED if r["kind"] == "chat_completions"]) == 2


def test_every_provider_failing_returns_none_rather_than_raising(harness, monkeypatch):
    """The assistant degrades to retrieval-only; it must never 500."""
    from app.core import llm

    _configure_llm(monkeypatch, xai="rejected-key", mistral="rejected-key",
                   gemini="rate-limited-key")
    assert llm.complete("You are a counsellor.", "hello") is None


def test_a_rate_limited_provider_is_treated_as_a_failure_not_a_reply(harness, monkeypatch):
    from app.core import llm

    _configure_llm(monkeypatch, xai="rate-limited-key", mistral="good-key")
    result = llm.complete("You are a counsellor.", "hello")
    assert result is not None and result.provider == "mistral"


# --------------------------------------------------------------- Conversions API
def test_capi_hashes_personal_data_before_it_leaves_the_server(harness, monkeypatch, client):
    """
    The Conversions API carries a student's email and phone to Meta. Sending either in
    the clear would be a privacy breach that no amount of later configuration undoes, so
    this asserts on the exact bytes we put on the wire: the SHA-256 digests must be
    present and the raw values must appear nowhere at all.
    """
    import hashlib
    import json as _json

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "meta_graph_base_url", "http://conformance", raising=False)
    monkeypatch.setattr(settings, "meta_pixel_id", "PIXEL123", raising=False)
    monkeypatch.setattr(settings, "meta_capi_access_token", VALID_TOKEN, raising=False)

    email, phone = "Rahim.Ahmed@Example.COM", "+8801711111111"
    response = client.post("/api/v1/marketing/conversion", json={
        "event_name": "Lead", "email": email, "phone": phone,
        "event_source_url": "https://scholara.com.bd/contact",
        "value": 0, "currency": "BDT",
    })
    assert response.status_code == 200

    sent = [r for r in RECEIVED if r["kind"] == "capi_events"]
    assert sent, "the conversion never reached the endpoint"
    raw = _json.dumps(sent[0]["body"])

    assert email not in raw and email.lower() not in raw, "a raw email was sent to Meta"
    assert phone not in raw, "a raw phone number was sent to Meta"

    user_data = sent[0]["body"]["data"][0]["user_data"]
    assert user_data["em"] == hashlib.sha256(email.strip().lower().encode()).hexdigest()
    assert user_data["ph"] == hashlib.sha256(phone.strip().lower().encode()).hexdigest()


def test_capi_without_credentials_logs_locally_instead_of_failing(harness, client):
    """No pixel configured must degrade quietly - a lead is never lost to a missing token."""
    response = client.post("/api/v1/marketing/conversion", json={
        "event_name": "Lead", "email": "a@b.com", "event_source_url": "https://x", "value": 0})
    assert response.status_code == 200
    assert response.json()["status"] in {"LOGGED_LOCALLY", 200}
