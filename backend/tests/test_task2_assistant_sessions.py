"""W4 acceptance tests - secure, database-backed assistant sessions.

Every test uses the disposable SQLite database from conftest. No hosted service is contacted.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from app.api.v1.routers import integrations
from app.core import assistant_sessions as sessions
from app.models.crm import AssistantConversation, AssistantSession, AuditLog
from sqlalchemy import select

from task2_helpers import clear_assistant_cookies, new_session, open_assistant_session

COOKIE = sessions.COOKIE_NAME
CSRF = sessions.CSRF_HEADER


def _raw_cookie(client) -> str:
    value = client.cookies.get(COOKIE)
    assert value, "the HttpOnly session cookie was not set"
    return value


def _row(db, sid: str) -> AssistantSession:
    db.expire_all()
    row = db.execute(select(AssistantSession).where(AssistantSession.sid == sid)).scalars().first()
    assert row is not None
    return row


def _second_profile(client, api) -> str:
    import uuid
    suffix = uuid.uuid4().hex[:10]
    res = client.post(f"{api}/profiles", json={
        "full_name": f"Second Session Student {suffix}",
        "email": f"second.{suffix}@example.com",
        "phone": "+88017" + suffix[:8], "highest_qualification": "Bachelor",
        "major": "Data Science", "cgpa": 3.6, "cgpa_scale": 4.0,
        "target_degree_level": "MASTER", "target_discipline": "Data Science",
    })
    assert res.status_code == 200, res.text
    return res.json()["token"]


# ------------------------------------------------------------------ credential
def test_issue_stores_only_a_keyed_hash_and_a_separate_sid(client, api, db):
    try:
        state, _headers = open_assistant_session(client, api)
        raw = _raw_cookie(client)
        assert raw not in state.values()
        assert state["sid"] != raw
        assert len(raw) >= 43
        row = _row(db, state["sid"])
        assert row.credential_hash != raw
        assert len(row.credential_hash) == 64
        assert row.ownership == sessions.ANONYMOUS
        assert row.student_profile_id is None
        assert sessions.resolve(db, raw) is not None
    finally:
        clear_assistant_cookies(client)


def test_forged_malformed_and_unknown_credentials_are_rejected(client, api, db):
    assert sessions.resolve(db, "forged-credential") is None
    assert sessions.resolve(db, "") is None
    client.cookies.set(COOKIE, "not-a-real-session", path=sessions.COOKIE_PATH)
    res = client.get(f"{api}/assistant/session")
    assert res.status_code == 401
    assert "not-a-real-session" not in res.text
    clear_assistant_cookies(client)


def test_no_raw_credential_in_logs_audit_records_or_errors(client, api, db, caplog):
    caplog.set_level(logging.DEBUG)
    try:
        state, _headers = open_assistant_session(client, api)
        raw = _raw_cookie(client)
        assert raw not in caplog.text
        row = _row(db, state["sid"])
        assert raw not in repr(row.__dict__)
        res = client.get(f"{api}/assistant/session")
        assert raw not in res.text
        # A failed request must not echo the cookie either. The replacement character is chosen so
        # the value is *always* different: "raw[:-1] + 'x'" is the same string whenever the
        # credential already ends in 'x' (base64url contains it), which made this assertion pass
        # for the wrong reason in about one run in 64.
        tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")
        assert tampered != raw
        client.cookies.set(COOKIE, tampered, path=sessions.COOKIE_PATH)
        bad = client.get(f"{api}/assistant/session")
        assert bad.status_code == 401 and raw not in bad.text
    finally:
        clear_assistant_cookies(client)


# -------------------------------------------------------------------- lifecycle
def test_rotate_rejects_the_previous_credential(client, api, db):
    try:
        state, headers = open_assistant_session(client, api)
        old = _raw_cookie(client)
        res = client.post(f"{api}/assistant/session/rotate", headers=headers)
        assert res.status_code == 200, res.text
        new = _raw_cookie(client)
        assert new != old
        assert res.json()["csrf_token"] != headers[CSRF]
        assert sessions.resolve(db, old) is None
        assert sessions.resolve(db, new) is not None
        assert _row(db, state["sid"]).credential_generation == 1
    finally:
        clear_assistant_cookies(client)


def test_revocation_survives_a_new_database_session(client, api, db):
    try:
        state, headers = open_assistant_session(client, api)
        raw = _raw_cookie(client)
        assert client.post(f"{api}/assistant/session/revoke", headers=headers).status_code == 200
        fresh = new_session()
        try:
            assert sessions.resolve(fresh, raw) is None
            assert _row(fresh, state["sid"]).revoked_at is not None
        finally:
            fresh.close()
        assert client.get(f"{api}/assistant/session").status_code == 401
    finally:
        clear_assistant_cookies(client)


def test_idle_and_absolute_expiry_reject_an_uncleaned_row(db):
    own = new_session()
    try:
        raw, row = sessions.issue(own)
        later = sessions.now_utc() + sessions.idle_limit() + timedelta(seconds=5)
        assert sessions.resolve(own, raw, now=later) is None
    finally:
        own.close()

    own = new_session()
    try:
        raw, row = sessions.issue(own)
        row.absolute_expires_at = sessions._store_time(sessions.now_utc() - timedelta(seconds=1))
        own.commit()
        assert sessions.resolve(own, raw) is None
    finally:
        own.close()


def test_rotation_cannot_revive_an_expired_or_revoked_session(db):
    own = new_session()
    try:
        raw, row = sessions.issue(own)
        row.revoked_at = sessions._store_time(sessions.now_utc())
        own.commit()
        with pytest.raises(sessions.RotationConflict):
            sessions.rotate(own, row)
    finally:
        own.close()

    own = new_session()
    try:
        raw, row = sessions.issue(own)
        row.absolute_expires_at = sessions._store_time(sessions.now_utc() - timedelta(seconds=1))
        own.commit()
        with pytest.raises(sessions.RotationConflict):
            sessions.rotate(own, row)
    finally:
        own.close()


def test_two_rotations_cannot_both_succeed(db):
    first = new_session()
    second = new_session()
    try:
        _raw, row = sessions.issue(first)
        stale = second.get(AssistantSession, row.id)
        sessions.rotate(first, row)
        with pytest.raises(sessions.RotationConflict):
            sessions.rotate(second, stale)
    finally:
        first.close()
        second.close()


def test_revocation_racing_rotation_never_leaves_a_usable_credential(db):
    first = new_session()
    second = new_session()
    try:
        raw, row = sessions.issue(first)
        stale = second.get(AssistantSession, row.id)
        assert sessions.revoke(first, row) is True
        with pytest.raises(sessions.RotationConflict):
            sessions.rotate(second, stale)
        assert sessions.resolve(first, raw) is None
    finally:
        first.close()
        second.close()

    first = new_session()
    second = new_session()
    try:
        _old, row = sessions.issue(first)
        stale = second.get(AssistantSession, row.id)
        new_credential, _row_after = sessions.rotate(first, row)
        assert sessions.revoke(second, stale) is True
        first.expire_all()
        assert sessions.resolve(first, new_credential) is None
    finally:
        first.close()
        second.close()


# -------------------------------------------------------------------- ownership
def test_anonymous_session_with_a_valid_profile_token_is_not_bound(client, api, db, monkeypatch):
    captured = {}

    def fake_ask(db_, question, **kwargs):
        captured.update(kwargs)
        return {"answer": "ok", "intent": "general", "grounded": False, "escalated": False,
                "mode": "template", "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    try:
        state, headers = open_assistant_session(client, api)
        token = _second_profile(client, api)
        res = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "masters in data science", "profile_token": token,
            "context": {"profile": {"cgpa": 4.0}, "research_interests": ["AI"]},
        })
        assert res.status_code == 200
        assert captured["student_profile_id"] is None
        assert "profile" not in captured["context"]
        assert captured["session"].ownership == sessions.ANONYMOUS
        assert state["ownership"] == sessions.ANONYMOUS
    finally:
        clear_assistant_cookies(client)


def test_profile_bound_session_uses_its_own_profile_and_cannot_downgrade(client, api, db, monkeypatch):
    captured = {}

    def fake_ask(db_, question, **kwargs):
        captured.update(kwargs)
        return {"answer": "ok", "intent": "general", "grounded": False, "escalated": False,
                "mode": "template", "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    first_token = _second_profile(client, api)
    second_token = _second_profile(client, api)
    try:
        state, headers = open_assistant_session(client, api, profile_token=first_token)
        assert state["ownership"] == sessions.PROFILE

        # The same profile token is accepted and the captured identity is Student A.
        ok = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "masters in data science", "profile_token": first_token,
        })
        assert ok.status_code == 200
        assert captured["student_profile_id"] == state["profile_id"]
        assert captured["context"]["profile"]["cgpa"] is not None
        assert captured["session"].ownership == sessions.PROFILE

        # Student B's token against Student A's session is a mismatch: reject and revoke.
        res = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "masters in data science", "profile_token": second_token,
        })
        assert res.status_code == 409
        assert "profile" in res.text.lower()
        assert client.get(f"{api}/assistant/session").status_code == 401
        # A subsequent request is anonymous and cannot carry A's profile.
        after = client.post(f"{api}/assistant/ask", json={
            "question": "masters in data science", "profile_token": second_token,
            "session_id": "post-mismatch",
        }).json()
        assert after["session"] is None
    finally:
        clear_assistant_cookies(client)


def test_invalid_profile_token_cannot_issue_a_session(client, api):
    res = client.post(f"{api}/assistant/session", json={"profile_token": "not-a-real-profile"})
    assert res.status_code == 400
    assert "not-a-real-profile" not in res.text
    assert client.get(f"{api}/assistant/session").status_code == 401


def test_invalid_profile_token_cannot_bind_and_binding_is_explicit(client, api, db):
    try:
        state, headers = open_assistant_session(client, api)
        bad = client.post(f"{api}/assistant/session/bind", headers=headers,
                          json={"profile_token": "not-a-real-profile"})
        assert bad.status_code == 400
        assert client.get(f"{api}/assistant/session").json()["ownership"] == sessions.ANONYMOUS

        token = _second_profile(client, api)
        old = _raw_cookie(client)
        bound = client.post(f"{api}/assistant/session/bind", headers=headers,
                            json={"profile_token": token})
        assert bound.status_code == 200, bound.text
        assert bound.json()["ownership"] == sessions.PROFILE
        new = _raw_cookie(client)
        assert new != old
        assert sessions.resolve(db, old) is None
        assert sessions.resolve(db, new) is not None
        assert state["sid"] == bound.json()["sid"]
    finally:
        clear_assistant_cookies(client)


def test_profile_revocation_hook_revokes_every_associated_session(db):
    first = new_session()
    second = new_session()
    try:
        token = _second_profile_for_db(first)
        r1, _ = sessions.issue(first, profile_token=token)
        r2, _ = sessions.issue(first, profile_token=token)
        anon, _ = sessions.issue(first)
        profile_id = sessions.resolve(first, r1).student_profile_id
        assert sessions.revoke_for_profile(second, profile_id) == 2
        second.commit()
    finally:
        first.close()
        second.close()

    check = new_session()
    try:
        assert sessions.resolve(check, r1) is None
        assert sessions.resolve(check, r2) is None
        assert sessions.resolve(check, anon) is not None
    finally:
        check.close()


def _second_profile_for_db(db) -> str:
    import secrets

    from app.models.crm import StudentProfile

    token = secrets.token_urlsafe(10)
    row = StudentProfile(public_token=token, full_name="Bound Student")
    db.add(row)
    db.commit()
    return token


# -------------------------------------------------------------------- CSRF/origin
def test_cookie_authenticated_writes_require_csrf_and_check_origin(client, api):
    try:
        _state, headers = open_assistant_session(client, api)
        assert client.post(f"{api}/assistant/ask", json={"question": "masters in data science"}).status_code == 403
        assert client.post(f"{api}/assistant/session/rotate").status_code == 403
        assert client.post(f"{api}/assistant/session/rotate",
                           headers={CSRF: "wrong"}).status_code == 403
        assert client.post(f"{api}/assistant/session/rotate",
                           headers={CSRF: headers[CSRF], "Origin": "https://evil.example"}
                           ).status_code == 403
        assert client.post(f"{api}/assistant/session/rotate", headers=headers).status_code == 200
    finally:
        clear_assistant_cookies(client)


# ------------------------------------------------------------------- admin revoke
def test_admin_can_revoke_without_seeing_credentials(client, api, db, auth):
    try:
        state, _headers = open_assistant_session(client, api)
        raw = _raw_cookie(client)
        listing = client.get(f"{api}/admin/assistant/sessions", headers=auth)
        assert listing.status_code == 200
        assert raw not in listing.text
        assert any(row["sid"] == state["sid"] for row in listing.json()["items"])

        revoked = client.post(f"{api}/admin/assistant/sessions/{state['sid']}/revoke", headers=auth)
        assert revoked.status_code == 200
        assert sessions.resolve(db, raw) is None
        audit = db.execute(select(AuditLog).where(
            AuditLog.action == "ASSISTANT_SESSION_REVOKED")).scalars().all()
        assert audit and raw not in repr([a.detail for a in audit])
    finally:
        clear_assistant_cookies(client)


def test_admin_session_endpoints_reject_anonymous_and_non_admin(client, api, student_token):
    assert client.get(f"{api}/admin/assistant/sessions").status_code == 401
    assert client.get(f"{api}/admin/assistant/sessions",
                      headers={"Authorization": "Bearer not-a-token"}).status_code == 401


# ------------------------------------------------------------------ conversation link
def test_verified_conversation_rows_are_linked_to_the_session(client, api, db):
    try:
        state, headers = open_assistant_session(client, api)
        res = client.post(f"{api}/assistant/ask", headers=headers,
                          json={"question": "masters in data science"})
        assert res.status_code == 200
        row = _row(db, state["sid"])
        db.expire_all()
        conversation = db.execute(
            select(AssistantConversation)
            .where(AssistantConversation.session_ref_id == row.id)
            .order_by(AssistantConversation.id.desc())
        ).scalars().first()
        assert conversation is not None
        assert conversation.session_id == state["sid"]
    finally:
        clear_assistant_cookies(client)


def test_legacy_conversations_are_preserved_without_trusted_ownership(db):
    own = new_session()
    try:
        row = AssistantConversation(session_id="legacy-client-id", question="q", answer="a",
                                    intent="general", grounded=False)
        own.add(row)
        own.commit()
        stored = own.execute(
            select(AssistantConversation).where(
                AssistantConversation.session_id == "legacy-client-id")
        ).scalars().first()
        assert stored is not None
        assert stored.session_ref_id is None
    finally:
        own.query(AssistantConversation).filter(
            AssistantConversation.session_id == "legacy-client-id").delete(synchronize_session=False)
        own.commit()
        own.close()


def test_cookie_is_host_only_httponly_and_narrowly_scoped(client, api):
    try:
        res = client.post(f"{api}/assistant/session", json={})
        assert res.status_code == 200
        header = res.headers["set-cookie"].lower()
        assert "httponly" in header
        assert "samesite=lax" in header
        assert "path=/api/v1/assistant" in header
        assert "domain=" not in header
    finally:
        clear_assistant_cookies(client)


def test_a_failed_commit_does_not_issue_a_rotation_credential(db):
    from unittest.mock import patch

    own = new_session()
    try:
        raw, row = sessions.issue(own)
        with patch.object(own, "commit", side_effect=RuntimeError("commit failed")):
            with pytest.raises(RuntimeError):
                sessions.rotate(own, row)
        own.rollback()
        assert sessions.resolve(own, raw) is not None
        assert row.credential_generation == 0
    finally:
        own.close()


def test_production_cookie_is_secure_and_default_secret_is_refused(monkeypatch, db):
    from types import SimpleNamespace

    from app.core.config import settings
    from fastapi import Response

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "assistant_session_secret", "production-session-secret")
    row = SimpleNamespace(sid="sid", credential_generation=0,
                          absolute_expires_at=sessions._store_time(
                              sessions.now_utc() + timedelta(hours=1)))
    response = Response()
    sessions.set_cookie(response, "raw-credential", row)
    header = response.headers["set-cookie"].lower()
    assert "secure" in header and "httponly" in header

    monkeypatch.setattr(settings, "assistant_session_secret", "")
    monkeypatch.setattr(settings, "secret_key", "dev-secret-change-me")
    with pytest.raises(sessions.SessionError) as caught:
        sessions.issue(db)
    assert caught.value.code == "SESSION_KEY_UNCONFIGURED"


def test_explicit_profile_switch_revokes_old_credential_and_binds_new(client, api, db):
    first_token = _second_profile(client, api)
    second_token = _second_profile(client, api)
    try:
        first_state, headers = open_assistant_session(client, api, profile_token=first_token)
        old_raw = _raw_cookie(client)
        switched = client.post(f"{api}/assistant/session/bind", headers=headers,
                               json={"profile_token": second_token})
        assert switched.status_code == 200, switched.text
        body = switched.json()
        assert body["ownership"] == sessions.PROFILE
        assert body["profile_id"] != first_state["profile_id"]
        new_raw = _raw_cookie(client)
        assert new_raw != old_raw
        assert sessions.resolve(db, old_raw) is None
        assert sessions.resolve(db, new_raw) is not None
        # The old session's CSRF token cannot authorise the new binding either.
        stale_rotate = client.post(f"{api}/assistant/session/rotate",
                                   headers={CSRF: headers[CSRF]})
        assert stale_rotate.status_code in (403, 401)
    finally:
        clear_assistant_cookies(client)


def test_a_profile_bound_session_without_the_current_token_is_revoked(client, api, db):
    token = _second_profile(client, api)
    try:
        state, headers = open_assistant_session(client, api, profile_token=token)
        raw = _raw_cookie(client)
        res = client.post(f"{api}/assistant/ask", headers=headers,
                          json={"question": "masters in data science"})
        assert res.status_code == 409
        assert sessions.resolve(db, raw) is None
        assert client.get(f"{api}/assistant/session").status_code == 401
        assert state["ownership"] == sessions.PROFILE
    finally:
        clear_assistant_cookies(client)


def test_a_stale_cookie_cannot_bind_after_a_profile_transition(client, api, db):
    first_token = _second_profile(client, api)
    second_token = _second_profile(client, api)
    try:
        _state, headers = open_assistant_session(client, api, profile_token=first_token)
        old_raw = _raw_cookie(client)
        first = client.post(f"{api}/assistant/session/bind", headers=headers,
                            json={"profile_token": second_token})
        assert first.status_code == 200
        # Replay the old cookie, as a delayed bootstrap response might.
        client.cookies.set(COOKIE, old_raw, path=sessions.COOKIE_PATH)
        replay = client.get(f"{api}/assistant/session")
        assert replay.status_code == 401
        assert sessions.resolve(db, old_raw) is None
    finally:
        clear_assistant_cookies(client)


def test_the_public_sid_cannot_authenticate_by_itself(client, api):
    try:
        state, _headers = open_assistant_session(client, api)
        client.cookies.set(COOKIE, state["sid"], path=sessions.COOKIE_PATH)
        assert client.get(f"{api}/assistant/session").status_code == 401
    finally:
        clear_assistant_cookies(client)


def test_the_old_csrf_token_fails_after_rotation(client, api):
    try:
        _state, headers = open_assistant_session(client, api)
        old_csrf = headers[CSRF]
        rotated = client.post(f"{api}/assistant/session/rotate", headers=headers)
        assert rotated.status_code == 200
        blocked = client.post(f"{api}/assistant/ask",
                              headers={CSRF: old_csrf},
                              json={"question": "masters in data science"})
        assert blocked.status_code == 403
    finally:
        clear_assistant_cookies(client)


def test_a_valid_non_admin_staff_token_cannot_manage_sessions(client, api, db, auth):
    from app.core.security import create_access_token
    from app.models.crm import User

    counselor = db.execute(select(User).where(User.role == "COUNSELOR")).scalars().first()
    assert counselor is not None
    token = create_access_token(subject=str(counselor.id), role=counselor.role)
    res = client.get(f"{api}/admin/assistant/sessions",
                     headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_idle_expiry_is_enforced_through_the_http_endpoint(client, api, db):
    try:
        state, _headers = open_assistant_session(client, api)
        row = _row(db, state["sid"])
        row.last_seen_at = sessions._store_time(
            sessions.now_utc() - sessions.idle_limit() - timedelta(seconds=10))
        db.commit()
        assert client.get(f"{api}/assistant/session").status_code == 401
    finally:
        clear_assistant_cookies(client)


def test_session_and_answer_responses_are_not_cacheable(client, api):
    try:
        _state, headers = open_assistant_session(client, api)
        state = client.get(f"{api}/assistant/session")
        assert "no-store" in state.headers.get("cache-control", "").lower()
        answer = client.post(f"{api}/assistant/ask", headers=headers,
                             json={"question": "masters in data science"})
        assert answer.status_code == 200
        assert "no-store" in answer.headers.get("cache-control", "").lower()
    finally:
        clear_assistant_cookies(client)


def test_anonymous_session_becomes_profile_bound_only_through_explicit_bind(client, api, db):
    token = _second_profile(client, api)
    try:
        state, headers = open_assistant_session(client, api)
        assert state["ownership"] == sessions.ANONYMOUS
        bound = client.post(f"{api}/assistant/session/bind", headers=headers,
                            json={"profile_token": token})
        assert bound.status_code == 200
        assert bound.json()["ownership"] == sessions.PROFILE
    finally:
        clear_assistant_cookies(client)


def test_logout_reauth_transition_leaves_old_profile_unreachable(client, api, db, monkeypatch):
    captured = {}

    def fake_ask(db_, question, **kwargs):
        captured.update(kwargs)
        return {"answer": "ok", "intent": "general", "grounded": False, "escalated": False,
                "mode": "template", "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    first_token = _second_profile(client, api)
    second_token = _second_profile(client, api)
    try:
        first_state, headers = open_assistant_session(client, api, profile_token=first_token)
        # A stale request from Student A is rejected once the browser presents Student B.
        mismatch = client.post(f"{api}/assistant/ask", headers=headers,
                               json={"question": "cost", "profile_token": second_token})
        assert mismatch.status_code == 409
        # Re-bootstrap as B, which is what the frontend does after the 409.
        fresh = client.post(f"{api}/assistant/session", json={"profile_token": second_token})
        assert fresh.status_code == 200
        csrf = fresh.json()["csrf_token"]
        ok = client.post(f"{api}/assistant/ask", headers={CSRF: csrf},
                         json={"question": "cost", "profile_token": second_token})
        assert ok.status_code == 200
        assert captured["student_profile_id"] == fresh.json()["profile_id"]
        assert captured["student_profile_id"] != first_state["profile_id"]
    finally:
        clear_assistant_cookies(client)


def test_a_rotation_during_generation_discards_the_answer(client, api, db, monkeypatch):
    def fake_ask(db_, question, **kwargs):
        other = new_session()
        try:
            row = other.get(AssistantSession, kwargs["session"].id)
            sessions.rotate(other, row)
        finally:
            other.close()
        return {"answer": "STALE ANSWER", "intent": "general", "grounded": True,
                "escalated": False, "mode": "template",
                "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    try:
        _state, headers = open_assistant_session(client, api)
        res = client.post(f"{api}/assistant/ask", headers=headers,
                          json={"question": "masters in data science"})
        assert res.status_code == 409
        assert "STALE ANSWER" not in res.text
        assert "session changed" in res.text.lower()
    finally:
        clear_assistant_cookies(client)


def test_a_revocation_during_generation_discards_the_answer(client, api, db, monkeypatch):
    def fake_ask(db_, question, **kwargs):
        other = new_session()
        try:
            row = other.get(AssistantSession, kwargs["session"].id)
            sessions.revoke(other, row, "REVOKED_DURING_GENERATION")
        finally:
            other.close()
        return {"answer": "STALE ANSWER", "intent": "general", "grounded": True,
                "escalated": False, "mode": "template",
                "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    try:
        _state, headers = open_assistant_session(client, api)
        res = client.post(f"{api}/assistant/ask", headers=headers,
                          json={"question": "masters in data science"})
        assert res.status_code == 409
        assert "STALE ANSWER" not in res.text
    finally:
        clear_assistant_cookies(client)


def test_a_rebind_during_generation_discards_the_old_profile_answer(client, api, db, monkeypatch):
    first_token = _second_profile(client, api)
    second_token = _second_profile(client, api)

    def fake_ask(db_, question, **kwargs):
        other = new_session()
        try:
            row = other.get(AssistantSession, kwargs["session"].id)
            sessions.switch_profile(other, row, second_token)
        finally:
            other.close()
        return {"answer": "OLD PROFILE ANSWER", "intent": "general", "grounded": True,
                "escalated": False, "mode": "template",
                "domain": {"decision": "ALLOW", "reason": "in_scope"}}

    monkeypatch.setattr(integrations.assistant_engine, "ask", fake_ask)
    try:
        _state, headers = open_assistant_session(client, api, profile_token=first_token)
        res = client.post(f"{api}/assistant/ask", headers=headers,
                          json={"question": "cost", "profile_token": first_token})
        assert res.status_code == 409
        assert "OLD PROFILE ANSWER" not in res.text
    finally:
        clear_assistant_cookies(client)


def test_an_ask_refreshes_idle_activity(client, api, db):
    try:
        state, headers = open_assistant_session(client, api)
        row = _row(db, state["sid"])
        row.last_seen_at = sessions._store_time(
            sessions.now_utc() - sessions.idle_limit() + timedelta(minutes=5))
        db.commit()
        before = row.last_seen_at
        assert client.post(f"{api}/assistant/ask", headers=headers,
                           json={"question": "masters in data science"}).status_code == 200
        db.expire_all()
        after = _row(db, state["sid"]).last_seen_at
        assert after > before
        assert client.get(f"{api}/assistant/session").status_code == 200
    finally:
        clear_assistant_cookies(client)
