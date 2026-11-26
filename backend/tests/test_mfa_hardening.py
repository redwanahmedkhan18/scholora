"""Two-factor authentication: enrolment, challenge, replay, recovery and reset.

Each test here corresponds to a way the shipped flow was wrong:

* the second step re-posted the password instead of using a scoped challenge token;
* a valid six-digit code could be replayed inside its drift window;
* an administrator who lost the authenticator had no in-app way back in, so the login page
  told them to run a local shell script;
* recovery codes could not be regenerated, and an administrator could not reset a colleague's
  enrolment;
* the seeded accounts were enrolled with TOTP secrets published in this repository.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.core import mfa
from app.core.config import get_settings, settings
from app.core.security import create_access_token
from app.models.crm import AuditLog, User
from sqlalchemy import select

PASSWORD = "Testing@12345"


def _make_staff(client, api, auth, email: str, role: str = "COUNSELOR") -> dict:
    created = client.post(f"{api}/admin/users", headers=auth, json={
        "email": email, "full_name": "MFA Test", "role": role,
        "password": PASSWORD, "is_active": True,
    })
    assert created.status_code == 200, created.text
    return created.json()


def _login(client, api, email: str) -> dict:
    res = client.post(f"{api}/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _enrol(client, api, email: str) -> tuple[str, list[str], str]:
    """Take an account from password-only to enrolled; returns (secret, codes, session)."""
    token = _login(client, api, email)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    enrolled = client.post(f"{api}/admin/auth/mfa/enroll", headers=headers).json()
    secret = enrolled["secret"]
    confirmed = client.post(f"{api}/admin/auth/mfa/confirm", headers=headers,
                            json={"code": mfa.current_code(secret)})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    return secret, body["recovery_codes"], body["access_token"]


def _next_code(secret: str) -> str:
    """A code for the next 30-second step - the current one has just been spent."""
    return mfa.current_code(secret, time.time() + 30)


# ------------------------------------------------------------------ enrolment
def test_enrolment_returns_a_qr_a_manual_key_and_an_otpauth_uri(client, api, auth):
    _make_staff(client, api, auth, "mfa.enrol@scholara.com.bd")
    token = _login(client, api, "mfa.enrol@scholara.com.bd")["access_token"]
    res = client.post(f"{api}/admin/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["otpauth_url"].startswith("otpauth://totp/")
    assert "secret=" in body["otpauth_url"]
    assert body["manual_entry"].replace(" ", "") == body["secret"]
    assert body["digits"] == 6 and body["period"] == 30
    # A QR is offered as a data URI image, never as markup the page would have to inject.
    assert body["qr_data_uri"].startswith("data:image/svg+xml;base64,")


def test_an_unconfirmed_secret_is_never_an_active_factor(client, api, auth, db):
    _make_staff(client, api, auth, "mfa.pending@scholara.com.bd")
    token = _login(client, api, "mfa.pending@scholara.com.bd")["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post(f"{api}/admin/auth/mfa/enroll", headers=headers)

    # Password alone still signs this account in: enrolment started but was never proved.
    step1 = _login(client, api, "mfa.pending@scholara.com.bd")
    assert step1.get("mfa_required") is False
    assert step1.get("access_token")


# ------------------------------------------------------------------ challenge
def test_password_alone_returns_a_scoped_challenge_not_a_session(client, api, auth):
    _make_staff(client, api, auth, "mfa.gate@scholara.com.bd")
    _enrol(client, api, "mfa.gate@scholara.com.bd")

    step1 = _login(client, api, "mfa.gate@scholara.com.bd")
    assert step1["mfa_required"] is True
    assert "access_token" not in step1
    challenge = step1["challenge_token"]
    assert step1["challenge_expires_in"] <= 600

    # The challenge token is not a session: every authenticated route refuses it.
    headers = {"Authorization": f"Bearer {challenge}"}
    for path in ["/admin/auth/me", "/admin/dashboard", "/admin/settings"]:
        assert client.get(f"{api}{path}", headers=headers).status_code == 403, path
    # …and it cannot be used to change the second factor either.
    assert client.post(f"{api}/admin/auth/mfa/enroll", headers=headers).status_code == 403


def test_challenge_with_a_valid_code_issues_a_session(client, api, auth):
    _make_staff(client, api, auth, "mfa.ok@scholara.com.bd")
    secret, _, _ = _enrol(client, api, "mfa.ok@scholara.com.bd")
    challenge = _login(client, api, "mfa.ok@scholara.com.bd")["challenge_token"]

    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={"challenge_token": challenge, "code": _next_code(secret)})
    assert res.status_code == 200, res.text
    assert res.json()["mfa_method"] == "totp"
    session = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get(f"{api}/admin/dashboard", headers=session).status_code == 200


def test_an_expired_challenge_is_refused(client, api, auth):
    _make_staff(client, api, auth, "mfa.expired@scholara.com.bd")
    secret, _, _ = _enrol(client, api, "mfa.expired@scholara.com.bd")

    # Mint a challenge that expired a minute ago, exactly as the login endpoint would.
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        user = session.execute(
            select(User).where(User.email == "mfa.expired@scholara.com.bd")
        ).scalars().first()
        assert user is not None
        stale = create_access_token(str(user.id), user.role, extra={
            "scope": "mfa_challenge",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=5),
        })
    finally:
        session.close()

    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={"challenge_token": stale, "code": _next_code(secret)})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_a_challenge_token_from_the_wrong_scope_is_refused(client, api, auth):
    _make_staff(client, api, auth, "mfa.scope@scholara.com.bd")
    secret, _, session_token = _enrol(client, api, "mfa.scope@scholara.com.bd")
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        user = session.execute(
            select(User).where(User.email == "mfa.scope@scholara.com.bd")
        ).scalars().first()
        enrolment_token = create_access_token(str(user.id), user.role,
                                               extra={"scope": "mfa_enrolment"})
    finally:
        session.close()

    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={"challenge_token": enrolment_token, "code": _next_code(secret)})
    assert res.status_code == 401
    assert session_token


# --------------------------------------------------------------------- replay
def test_a_totp_code_cannot_be_replayed(client, api, auth):
    _make_staff(client, api, auth, "mfa.replay@scholara.com.bd")
    secret, _, _ = _enrol(client, api, "mfa.replay@scholara.com.bd")

    challenge = _login(client, api, "mfa.replay@scholara.com.bd")["challenge_token"]
    code = _next_code(secret)
    first = client.post(f"{api}/admin/auth/mfa/challenge",
                        json={"challenge_token": challenge, "code": code})
    assert first.status_code == 200, first.text

    # Same code, fresh challenge - the step is already spent.
    challenge2 = _login(client, api, "mfa.replay@scholara.com.bd")["challenge_token"]
    second = client.post(f"{api}/admin/auth/mfa/challenge",
                         json={"challenge_token": challenge2, "code": code})
    assert second.status_code == 401
    assert "already been used" in second.json()["detail"]


def test_the_spent_step_is_recorded_on_the_account(client, api, auth, db):
    _make_staff(client, api, auth, "mfa.step@scholara.com.bd")
    secret, _, _ = _enrol(client, api, "mfa.step@scholara.com.bd")
    challenge = _login(client, api, "mfa.step@scholara.com.bd")["challenge_token"]
    client.post(f"{api}/admin/auth/mfa/challenge",
                json={"challenge_token": challenge, "code": _next_code(secret)})

    db.expire_all()
    user = db.execute(select(User).where(User.email == "mfa.step@scholara.com.bd")).scalars().first()
    assert user.mfa_last_used_step and user.mfa_last_used_step > 0


# ------------------------------------------------------------------- recovery
def test_recovery_code_works_once_and_is_then_dead(client, api, auth):
    _make_staff(client, api, auth, "mfa.recovery@scholara.com.bd")
    _, codes, _ = _enrol(client, api, "mfa.recovery@scholara.com.bd")

    challenge = _login(client, api, "mfa.recovery@scholara.com.bd")["challenge_token"]
    ok = client.post(f"{api}/admin/auth/mfa/challenge",
                     json={"challenge_token": challenge, "code": codes[0]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["mfa_method"] == "recovery"

    again = _login(client, api, "mfa.recovery@scholara.com.bd")["challenge_token"]
    reused = client.post(f"{api}/admin/auth/mfa/challenge",
                         json={"challenge_token": again, "code": codes[0]})
    assert reused.status_code == 401


def test_lost_authenticator_recovery_reaches_enrolment_without_a_session(client, api, auth):
    _make_staff(client, api, auth, "mfa.lost@scholara.com.bd")
    secret, codes, _ = _enrol(client, api, "mfa.lost@scholara.com.bd")

    res = client.post(f"{api}/admin/auth/mfa/recover", json={
        "email": "mfa.lost@scholara.com.bd", "password": PASSWORD, "recovery_code": codes[0],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "mfa_enrolment"
    assert body["recovery_codes_remaining"] == len(codes) - 1
    token = {"Authorization": f"Bearer {body['access_token']}"}

    # It is an enrolment token, not a session.
    assert client.get(f"{api}/admin/dashboard", headers=token).status_code == 403

    # Re-enrol a new authenticator and finish with a real session.
    enrolled = client.post(f"{api}/admin/auth/mfa/enroll", headers=token).json()
    new_secret = enrolled["secret"]
    confirmed = client.post(f"{api}/admin/auth/mfa/confirm", headers=token,
                            json={"code": mfa.current_code(new_secret)})
    assert confirmed.status_code == 200
    assert new_secret != secret

    # The old authenticator no longer works: its next-step code is refused.
    challenge = _login(client, api, "mfa.lost@scholara.com.bd")["challenge_token"]
    assert client.post(f"{api}/admin/auth/mfa/challenge",
                       json={"challenge_token": challenge,
                             "code": mfa.current_code(secret, time.time() + 90)}).status_code == 401


def test_recovery_requires_the_password_and_an_unused_code(client, api, auth):
    _make_staff(client, api, auth, "mfa.recover2@scholara.com.bd")
    _, codes, _ = _enrol(client, api, "mfa.recover2@scholara.com.bd")

    wrong_password = client.post(f"{api}/admin/auth/mfa/recover", json={
        "email": "mfa.recover2@scholara.com.bd", "password": "not-it", "recovery_code": codes[0],
    })
    assert wrong_password.status_code == 401

    bad_code = client.post(f"{api}/admin/auth/mfa/recover", json={
        "email": "mfa.recover2@scholara.com.bd", "password": PASSWORD, "recovery_code": "aaaa-bbbb-cccc",
    })
    assert bad_code.status_code == 401
    # A failed attempt must not consume the code.
    assert client.post(f"{api}/admin/auth/mfa/recover", json={
        "email": "mfa.recover2@scholara.com.bd", "password": PASSWORD, "recovery_code": codes[0],
    }).status_code == 200


def test_regenerating_recovery_codes_invalidates_the_old_set(client, api, auth):
    _make_staff(client, api, auth, "mfa.regen@scholara.com.bd")
    secret, old_codes, session = _enrol(client, api, "mfa.regen@scholara.com.bd")

    res = client.post(f"{api}/admin/auth/mfa/recovery-codes/regenerate",
                      headers={"Authorization": f"Bearer {session}"},
                      json={"code": _next_code(secret)})
    assert res.status_code == 200, res.text
    new_codes = res.json()["recovery_codes"]
    assert len(new_codes) == 8
    assert not set(new_codes) & set(old_codes)

    # An old code no longer authenticates…
    challenge = _login(client, api, "mfa.regen@scholara.com.bd")["challenge_token"]
    assert client.post(f"{api}/admin/auth/mfa/challenge",
                       json={"challenge_token": challenge, "code": old_codes[0]}).status_code == 401
    # …and a new one does.
    challenge = _login(client, api, "mfa.regen@scholara.com.bd")["challenge_token"]
    assert client.post(f"{api}/admin/auth/mfa/challenge",
                       json={"challenge_token": challenge, "code": new_codes[0]}).status_code == 200


def test_regeneration_requires_a_live_code(client, api, auth):
    _make_staff(client, api, auth, "mfa.regen2@scholara.com.bd")
    _, _, session = _enrol(client, api, "mfa.regen2@scholara.com.bd")
    res = client.post(f"{api}/admin/auth/mfa/recovery-codes/regenerate",
                      headers={"Authorization": f"Bearer {session}"}, json={"code": "000000"})
    assert res.status_code == 401


# ---------------------------------------------------------------------- reset
def test_only_an_admin_with_a_live_code_can_reset_someone_elses_mfa(client, api, auth):
    target = _make_staff(client, api, auth, "mfa.target@scholara.com.bd", role="SENIOR_COUNSELOR")
    _enrol(client, api, "mfa.target@scholara.com.bd")

    counselor_token = client.post(f"{api}/admin/auth/login", json={
        "email": "counselor@scholara.com.bd", "password": "Counselor@123",
    }).json()["access_token"]
    forbidden = client.post(f"{api}/admin/auth/mfa/reset",
                            headers={"Authorization": f"Bearer {counselor_token}"},
                            json={"user_id": target["id"], "code": "123456"})
    assert forbidden.status_code == 403

    admin_challenge = client.get(f"{api}/admin/auth/mfa/status", headers=auth)
    assert admin_challenge.status_code == 200
    from app.seed_data import DEV_MFA_SECRET_ADMIN

    reset = client.post(f"{api}/admin/auth/mfa/reset", headers=auth,
                        json={"user_id": target["id"],
                              "code": mfa.current_code(DEV_MFA_SECRET_ADMIN, time.time() + 30)})
    assert reset.status_code == 200, reset.text
    assert "reset" in reset.json()["message"].lower()

    # The target is now back at the mandatory enrolment gate, not signed in.
    step1 = _login(client, api, "mfa.target@scholara.com.bd")
    assert step1["mfa_required"] is False
    assert step1.get("mfa_enrolment_required") is True
    assert step1.get("scope") == "mfa_enrolment"


def test_an_admin_cannot_reset_their_own_mfa_through_the_reset_endpoint(client, api, auth, db):
    me = client.get(f"{api}/admin/auth/me", headers=auth).json()
    res = client.post(f"{api}/admin/auth/mfa/reset", headers=auth,
                      json={"user_id": me["id"], "code": "123456"})
    assert res.status_code == 400


def test_a_wrong_code_cannot_reset_anyone(client, api, auth):
    target = _make_staff(client, api, auth, "mfa.target2@scholara.com.bd")
    _enrol(client, api, "mfa.target2@scholara.com.bd")
    res = client.post(f"{api}/admin/auth/mfa/reset", headers=auth,
                      json={"user_id": target["id"], "code": "000000"})
    assert res.status_code == 401
    assert _login(client, api, "mfa.target2@scholara.com.bd")["mfa_required"] is True


def test_mfa_events_are_audited_without_secrets(client, api, auth, db):
    _make_staff(client, api, auth, "mfa.audit@scholara.com.bd")
    secret, _, session = _enrol(client, api, "mfa.audit@scholara.com.bd")

    db.expire_all()
    rows = db.execute(select(AuditLog).where(AuditLog.action.like("MFA%"))).scalars().all()
    actions = {r.action for r in rows}
    assert {"MFA_ENROLL_START", "MFA_ENABLED"} <= actions
    for row in rows:
        assert secret not in str(row.detail)
    assert session


def test_mfa_status_never_returns_the_secret(client, api, auth):
    _make_staff(client, api, auth, "mfa.status@scholara.com.bd")
    secret, _, session = _enrol(client, api, "mfa.status@scholara.com.bd")
    body = client.get(f"{api}/admin/auth/mfa/status",
                      headers={"Authorization": f"Bearer {session}"}).json()
    assert "secret" not in body
    assert secret not in str(body)
    assert body["enrolled"] is True
    assert body["recovery_codes_remaining"] == 8


# --------------------------------------------------------- mandatory policy
def test_mandatory_role_cannot_disable_mfa(client, api, auth):
    from app.seed_data import DEV_MFA_SECRET_ADMIN

    res = client.post(f"{api}/admin/auth/mfa/disable", headers=auth,
                      json={"code": mfa.current_code(DEV_MFA_SECRET_ADMIN, time.time() + 30)})
    assert res.status_code == 403


def test_a_counselor_with_mfa_can_disable_it_with_a_live_code(client, api, auth):
    _make_staff(client, api, auth, "mfa.optional@scholara.com.bd")
    secret, _, session = _enrol(client, api, "mfa.optional@scholara.com.bd")
    res = client.post(f"{api}/admin/auth/mfa/disable",
                      headers={"Authorization": f"Bearer {session}"},
                      json={"code": _next_code(secret)})
    assert res.status_code == 200, res.text
    assert _login(client, api, "mfa.optional@scholara.com.bd")["mfa_required"] is False


def test_the_published_default_password_is_refused_in_production(client, api, monkeypatch):
    """The seed password ships in this repository; a production login must not accept it."""
    previous = get_settings().environment
    get_settings().environment = "production"
    try:
        res = client.post(f"{api}/admin/auth/login",
                          json={"email": "admin@scholara.com.bd", "password": "Admin@12345"})
        assert res.status_code == 403
        assert "published default password" in res.json()["detail"]
        assert "access_token" not in res.json()
    finally:
        get_settings().environment = previous


def test_every_password_the_seed_ships_is_refused_in_production(client, api):
    """The seed writes four published passwords. Every one must be refused in production.

    `published_passwords` is the only backstop: the generic admin user API will happily store
    any short string as a password hash, so a password missing from this list is a working
    production credential for whichever account still holds it.
    """
    published = set(get_settings().published_password_list)
    demo_credentials = {
        "admin@scholara.com.bd": "Admin@12345",      # seed.py: settings.admin_password
        "senior@scholara.com.bd": "Senior@12345",
        "counselor@scholara.com.bd": "Counselor@123",
        "data@scholara.com.bd": "Data@12345",
    }
    missing = set(demo_credentials.values()) - published
    assert not missing, f"seed passwords absent from published_passwords: {sorted(missing)}"

    previous = get_settings().environment
    get_settings().environment = "production"
    try:
        for email, password in demo_credentials.items():
            res = client.post(f"{api}/admin/auth/login",
                              json={"email": email, "password": password})
            assert res.status_code == 403, f"{email} signed in with a published password"
            assert "published default password" in res.json()["detail"]
            assert "access_token" not in res.json()
    finally:
        get_settings().environment = previous


def test_demo_staff_seeding_is_impossible_in_production(monkeypatch):
    from app import seed

    previous = get_settings().environment
    try:
        get_settings().environment = "production"
        assert seed.demo_users_allowed() is False
        get_settings().environment = "development"
        settings.dev_seed_enabled = False
        assert seed.demo_users_allowed() is False
        settings.dev_seed_enabled = True
        assert seed.demo_users_allowed() is True
    finally:
        get_settings().environment = previous
        settings.dev_seed_enabled = True


def test_recovery_hashing_is_keyed_and_accepts_legacy_digests():
    import hashlib

    plain, hashed = mfa.issue_recovery_codes(2)
    assert hashed[0].startswith("hmac-sha256:")
    assert mfa.check_recovery_code(plain[0], hashed) == hashed[0]

    legacy = hashlib.sha256(plain[1].strip().lower().encode()).hexdigest()
    assert mfa.check_recovery_code(plain[1], [legacy]) == legacy


def test_a_verification_rule_shields_the_recovery_endpoint():
    from app.core import ratelimit

    rule = ratelimit.match_rule("/api/v1/admin/auth/mfa/recover", "POST")
    assert rule is not None and rule.name == "mfa_recover"
    assert rule.limit <= 10
    assert ratelimit.match_rule("/api/v1/admin/auth/mfa/challenge", "POST").name == "mfa"


# ------------------------------------------------------- malformed stored secrets
# A secret column is plain text, so a hand-edited row, a truncated restore or a bad merge can
# leave something that is not base32. `base64.b32decode` raises on those, and that exception
# used to escape a login attempt as an HTTP 500 instead of a plain rejection.
MALFORMED_SECRETS = [
    "",                                  # empty
    "   ",                               # whitespace only
    "not-base32!",                       # invalid characters
    "AAAA1AAA",                          # '1' is outside the base32 alphabet
    "ABC",                               # wrong length for base32 (3 mod 8)
    "=======",                           # decodes to zero bytes, which is not a key
    "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3P",    # truncated to a length base32 cannot encode
]


def test_a_malformed_secret_is_never_a_valid_code():
    for secret in MALFORMED_SECRETS:
        assert mfa.decode_secret(secret) is None, secret
        assert mfa.match_step(secret, "123456") is None, secret
        assert mfa.verify(secret, "123456") is False, secret


def test_malformed_input_raises_something_that_cannot_leak_the_secret():
    for secret in MALFORMED_SECRETS:
        try:
            mfa.current_code(secret)
        except ValueError as exc:                      # a CLI caller gets a clear error...
            if secret.strip():                         # ...that never echoes the value
                assert secret.strip() not in str(exc)
        else:
            raise AssertionError(f"current_code accepted {secret!r}")


def test_valid_secrets_still_verify_and_replay_prevention_is_unchanged():
    secret = mfa.generate_secret()
    assert mfa.decode_secret(secret) is not None

    at = time.time()
    code = mfa.current_code(secret, at)
    step = mfa.match_step(secret, code, at)
    assert step == int(at // mfa.PERIOD)
    assert mfa.verify(secret, code, at) is True
    # the same step is refused once spent - the replay guard still bites
    assert mfa.verify(secret, code, at, minimum_step=step) is False
    assert mfa.verify(secret, code, at, minimum_step=step - 1) is True
    # lower case and surrounding spaces are tolerated, a wrong code is not
    assert mfa.verify(secret.lower(), code, at) is True
    assert mfa.verify(secret, f" {code} ", at) is True
    assert mfa.verify(secret, "000000", at) is False


def test_a_corrupt_secret_in_the_database_is_a_401_not_a_500(client, api, auth, db):
    _make_staff(client, api, auth, "mfa.corrupt@scholara.com.bd")
    secret, _, _ = _enrol(client, api, "mfa.corrupt@scholara.com.bd")

    db.expire_all()
    user = db.execute(
        select(User).where(User.email == "mfa.corrupt@scholara.com.bd")
    ).scalars().first()
    user.mfa_secret = "ZZZZ-not-valid-base32"          # simulate a hand-edited / truncated row
    db.commit()

    challenge = _login(client, api, "mfa.corrupt@scholara.com.bd")["challenge_token"]
    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={"challenge_token": challenge,
                            "code": mfa.current_code(secret)})
    assert res.status_code == 401, res.text
    assert "Internal Server Error" not in res.text
