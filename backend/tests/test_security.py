"""Security surface: RBAC, MFA, signed document URLs, webhook signatures, rate limits."""
from __future__ import annotations

import time

import pytest

from app.core import mfa, storage, webhooks


# ------------------------------------------------------------------ RBAC
def test_admin_endpoints_reject_anonymous(client, api):
    for path in ["/admin/dashboard", "/admin/leads", "/admin/audit/log", "/admin/counselor/dashboard"]:
        assert client.get(f"{api}{path}").status_code == 401, path


def test_counselor_cannot_delete(client, api):
    token = client.post(f"{api}/admin/auth/login", json={
        "email": "counselor@scholara.com.bd", "password": "Counselor@123",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # Counselors may read…
    assert client.get(f"{api}/admin/leads", headers=headers).status_code == 200
    # …but deletion needs SENIOR_COUNSELOR or above.
    assert client.delete(f"{api}/admin/countries/1", headers=headers).status_code == 403


def test_audit_log_requires_senior_role(client, api):
    token = client.post(f"{api}/admin/auth/login", json={
        "email": "counselor@scholara.com.bd", "password": "Counselor@123",
    }).json()["access_token"]
    res = client.get(f"{api}/admin/audit/log", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_immutable_resources_cannot_be_written(client, api, auth):
    for resource in ["audit-logs", "analytics-events", "assistant-conversations", "search-queries"]:
        assert client.put(f"{api}/admin/{resource}/1", json={"x": 1}, headers=auth).status_code == 405
        assert client.delete(f"{api}/admin/{resource}/1", headers=auth).status_code == 405


# ------------------------------------------------------------------- MFA
def test_totp_roundtrip():
    secret = mfa.generate_secret()
    assert mfa.verify(secret, mfa.current_code(secret))
    assert not mfa.verify(secret, "000000")
    assert not mfa.verify(secret, "abc")
    assert not mfa.verify(secret, "")


def test_totp_accepts_one_step_of_drift():
    secret = mfa.generate_secret()
    now = time.time()
    assert mfa.verify(secret, mfa.current_code(secret, now - 30), at=now)
    assert mfa.verify(secret, mfa.current_code(secret, now + 30), at=now)
    # Two windows away must fail.
    assert not mfa.verify(secret, mfa.current_code(secret, now + 120), at=now)


def test_recovery_codes_are_hashed_and_single_use():
    plain, hashed = mfa.issue_recovery_codes(4)
    assert len(plain) == len(hashed) == 4
    assert all(p not in hashed for p in plain), "plaintext codes must never be stored"
    remaining, ok = mfa.consume_recovery_code(hashed, plain[0])
    assert ok and len(remaining) == 3
    _, reused = mfa.consume_recovery_code(remaining, plain[0])
    assert not reused, "a recovery code must not work twice"


def test_mfa_enrolment_flow_and_login_gate(client, api, auth):
    # A throwaway staff account keeps the shared fixtures password-only.
    created = client.post(f"{api}/admin/users", headers=auth, json={
        "email": "mfa.flow@scholara.com.bd", "full_name": "MFA Flow", "role": "COUNSELOR",
        "password": "Testing@12345", "is_active": True,
    }).json()
    token = client.post(f"{api}/admin/auth/login", json={
        "email": "mfa.flow@scholara.com.bd", "password": "Testing@12345",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    secret = client.post(f"{api}/admin/auth/mfa/enroll", headers=headers).json()["secret"]
    assert client.post(f"{api}/admin/auth/mfa/confirm", headers=headers,
                       json={"code": "000000"}).status_code == 400
    confirmed = client.post(f"{api}/admin/auth/mfa/confirm", headers=headers,
                            json={"code": mfa.current_code(secret)})
    assert confirmed.status_code == 200
    recovery = confirmed.json()["recovery_codes"]
    assert len(recovery) == 8

    # Password alone must no longer yield a token.
    step1 = client.post(f"{api}/admin/auth/login", json={
        "email": "mfa.flow@scholara.com.bd", "password": "Testing@12345",
    }).json()
    assert step1.get("mfa_required") is True
    assert "access_token" not in step1

    # The challenge accepts a TOTP…
    ok = client.post(f"{api}/admin/auth/mfa/challenge", json={
        "email": "mfa.flow@scholara.com.bd", "password": "Testing@12345",
        "code": mfa.current_code(secret),
    })
    assert ok.status_code == 200 and ok.json()["access_token"]

    # …and a recovery code, once.
    first = client.post(f"{api}/admin/auth/mfa/challenge", json={
        "email": "mfa.flow@scholara.com.bd", "password": "Testing@12345", "code": recovery[0],
    })
    assert first.status_code == 200
    second = client.post(f"{api}/admin/auth/mfa/challenge", json={
        "email": "mfa.flow@scholara.com.bd", "password": "Testing@12345", "code": recovery[0],
    })
    assert second.status_code == 401

    client.delete(f"{api}/admin/users/{created['id']}", headers=auth)


# --------------------------------------------------------- signed documents
def test_signature_is_key_bound_and_expiring():
    token = storage.sign_key("students/1/file.pdf", 60)
    assert storage.verify_signature(token) == "students/1/file.pdf"

    with pytest.raises(storage.StorageError):
        storage.verify_signature(token[:-3] + "xyz")          # tampered MAC
    with pytest.raises(storage.StorageError):
        storage.verify_signature("not-a-token")

    expired = storage.sign_key("students/1/file.pdf", -5)
    with pytest.raises(storage.StorageError):
        storage.verify_signature(expired)


def test_upload_rejects_oversize_and_mislabelled_files():
    with pytest.raises(storage.StorageError):
        storage.save_upload(owner_scope="t", filename="big.pdf",
                            content=b"%PDF-1.4" + b"0" * (16 * 1024 * 1024),
                            declared_mime="application/pdf")
    with pytest.raises(storage.StorageError):
        storage.save_upload(owner_scope="t", filename="fake.pdf", content=b"not a pdf at all",
                            declared_mime="application/pdf")
    with pytest.raises(storage.StorageError):
        storage.save_upload(owner_scope="t", filename="evil.exe", content=b"MZ\x90\x00",
                            declared_mime="application/x-msdownload")


def test_document_is_only_reachable_through_a_signature(client, api, student_token):
    res = client.post(f"{api}/portal/{student_token}/documents",
                      data={"doc_type": "PASSPORT"},
                      files={"file": ("p.pdf", b"%PDF-1.4\ntest\n%%EOF", "application/pdf")})
    assert res.status_code == 200
    url = res.json()["document"]["download_url"]

    assert client.get(url).status_code == 200
    assert client.get(url[:-4] + "aaaa").status_code == 403
    # The raw storage key is not a route.
    assert client.get(f"{api}/documents/file/students-1-p.pdf").status_code == 403


def test_a_student_token_cannot_reach_another_students_data(client, api, student_token):
    other = client.post(f"{api}/profiles", json={"full_name": "Someone Else"}).json()["token"]
    doc = client.post(f"{api}/portal/{student_token}/documents",
                      data={"doc_type": "CV"},
                      files={"file": ("cv.pdf", b"%PDF-1.4\ncv\n%%EOF", "application/pdf")}).json()
    doc_id = doc["document"]["id"]
    assert client.delete(f"{api}/portal/{other}/documents/{doc_id}").status_code == 404


# ------------------------------------------------------------- webhooks
def test_meta_signature_validation():
    secret = "topsecret"
    body = b'{"entry":[]}'
    import hashlib
    import hmac as _hmac

    good = "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert webhooks.verify_meta_signature(body, good, secret)
    assert not webhooks.verify_meta_signature(body, "sha256=deadbeef", secret)
    assert not webhooks.verify_meta_signature(body, None, secret)
    assert not webhooks.verify_meta_signature(b'{"entry":[1]}', good, secret)


def test_whatsapp_webhook_rejects_a_forged_signature(client, api):
    res = client.post(f"{api}/whatsapp/webhook", json={"entry": []},
                      headers={"X-Hub-Signature-256": "sha256=deadbeef"})
    assert res.status_code == 401


def test_outbound_signature_has_timestamp_and_mac():
    headers = webhooks.sign_outbound(b"{}", "secret")
    assert headers["X-Scholara-Timestamp"].isdigit()
    assert len(headers["X-Scholara-Signature"]) == 64


def test_generic_signature_rejects_replays():
    import hashlib
    import hmac as _hmac

    secret, body = "s3cret", b'{"a":1}'
    old = str(int(time.time()) - 10_000)
    sig = _hmac.new(secret.encode(), f"{old}.".encode() + body, hashlib.sha256).hexdigest()
    assert not webhooks.verify_generic_signature(body, sig, old, secret)


# ---------------------------------------------------------------- headers
def test_security_headers_are_present(client, api):
    res = client.get(f"{api}/countries")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "strict-origin" in res.headers["Referrer-Policy"]


def test_portal_pages_are_not_cacheable(client, api, student_token):
    res = client.post(f"{api}/portal/{student_token}/documents",
                      data={"doc_type": "SOP"},
                      files={"file": ("sop.pdf", b"%PDF-1.4\nsop\n%%EOF", "application/pdf")})
    url = res.json()["document"]["download_url"]
    download = client.get(url)
    assert "no-store" in download.headers.get("Cache-Control", "")
    assert "noindex" in download.headers.get("X-Robots-Tag", "")


# --------------------------------------------------------------------------
# Staff offboarding.
#
# Deleting a user used to work only because SQLite does not enforce foreign keys by default -
# on PostgreSQL the same request returned 500 (audit_logs_actor_id_fkey). The fix detaches
# rather than cascades, so the audit trail and the business records survive the person.
# --------------------------------------------------------------------------

def test_deleting_staff_user_preserves_audit_trail(client, api, auth):
    created = client.post(
        f"{api}/admin/users",
        headers=auth,
        json={
            "email": "offboard@scholara.com.bd",
            "full_name": "Offboard Test",
            "role": "COUNSELOR",
            "password": "Offboard@12345",
        },
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    # Give the account an audit footprint by signing in as them.
    login = client.post(
        f"{api}/admin/auth/login",
        json={"email": "offboard@scholara.com.bd", "password": "Offboard@12345"},
    )
    assert login.status_code == 200, login.text

    before = client.get(f"{api}/admin/audit-logs?page_size=200", headers=auth)
    assert before.status_code == 200
    trail_before = [
        r for r in before.json()["items"] if r.get("actor_email") == "offboard@scholara.com.bd"
    ]

    deleted = client.delete(f"{api}/admin/users/{user_id}", headers=auth)
    assert deleted.status_code == 200, deleted.text

    # The user is gone...
    assert client.get(f"{api}/admin/users/{user_id}", headers=auth).status_code == 404

    # ...but every audit row they generated is still there, still attributable by email.
    after = client.get(f"{api}/admin/audit-logs?page_size=200", headers=auth)
    trail_after = [
        r for r in after.json()["items"] if r.get("actor_email") == "offboard@scholara.com.bd"
    ]
    assert len(trail_after) >= len(trail_before)
    for row in trail_after:
        assert row.get("actor_id") is None  # detached, not deleted


def test_admin_cannot_delete_their_own_account(client, api, auth):
    me = client.get(f"{api}/admin/auth/me", headers=auth)
    assert me.status_code == 200, me.text
    own_id = me.json()["id"]
    blocked = client.delete(f"{api}/admin/users/{own_id}", headers=auth)
    assert blocked.status_code == 400
    assert client.get(f"{api}/admin/users/{own_id}", headers=auth).status_code == 200


# ------------------------------------------------------- mandatory MFA is actually mandatory
# Regression cover for a policy that used to be advisory: MFA_REQUIRED_ROLES was consulted
# when *disabling* MFA and when setting a "mfa_recommended" flag, but an ADMIN who had never
# enrolled still received a full, unrestricted session from POST /admin/auth/login.

def _unenrolled(db, email: str):
    """Strip enrolment from a seeded account and restore it afterwards."""
    from app.models.crm import User
    user = db.query(User).filter(User.email == email).first()
    saved = (user.mfa_secret, user.mfa_confirmed_at, user.mfa_recovery_codes)
    user.mfa_secret, user.mfa_confirmed_at, user.mfa_recovery_codes = None, None, []
    db.commit()
    return user, saved


def _restore(db, user, saved):
    user.mfa_secret, user.mfa_confirmed_at, user.mfa_recovery_codes = saved
    db.commit()


def test_mandatory_mfa_role_without_enrolment_gets_no_session(client, api, db):
    user, saved = _unenrolled(db, "senior@scholara.com.bd")
    try:
        res = client.post(f"{api}/admin/auth/login",
                          json={"email": "senior@scholara.com.bd", "password": "Senior@12345"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mfa_enrolment_required"] is True
        assert body["scope"] == "mfa_enrolment"

        token = body["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        # The scoped token must not reach anything except enrolment.
        for path in ("/admin/dashboard", "/admin/leads", "/admin/auth/me"):
            assert client.get(f"{api}{path}", headers=headers).status_code == 403, path
        # ...but enrolment itself has to stay reachable or the account is bricked.
        assert client.get(f"{api}/admin/auth/mfa/status", headers=headers).status_code == 200
        assert client.post(f"{api}/admin/auth/mfa/enroll", headers=headers).status_code == 200
    finally:
        _restore(db, user, saved)


def test_completing_enrolment_upgrades_the_scoped_token_to_a_session(client, api, db):
    from app.core import mfa
    user, saved = _unenrolled(db, "senior@scholara.com.bd")
    try:
        token = client.post(f"{api}/admin/auth/login",
                            json={"email": "senior@scholara.com.bd",
                                  "password": "Senior@12345"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        secret = client.post(f"{api}/admin/auth/mfa/enroll", headers=headers).json()["secret"]
        res = client.post(f"{api}/admin/auth/mfa/confirm", headers=headers,
                          json={"code": mfa.current_code(secret)})
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["recovery_codes"]) == 8
        # Both factors were demonstrated, so a real session comes back.
        session = {"Authorization": f"Bearer {body['access_token']}"}
        assert client.get(f"{api}/admin/dashboard", headers=session).status_code == 200
    finally:
        _restore(db, user, saved)


def test_enrolled_admin_still_requires_the_second_factor(client, api):
    res = client.post(f"{api}/admin/auth/login",
                      json={"email": "admin@scholara.com.bd", "password": "Admin@12345"})
    assert res.status_code == 200
    body = res.json()
    assert body["mfa_required"] is True
    assert "access_token" not in body


def test_non_mandatory_role_is_unaffected(client, api):
    res = client.post(f"{api}/admin/auth/login",
                      json={"email": "counselor@scholara.com.bd", "password": "Counselor@123"})
    assert res.status_code == 200
    body = res.json()
    assert body["mfa_required"] is False and body["mfa_enrolment_required"] is False
    assert client.get(f"{api}/admin/auth/me",
                      headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200


def test_enrolment_state_has_no_redundant_flag():
    """`mfa_enabled` was a write-only column that disagreed with reality; it must stay gone."""
    from app.models.crm import User
    assert not hasattr(User, "mfa_enabled")


def test_autogenerate_cannot_propose_dropping_the_pgvector_index():
    """
    alembic autogenerate cannot see `knowledge_chunks.embedding_vec` (raw-SQL pgvector column,
    no portable SQLAlchemy type) and therefore proposed dropping it, and its HNSW index, on
    every run. One careless `alembic upgrade head` would have destroyed the retrieval index.
    env.py now filters both out; this asserts the filter still covers them.
    """
    import importlib.util
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    code = src.read_text()
    # env.py runs migrations on import, so lift out just the predicate.
    ns: dict = {}
    start = code.index("INVISIBLE_TO_AUTOGENERATE")
    end = code.index("def run_migrations_offline")
    exec(code[start:end], ns)

    class _Col:
        table = type("T", (), {"name": "knowledge_chunks"})()

    assert ns["include_object"](_Col(), "embedding_vec", "column", True, None) is False
    assert ns["include_object"](_Col(), "ix_knowledge_chunks_embedding_vec", "index", True, None) is False
    # Everything else must still be compared.
    assert ns["include_object"](_Col(), "title", "column", True, None) is True


# ---------------------------------------------------- runtime configuration overlay
# Credentials and tuning knobs became admin-editable. The risks that introduces are: a
# non-allowlisted key being writable, a secret leaking back to the browser, a change that
# is not audited, and the overlay failing open when the database is unavailable.

def test_only_allowlisted_keys_can_be_written(client, api, auth):
    res = client.put(f"{api}/admin/config",
                     json={"values": {"database_url": "postgresql://attacker/evil"}},
                     headers=auth)
    assert res.status_code == 400
    assert "not a runtime-editable setting" in res.json()["detail"]


def test_secret_values_are_never_returned_in_readable_form(client, api, auth):
    secret = "EAAG-super-secret-token-9876543210"
    assert client.put(f"{api}/admin/config",
                      json={"values": {"whatsapp_access_token": secret}},
                      headers=auth).status_code == 200
    body = client.get(f"{api}/admin/config", headers=auth).text
    assert secret not in body, "the raw secret must never reach the client"

    item = next(i for g in client.get(f"{api}/admin/config", headers=auth).json()["groups"]
                for i in g["items"] if i["key"] == "whatsapp_access_token")
    assert item["has_value"] is True
    assert item["value"].startswith("EAAG") and "•" in item["value"]
    # clean up so later tests see a pristine overlay
    client.put(f"{api}/admin/config", json={"values": {"whatsapp_access_token": ""}}, headers=auth)


def test_secrets_are_encrypted_at_rest(client, api, auth, db):
    from app.models.content import RuntimeSetting
    secret = "mistral-key-plaintext-check-123"
    client.put(f"{api}/admin/config", json={"values": {"mistral_api_key": secret}}, headers=auth)
    row = db.query(RuntimeSetting).filter(RuntimeSetting.key == "mistral_api_key").first()
    assert row is not None and row.is_secret
    assert secret not in row.value, "a database dump must not expose the credential"

    from app.core import runtime
    assert runtime.decrypt(row.value) == secret
    client.put(f"{api}/admin/config", json={"values": {"mistral_api_key": ""}}, headers=auth)


def test_config_changes_are_audited_without_recording_the_value(client, api, auth, db):
    from app.models.crm import AuditLog
    client.put(f"{api}/admin/config", json={"values": {"meta_pixel_id": "9988776655"}}, headers=auth)
    entry = (db.query(AuditLog).filter(AuditLog.action == "UPDATE_RUNTIME_CONFIG")
             .order_by(AuditLog.id.desc()).first())
    assert entry is not None
    assert "meta_pixel_id" in (entry.detail or {}).get("changed", [])
    assert "9988776655" not in str(entry.detail), "the audit log must record keys, not values"
    client.put(f"{api}/admin/config", json={"values": {"meta_pixel_id": ""}}, headers=auth)


def test_an_override_changes_behaviour_without_a_restart(client, api, auth):
    from app.core.config import settings
    original = float(settings.retrieval_dense_weight)
    try:
        client.put(f"{api}/admin/config",
                   json={"values": {"retrieval_dense_weight": "0.9"}}, headers=auth)
        from app.core import runtime
        runtime.invalidate()
        assert float(settings.retrieval_dense_weight) == 0.9
    finally:
        client.put(f"{api}/admin/config",
                   json={"values": {"retrieval_dense_weight": ""}}, headers=auth)
        from app.core import runtime
        runtime.invalidate()
        assert float(settings.retrieval_dense_weight) == original


def test_clearing_an_override_falls_back_to_the_environment(client, api, auth):
    from app.core.config import get_settings, settings
    from app.core import runtime
    env_value = get_settings().retrieval_min_score
    client.put(f"{api}/admin/config", json={"values": {"retrieval_min_score": "0.42"}}, headers=auth)
    runtime.invalidate()
    assert float(settings.retrieval_min_score) == 0.42
    client.put(f"{api}/admin/config", json={"values": {"retrieval_min_score": ""}}, headers=auth)
    runtime.invalidate()
    assert float(settings.retrieval_min_score) == env_value


def test_out_of_range_values_are_rejected(client, api, auth):
    res = client.put(f"{api}/admin/config",
                     json={"values": {"retrieval_dense_weight": "5"}}, headers=auth)
    assert res.status_code == 400
    assert "at most" in res.json()["detail"]


def test_config_requires_admin_not_merely_staff(client, api, student_token):
    res = client.get(f"{api}/admin/config", headers={"Authorization": f"Bearer {student_token}"})
    assert res.status_code in (401, 403)
