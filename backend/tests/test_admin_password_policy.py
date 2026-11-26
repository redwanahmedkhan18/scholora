"""The staff-password policy is enforced where passwords are written, not only where they are used.

Three places can put a password on a staff account:

* the back office CRUD form (``POST/PUT /admin/users``) - the path this file covers,
* ``python -m app.mgmt_admin --create-admin/--set-password`` (covered in ``test_mfa_hardening``),
* the seed, which only writes the published demo values in development.

The login endpoint refuses published passwords in production, but a refusal at sign-in is a
detective control: it turns a usable account into a support ticket. The password must be
rejected when it is stored. Short passwords are refused for the same reason - a 10-character
published demo value used to pass here because nothing checked the length.
"""
from __future__ import annotations

import pytest
from app.core.passwords import MIN_PASSWORD_LENGTH, password_policy_error

PUBLISHED = ["Admin@12345", "Senior@12345", "Counselor@123", "Data@12345"]


def _new_user(email: str, password=None, **extra):
    body = {"email": email, "full_name": "Policy Probe", "role": "COUNSELOR", "is_active": True}
    if password is not None:
        body["password"] = password
    body.update(extra)
    return body


def _exists(client, api, auth, email: str) -> bool:
    res = client.get(f"{api}/admin/users", params={"q": email, "page_size": 50}, headers=auth)
    assert res.status_code == 200
    return any(u["email"] == email for u in res.json()["items"])


# ---------------------------------------------------------------- the policy itself
@pytest.mark.parametrize("password", PUBLISHED)
def test_published_demo_passwords_are_rejected_by_the_policy(password):
    assert password_policy_error(password) is not None


def test_short_passwords_are_rejected_by_the_policy():
    short = "x" * (MIN_PASSWORD_LENGTH - 1)
    assert password_policy_error(short) == f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    assert password_policy_error("x" * MIN_PASSWORD_LENGTH) is None


def test_the_policy_is_shared_with_the_operator_cli():
    """One rule, two callers: the CLI must not accept what the API refuses."""
    from app import mgmt_admin

    for password in PUBLISHED + ["short"]:
        with pytest.raises(SystemExit):
            mgmt_admin._validate_password(password)
    mgmt_admin._validate_password("A-perfectly-fine-passphrase-42")


# ---------------------------------------------------------------- create path
@pytest.mark.parametrize("password", PUBLISHED)
def test_creating_a_user_with_a_published_password_is_refused(client, api, auth, password):
    email = f"policy-pub-{password.split('@')[0].lower()}@scholara.com.bd"
    res = client.post(f"{api}/admin/users", json=_new_user(email, password), headers=auth)
    assert res.status_code == 422
    # Two published values are also shorter than the minimum, so either refusal is correct;
    # what matters is that the account is not created and the password is not stored.
    assert "password" in res.json()["detail"].lower()
    assert not _exists(client, api, auth, email)


def test_creating_a_user_with_a_short_password_is_refused(client, api, auth):
    email = "policy-short@scholara.com.bd"
    res = client.post(f"{api}/admin/users", json=_new_user(email, "Short@1"), headers=auth)
    assert res.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in res.json()["detail"]
    assert not _exists(client, api, auth, email)


def test_a_published_password_of_publishing_length_is_refused_for_being_published(client, api, auth):
    """Longest published value (Counselor@123 is short) - proves the list, not just the length."""
    long_published = "Counselor@123" + "4" * 20  # not published: this must be accepted
    refused = client.post(f"{api}/admin/users",
                          json=_new_user("policy-longpub@scholara.com.bd", "Senior@12345"),
                          headers=auth)
    assert refused.status_code == 422
    assert "published" in refused.json()["detail"].lower()
    assert not _exists(client, api, auth, "policy-longpub@scholara.com.bd")

    accepted = client.post(f"{api}/admin/users",
                           json=_new_user("policy-longok@scholara.com.bd", long_published),
                           headers=auth)
    assert accepted.status_code == 200, accepted.text


def test_creating_a_user_with_a_valid_password_works_and_the_password_never_returns(client, api, auth):
    email = "policy-valid@scholara.com.bd"
    secret = "An-actually-fine-passphrase-42"
    res = client.post(f"{api}/admin/users", json=_new_user(email, secret), headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["email"] == email
    # The plaintext never comes back in any representation.
    assert "password" not in body
    assert secret not in res.text

    # The stored hash is the password: this account can sign in with it.
    login = client.post(f"{api}/admin/auth/login", json={"email": email, "password": secret})
    assert login.status_code == 200, login.text
    assert "access_token" in login.json()


# ---------------------------------------------------------------- update path
def test_updating_a_password_too_short_or_published_is_refused_and_leaves_the_old_one(client, api, auth):
    email = "policy-update@scholara.com.bd"
    original = "Original-passphrase-99"
    created = client.post(f"{api}/admin/users", json=_new_user(email, original), headers=auth)
    assert created.status_code == 200
    user_id = created.json()["id"]

    for bad in ("Short@1", *PUBLISHED):
        res = client.put(f"{api}/admin/users/{user_id}", json={"password": bad}, headers=auth)
        assert res.status_code == 422, bad
        assert "password" in res.json()["detail"].lower()

    # Nothing was written: the original password still signs in and the row is intact.
    login = client.post(f"{api}/admin/auth/login", json={"email": email, "password": original})
    assert login.status_code == 200, login.text


def test_updating_to_a_valid_password_replaces_it(client, api, auth):
    email = "policy-rotate@scholara.com.bd"
    created = client.post(f"{api}/admin/users",
                          json=_new_user(email, "First-passphrase-11"), headers=auth)
    assert created.status_code == 200
    user_id = created.json()["id"]

    new_secret = "Second-passphrase-22"
    res = client.put(f"{api}/admin/users/{user_id}", json={"password": new_secret}, headers=auth)
    assert res.status_code == 200, res.text
    assert new_secret not in res.text

    assert client.post(f"{api}/admin/auth/login",
                       json={"email": email, "password": new_secret}).status_code == 200
    old = client.post(f"{api}/admin/auth/login", json={"email": email, "password": "First-passphrase-11"})
    assert old.status_code == 401


# ---------------------------------------------------------------- unchanged behaviour
def test_resources_without_a_password_field_are_unaffected(client, api, auth):
    """The policy only guards staff accounts; a 'password' key on a content row is not a password.

    NOTE: the generic serialiser returns every column, so this response also carries the
    pbkdf2 hash of the seed users - a pre-existing exposure reported separately, not asserted
    here as desired behaviour.
    """
    res = client.post(f"{api}/admin/leads",
                      json={"reference": "LEAD-POLICY-1", "full_name": "Policy Probe",
                            "phone": "+8801711223344", "source": "WEBSITE",
                            "password": "x"},
                      headers=auth)
    assert res.status_code in (200, 409)  # 409 only if a previous run created the same reference
