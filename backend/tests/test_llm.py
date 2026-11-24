"""Multi-provider LLM layer (Grok / Mistral / Gemini) and the generation guardrails.

Two things are under test and the second one is the important one:

1. ``core.llm`` — provider resolution, priority order, failover, kill switch, key redaction.
2. ``services.assistant_engine`` guardrails — a hostile model must not be able to put an
   admission probability, a guarantee or an unsourced email in front of a student. This is the
   executable form of the hardest rule in docs/05.

No network is used: a local HTTP server stands in for api.x.ai, api.mistral.ai and
generativelanguage.googleapis.com, all three of which speak the same wire format.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core import llm
from app.core.config import settings
from app.services import assistant_engine as engine

# --------------------------------------------------------------------------- fake provider
GOOD_REPLY = "Annual tuition is USD 28,000, verified on 12 March 2026."
HOSTILE_REPLY = (
    "You have a 95% admission chance at this university and I guarantee you qualify. "
    "The visa success rate is 98%. Email the professor at fake.prof@gmail.com "
    "or supervisor@notreal.edu for a guaranteed offer."
)


class _Handler(BaseHTTPRequestHandler):
    reply = GOOD_REPLY
    status = 200
    calls: list[dict] = []

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        body = json.loads(raw) if raw else {}
        type(self).calls.append(
            {"model": body.get("model"), "auth": self.headers.get("Authorization", "")}
        )
        if type(self).status != 200:
            self.send_response(type(self).status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        payload = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": type(self).reply}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence
        return


@pytest.fixture(scope="module")
def fake_provider():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture(autouse=True)
def _clean_settings():
    """Every test starts from 'no provider configured' and restores the real settings after."""
    keys = [
        "llm_enabled", "llm_provider_order", "xai_api_key", "xai_model", "xai_base_url",
        "mistral_api_key", "mistral_model", "mistral_base_url",
        "gemini_api_key", "gemini_model", "gemini_base_url",
    ]
    saved = {k: getattr(settings, k) for k in keys}
    settings.llm_enabled = True
    settings.llm_provider_order = "grok,mistral,gemini"
    for provider in ("xai", "mistral", "gemini"):
        setattr(settings, f"{provider}_api_key", "")
    _Handler.reply = GOOD_REPLY
    _Handler.status = 200
    _Handler.calls = []
    yield
    for key, value in saved.items():
        setattr(settings, key, value)


# ----------------------------------------------------------------- configuration & order
def test_no_provider_configured_means_template_mode():
    assert llm.available() is False
    assert llm.complete("system", "user") is None
    assert llm.health()["mode"] == "template"


def test_provider_order_is_parsed_deduplicated_and_filtered():
    settings.llm_provider_order = "gemini, mistral ,gemini,nonsense,grok"
    assert llm.provider_order() == ["gemini", "mistral", "grok"]


def test_unparseable_order_falls_back_to_the_full_catalogue():
    settings.llm_provider_order = "nope,alsonope"
    assert llm.provider_order() == list(llm.PROVIDER_KEYS)


def test_every_catalogued_provider_resolves_with_a_default_model_and_base_url():
    for key in llm.PROVIDER_KEYS:
        provider = llm._provider(key)
        assert provider is not None
        assert provider.model, f"{key} has no default model"
        assert provider.base_url.startswith("https://"), f"{key} base URL is not https"
        assert provider.endpoint.endswith("/chat/completions")


def test_defaults_point_at_the_three_intended_vendors():
    hosts = {p.key: p.base_url for p in llm.providers()}
    assert "api.x.ai" in hosts["grok"]
    assert "api.mistral.ai" in hosts["mistral"]
    assert "generativelanguage.googleapis.com" in hosts["gemini"]


# ----------------------------------------------------------------------------- dispatch
def test_unconfigured_providers_are_skipped_not_called(fake_provider):
    settings.mistral_api_key = "key-mistral"
    settings.mistral_base_url = fake_provider
    result = llm.complete("system", "user")
    assert result is not None
    assert result.provider == "mistral"
    statuses = {a["provider"]: a["status"] for a in result.attempts}
    assert statuses["grok"] == "skipped"
    assert statuses["mistral"] == "ok"
    assert "gemini" not in statuses  # never reached: we stop at the first success


def test_first_configured_provider_in_order_wins(fake_provider):
    for provider in ("xai", "mistral", "gemini"):
        setattr(settings, f"{provider}_api_key", f"key-{provider}")
        setattr(settings, f"{provider}_base_url", fake_provider)
    assert llm.complete("system", "user").provider == "grok"

    settings.llm_provider_order = "gemini,grok,mistral"
    assert llm.complete("system", "user").provider == "gemini"


def test_a_failing_provider_fails_over_to_the_next(fake_provider):
    settings.xai_api_key = "key-grok"
    settings.xai_base_url = "http://127.0.0.1:1/v1"  # nothing listening
    settings.mistral_api_key = "key-mistral"
    settings.mistral_base_url = fake_provider

    result = llm.complete("system", "user")
    assert result.provider == "mistral"
    statuses = {a["provider"]: a["status"] for a in result.attempts}
    assert statuses["grok"] == "error"
    assert statuses["mistral"] == "ok"


def test_an_upstream_error_status_is_a_failure_not_an_answer(fake_provider):
    settings.mistral_api_key = "key-mistral"
    settings.mistral_base_url = fake_provider
    _Handler.status = 429
    assert llm.complete("system", "user") is None
    assert "429" in llm.health()["providers"][1]["last_error"]


def test_all_providers_down_returns_none_so_the_caller_can_fall_back():
    settings.xai_api_key = "k"
    settings.xai_base_url = "http://127.0.0.1:1/v1"
    settings.mistral_api_key = "k"
    settings.mistral_base_url = "http://127.0.0.1:1/v1"
    assert llm.complete("system", "user") is None


def test_the_configured_model_name_is_what_gets_sent(fake_provider):
    settings.gemini_api_key = "key-gemini"
    settings.gemini_base_url = fake_provider
    settings.gemini_model = "gemini-2.5-flash"
    settings.llm_provider_order = "gemini"
    result = llm.complete("system", "user")
    assert result.model == "gemini-2.5-flash"
    assert _Handler.calls[-1]["model"] == "gemini-2.5-flash"
    assert _Handler.calls[-1]["auth"] == "Bearer key-gemini"


def test_llm_enabled_is_a_kill_switch(fake_provider):
    settings.mistral_api_key = "key-mistral"
    settings.mistral_base_url = fake_provider
    assert llm.available() is True
    settings.llm_enabled = False
    assert llm.available() is False
    assert llm.complete("system", "user") is None


def test_gemini_style_list_content_is_flattened(fake_provider):
    """Gemini's compatibility layer can return content as a list of parts."""
    settings.gemini_api_key = "k"
    settings.gemini_base_url = fake_provider
    settings.llm_provider_order = "gemini"

    class _ListHandler(_Handler):
        pass

    original = _Handler.do_POST

    def _list_post(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        payload = json.dumps(
            {"choices": [{"message": {"content": [{"text": "part one "}, {"text": "part two"}]}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    _Handler.do_POST = _list_post
    try:
        assert llm.complete("s", "u").text == "part one part two"
    finally:
        _Handler.do_POST = original


# ------------------------------------------------------------------------- secret hygiene
def test_health_never_exposes_key_material(fake_provider):
    settings.xai_api_key = "xai-supersecret-abc123"
    settings.mistral_api_key = "mistral-supersecret-def456"
    settings.gemini_api_key = "gemini-supersecret-ghi789"
    blob = json.dumps(llm.health(), default=str)
    for secret in ("xai-supersecret-abc123", "mistral-supersecret-def456", "gemini-supersecret-ghi789"):
        assert secret not in blob
    assert "api_key" not in blob


def test_health_reports_presence_priority_and_env_var_names():
    settings.mistral_api_key = "k"
    health = llm.health()
    by_key = {p["provider"]: p for p in health["providers"]}
    assert by_key["grok"]["configured"] is False
    assert by_key["mistral"]["configured"] is True
    assert by_key["grok"]["env_key"] == "XAI_API_KEY"
    assert by_key["mistral"]["env_key"] == "MISTRAL_API_KEY"
    assert by_key["gemini"]["env_key"] == "GEMINI_API_KEY"
    assert [p["priority"] for p in health["providers"]] == [1, 2, 3]
    assert health["active_provider"] == "mistral"
    assert health["mode"] == "llm"


def test_a_key_appearing_in_an_upstream_error_is_redacted():
    settings.xai_api_key = "leaky-key-value"
    assert "leaky-key-value" not in llm._redact("auth failed for leaky-key-value at endpoint")
    assert "***redacted***" in llm._redact("auth failed for leaky-key-value")


def test_ping_reports_a_missing_key_clearly_without_calling_out():
    result = llm.ping("gemini")
    assert result["ok"] is False
    assert "GEMINI_API_KEY" in result["error"]


def test_ping_round_trips_a_configured_provider(fake_provider):
    settings.xai_api_key = "key-grok"
    settings.xai_base_url = fake_provider
    result = llm.ping("grok")
    assert result["ok"] is True
    assert result["latency_ms"] >= 0
    assert result["reply"]


def test_ping_rejects_an_unknown_provider():
    assert llm.ping("chatgpt")["ok"] is False


# ---------------------------------------------------------------------------- guardrails
HOSTILE_SENTENCES = [
    "You have a 95% admission chance at this university.",
    "I guarantee you qualify for the scholarship.",
    "The visa success rate is 98%.",
    "Your chances of getting in are high.",
    "There is a 70 percent probability of acceptance.",
    "You will definitely get an offer from this university.",
    "Your admission chance is strong.",
    "You are fully eligible for this award.",
    "You will receive the scholarship.",
    "This is a guaranteed offer.",
    "Odds of admission are about 1 in 3.",
    "Your profile gives you good odds.",
    "You qualify for a full tuition waiver.",
    "Admission success rate for your profile is 80%.",
]

LEGITIMATE_SENTENCES = [
    "The scholarship covers 50% of tuition for the first year.",
    "IELTS 6.5 overall with no band below 6.0 is required.",
    "Annual tuition is 24,500 USD, verified on 12 March 2026.",
    "Your profile appears to meet the stated eligibility criteria.",
    "The January 2027 intake closes on 15 October 2026.",
    "This figure is marked NEEDS_REVIEW, so confirm it with the university.",
    "A 2.5% late fee applies after the deadline.",
    "The university reports 32% international students.",
    "Eligibility status: PARTIALLY_MEETS - your CGPA is below the stated minimum.",
    "You can book a free counseling session at our Uttara office.",
]


@pytest.mark.parametrize("sentence", HOSTILE_SENTENCES)
def test_a_prohibited_claim_is_removed_whole(sentence):
    clean, violations = engine._apply_guardrails(sentence, set())
    body = clean.split(engine.QUARANTINE_NOTE)[0].strip()
    assert body == "", f"survived the guardrail: {body!r}"
    assert violations


@pytest.mark.parametrize("sentence", LEGITIMATE_SENTENCES)
def test_a_legitimate_sentence_is_left_alone(sentence):
    clean, violations = engine._apply_guardrails(sentence, set())
    assert clean == sentence
    assert violations == []


def test_the_quarantine_notice_cannot_trip_its_own_policy():
    """The explanation must not contain the phrases it is explaining."""
    patterns = [
        r"\d{1,3}\s?%\s*(chance|probability|likelihood)",
        r"(high|good|strong|low|poor)\s+chance\s+of\s+(admission|acceptance)",
        r"guaranteed\s+(admission|offer|scholarship|visa)",
        r"you\s+will\s+(get|be)\s+(admitted|accepted)",
    ]
    for pattern in patterns:
        assert not re.search(pattern, engine.QUARANTINE_NOTE, re.I)
    clean, violations = engine._apply_guardrails(engine.QUARANTINE_NOTE, set())
    assert clean == engine.QUARANTINE_NOTE
    assert violations == []


def test_partial_rewriting_never_leaves_the_number_behind():
    """The regression this design exists for: scrubbing words left '...is 98%' in the text."""
    clean, _ = engine._apply_guardrails("The visa success rate is 98%.", set())
    assert "98" not in clean
    assert "%" not in clean


def test_a_good_sentence_survives_alongside_a_quarantined_one():
    text = "Annual tuition is USD 28,000. You have a 90% chance of admission."
    clean, violations = engine._apply_guardrails(text, set())
    assert "USD 28,000" in clean
    assert "90" not in clean
    assert engine.QUARANTINE_NOTE in clean
    assert "admission_probability" in violations


def test_sentence_splitting_is_lossless_and_protects_dotted_spans():
    samples = [
        "Contact a.b@uta.edu for details.",
        "IELTS 6.5 overall is required. Tuition is 24.5k USD.",
        "See www.uta.edu/fees for Dr. Chowdhury.",
        "Visit https://example.edu/a.b.c now. Then apply.",
    ]
    for text in samples:
        assert "".join(engine._sentences(text)) == text
    assert len(engine._sentences("Contact a.b@uta.edu for details.")) == 1


def test_an_unsourced_email_is_replaced_not_the_whole_sentence():
    clean, violations = engine._apply_guardrails(
        "Email the supervisor at random.person@gmail.com about the lab.", set()
    )
    assert "random.person@gmail.com" not in clean
    assert "about the lab" in clean
    assert "unsourced_email" in violations


def test_an_approved_email_passes_through():
    clean, violations = engine._apply_guardrails(
        "Email a.chowdhury@uta.edu about the lab.", {"a.chowdhury@uta.edu"}
    )
    assert "a.chowdhury@uta.edu" in clean
    assert violations == []


def test_an_email_split_across_a_quarantined_sentence_cannot_leak():
    """Regression: the splitter once cut through addresses, leaving fragments behind."""
    clean, _ = engine._apply_guardrails(
        "Contact fake.prof@gmail.com or supervisor@notreal.edu for a guaranteed offer.", set()
    )
    assert "@" not in clean
    assert "notreal" not in clean


# -------------------------------------------------------------- end to end through ask()
def test_a_hostile_model_cannot_reach_the_student(client, api, db, fake_provider):
    """Everything the model says is prohibited, so the deterministic answer is served."""
    settings.xai_api_key = "key-grok"
    settings.xai_base_url = fake_provider
    _Handler.reply = HOSTILE_REPLY

    result = engine.ask(db, "What are my chances at UT Arlington for the MSc in AI?")

    patterns = [
        r"\d{1,3}\s?%\s*(chance|probability|likelihood)",
        r"(high|good|strong|low|poor)\s+chance\s+of\s+(admission|acceptance)",
        r"guaranteed\s+(admission|offer|scholarship|visa)",
        r"you\s+will\s+(get|be)\s+(admitted|accepted)",
    ]
    for pattern in patterns:
        assert not re.search(pattern, result["answer"], re.I)
    assert not re.search(r"\d+\s*%", result["answer"])
    assert "@gmail.com" not in result["answer"]
    assert "notreal" not in result["answer"]
    # the whole generation was rejected, so we fell back to the sourced template
    assert result["mode"] == "template"


def test_a_well_behaved_model_is_used_and_attributed(client, api, db, fake_provider):
    settings.mistral_api_key = "key-mistral"
    settings.mistral_base_url = fake_provider
    settings.mistral_model = "mistral-large-latest"
    settings.llm_provider_order = "mistral"
    _Handler.reply = GOOD_REPLY

    result = engine.ask(db, "What is the tuition for the MSc in Artificial Intelligence?")
    assert result["mode"] == "llm"
    assert result["provider"] == "mistral"
    assert result["model"] == "mistral-large-latest"
    assert GOOD_REPLY in result["answer"]


def test_the_answer_records_which_provider_served_it(client, api, db):
    """With no provider configured the fields are present and null, not missing."""
    result = engine.ask(db, "What is the tuition for the MSc in Artificial Intelligence?")
    assert result["mode"] == "template"
    assert result["provider"] is None
    assert result["model"] is None


# --------------------------------------------------------------------------- admin surface
def test_provider_status_endpoint_requires_staff_and_hides_secrets(client, api, auth):
    settings.xai_api_key = "xai-supersecret-abc123"
    anonymous = client.get(f"{api}/admin/assistant/providers")
    assert anonymous.status_code in (401, 403)

    response = client.get(f"{api}/admin/assistant/providers", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert "xai-supersecret-abc123" not in json.dumps(body)
    assert [p["provider"] for p in body["providers"]] == ["grok", "mistral", "gemini"]
    assert body["providers"][0]["configured"] is True


def test_provider_test_endpoint_404s_on_an_unknown_provider(client, api, auth):
    response = client.post(f"{api}/admin/assistant/providers/openai/test", headers=auth)
    assert response.status_code == 404


def test_provider_test_endpoint_reports_a_missing_key(client, api, auth):
    response = client.post(f"{api}/admin/assistant/providers/gemini/test", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "GEMINI_API_KEY" in body["error"]
