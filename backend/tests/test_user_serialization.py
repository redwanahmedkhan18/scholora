"""Staff credential columns must never leave the admin API.

`users` is the one row type that carries secrets: `password_hash` (offline-crackable),
`mfa_secret` (the TOTP seed) and `mfa_recovery_codes` (the printed single-use codes). One of
them is enough to take an account over, and the generic CRUD serialiser returned every column
of every resource - so `GET /admin/users` handed the whole set to any staff caller.

These tests cover all three read paths (list, single, and the POST/PUT echoes) with both an
ADMIN and a non-ADMIN staff caller, and prove the columns are populated in the database so the
absence from the response means "removed", not "empty".
"""
from __future__ import annotations

import pytest
from app.models.crm import User
from sqlalchemy import select

SECRET_FIELDS = ("password_hash", "mfa_secret", "mfa_recovery_codes")

# Fields the back office legitimately needs to manage an account.
PUBLIC_FIELDS = ("id", "email", "full_name", "role", "phone", "is_active", "mfa_confirmed_at")


def _counselor_headers(client, api) -> dict:
    """A non-ADMIN staff session (COUNSELOR is not in MFA_REQUIRED_ROLES)."""
    res = client.post(f"{api}/admin/auth/login",
                      json={"email": "counselor@scholara.com.bd", "password": "Counselor@123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def admin_row(client, api, auth, db):
    """The seeded admin id, with a confirmed secret in the database."""
    res = client.get(f"{api}/admin/users", params={"q": "admin@scholara.com.bd"}, headers=auth)
    assert res.status_code == 200
    row = next(u for u in res.json()["items"] if u["email"] == "admin@scholara.com.bd")

    stored = db.execute(select(User).where(User.id == row["id"])).scalars().one()
    # The test is only meaningful if the columns really hold values.
    assert stored.password_hash.startswith(("$2", "$pbkdf2"))
    assert stored.mfa_secret
    assert stored.mfa_recovery_codes
    return row


def _assert_no_secrets(body: dict, where: str) -> None:
    for field in SECRET_FIELDS:
        assert field not in body, f"{where} leaked {field}"
    # Recovery codes are hashed today, but the plaintext would travel the same way - assert on
    # the values too, so a future "return the codes once" change cannot slip through unnoticed.
    flat = str(body)
    assert "$pbkdf2" not in flat and "$2b$" not in flat, f"{where} leaked a password hash"


@pytest.mark.parametrize("caller", ["admin", "counselor"])
def test_the_user_list_never_returns_credentials(client, api, auth, admin_row, caller):
    headers = auth if caller == "admin" else _counselor_headers(client, api)
    res = client.get(f"{api}/admin/users", headers=headers)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert items, "expected seeded users"
    for item in items:
        _assert_no_secrets(item, f"GET /admin/users as {caller}")
        # Nothing legitimate was dropped.
        assert set(PUBLIC_FIELDS) <= set(item), sorted(set(PUBLIC_FIELDS) - set(item))
        assert item["email"]


@pytest.mark.parametrize("caller", ["admin", "counselor"])
def test_a_single_user_read_never_returns_credentials(client, api, auth, admin_row, caller):
    headers = auth if caller == "admin" else _counselor_headers(client, api)
    res = client.get(f"{api}/admin/users/{admin_row['id']}", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_no_secrets(body, f"GET /admin/users/{{id}} as {caller}")
    assert body["email"] == "admin@scholara.com.bd"
    assert body["role"] == "ADMIN"
    assert body["mfa_confirmed_at"]          # enrolment state is still visible to the operator


def test_the_create_response_never_returns_credentials(client, api, auth):
    email = "serialize-create@scholara.com.bd"
    res = client.post(f"{api}/admin/users",
                      json={"email": email, "full_name": "Serialize Probe",
                            "role": "COUNSELOR", "password": "A-created-passphrase-42"},
                      headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_no_secrets(body, "POST /admin/users")
    assert body["email"] == email and body["id"]


def test_the_update_response_never_returns_credentials(client, api, auth, admin_row):
    """Updating an account echoes it back - that echo must not carry the stored secrets."""
    res = client.put(f"{api}/admin/users/{admin_row['id']}",
                     json={"full_name": "Platform Administrator"}, headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_no_secrets(body, "PUT /admin/users/{id}")
    assert body["id"] == admin_row["id"]


def test_a_password_change_still_works_but_the_hash_is_not_returned(client, api, auth):
    email = "serialize-rotate@scholara.com.bd"
    created = client.post(f"{api}/admin/users",
                          json={"email": email, "full_name": "Rotate Probe", "role": "COUNSELOR",
                                "password": "First-passphrase-11"}, headers=auth)
    assert created.status_code == 200
    user_id = created.json()["id"]

    res = client.put(f"{api}/admin/users/{user_id}",
                     json={"password": "Second-passphrase-22"}, headers=auth)
    assert res.status_code == 200, res.text
    _assert_no_secrets(res.json(), "PUT /admin/users/{id} password change")
    # The write really happened, and the account still works.
    login = client.post(f"{api}/admin/auth/login",
                        json={"email": email, "password": "Second-passphrase-22"})
    assert login.status_code == 200


def test_other_resources_keep_every_column(client, api, auth):
    """The filter is per-model: a non-user resource must serialise exactly as before."""
    from app.api.v1.routers.admin import RESOURCES

    model, _, _ = RESOURCES["countries"]
    res = client.get(f"{api}/admin/countries", params={"page_size": 1}, headers=auth)
    assert res.status_code == 200
    items = res.json()["items"]
    assert items, "expected seeded countries"
    expected = {c.name for c in model.__table__.columns}
    assert set(items[0]) == expected, sorted(expected - set(items[0])) or sorted(set(items[0]) - expected)
