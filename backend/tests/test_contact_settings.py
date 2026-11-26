"""Public contact information: one database row, validated writes, a narrow public read.

The failure these tests lock down is the one that shipped: the header and footer rendered
seeded placeholder values, the settings endpoint accepted *any* string for every column, and
nothing revalidated the cached pages after an administrator saved a change.
"""
from __future__ import annotations

import pytest
from app.core import validation
from app.models.content import SiteSetting
from sqlalchemy import select

PUBLIC_CONTACT_KEYS = {
    "phone", "phone_display", "extra_phones", "email", "extra_emails",
    "whatsapp", "facebook_url", "office_address", "office_hours", "updated_at",
}


def _valid_payload(**overrides):
    payload = {
        "primary_phone": "01711 223344",
        "whatsapp_number": "+880 1711-223344",
        "email": "Hello@Scholara.com.bd",
        "facebook_url": "https://www.facebook.com/scholara.bd",
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("raw,expected", [
    ("01711223344", "+8801711223344"),
    ("+880 1711-223344", "+8801711223344"),
    ("8801711223344", "+8801711223344"),
    ("  (+880) 1711 223344 ", "+8801711223344"),
    ("+44 20 7946 0958", "+442079460958"),
])
def test_phone_normalisation_is_stable(raw, expected):
    assert validation.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", "not-a-phone", "12345", "+0123456789", "0171122334",
    "0171122334455667788", "+8801711223344x",
])
def test_bad_phone_numbers_are_rejected(raw):
    with pytest.raises(ValueError):
        validation.normalize_phone(raw)


def test_local_number_needs_no_country_code_but_other_countries_do():
    with pytest.raises(ValueError, match="country code"):
        validation.normalize_phone("2079460958")


def test_phone_display_is_grouped_but_links_stay_digits():
    assert validation.phone_display("+8801711223344") == "+880 1711-223344"
    assert validation.whatsapp_number("+880 1711-223344") == "8801711223344"


@pytest.mark.parametrize("raw", [
    "javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
    "http://www.facebook.com/scholara", "https://evil.example/scholara",
    "https://www.facebook.com.evil.example/scholara", "https://www.facebook.com",
    "//www.facebook.com/scholara", "https://user:pass@www.facebook.com/scholara",
])
def test_facebook_url_must_be_a_real_https_page(raw):
    with pytest.raises(ValueError):
        validation.normalize_facebook_url(raw)


def test_facebook_url_accepts_common_forms():
    for url in ["https://www.facebook.com/scholara.bd",
                "https://facebook.com/profile.php",
                "https://m.facebook.com/scholara.bd"]:
        assert validation.normalize_facebook_url(url) == url


def test_email_is_normalised_and_checked():
    assert validation.normalize_email(" Hello@Scholara.COM ") == "hello@scholara.com"
    with pytest.raises(ValueError):
        validation.normalize_email("not-an-email")


# --------------------------------------------------------------- public read
def test_public_contact_exposes_only_approved_fields(client, api, auth):
    res = client.put(f"{api}/admin/settings/contact", headers=auth, json=_valid_payload())
    assert res.status_code == 200, res.text

    public = client.get(f"{api}/contact")
    assert public.status_code == 200
    body = public.json()
    assert set(body) <= PUBLIC_CONTACT_KEYS, set(body) - PUBLIC_CONTACT_KEYS
    assert body["phone"] == "+8801711223344"
    assert body["phone_display"] == "+880 1711-223344"
    assert body["email"] == "hello@scholara.com.bd"
    assert body["whatsapp"]["link_number"] == "8801711223344"
    assert body["whatsapp"]["enabled"] is True
    assert body["facebook_url"] == "https://www.facebook.com/scholara.bd"
    # Nothing administrative leaks through the public document.
    for forbidden in ("ga4_measurement_id", "meta_pixel_id", "site_url", "id", "phones"):
        assert forbidden not in body


def test_public_contact_is_unauthenticated_but_admin_read_is_not(client, api):
    assert client.get(f"{api}/contact").status_code == 200
    assert client.get(f"{api}/admin/settings/contact").status_code == 401


def test_non_admin_cannot_read_or_write_contact_settings(client, api):
    token = client.post(f"{api}/admin/auth/login", json={
        "email": "counselor@scholara.com.bd", "password": "Counselor@123",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{api}/admin/settings/contact", headers=headers).status_code == 403
    assert client.put(f"{api}/admin/settings/contact", headers=headers,
                      json=_valid_payload()).status_code == 403


# ------------------------------------------------------------------- writes
def test_admin_update_is_persisted_and_published_immediately(client, api, auth, db):
    res = client.put(f"{api}/admin/settings/contact", headers=auth, json=_valid_payload(
        primary_phone="+44 20 7946 0958",
        email="office@example.org",
        whatsapp_number="01711998877",
        facebook_url="https://www.facebook.com/scholara.official",
    ))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["primary_phone"] == "+442079460958"
    assert body["whatsapp_number"] == "+8801711998877"
    assert body["email"] == "office@example.org"

    # Stored, not just echoed: read the row the public endpoint reads.
    db.expire_all()
    row = db.get(SiteSetting, 1)
    assert row.phones[0] == "+442079460958"
    assert row.emails[0] == "office@example.org"
    assert row.whatsapp_number == "+8801711998877"

    public = client.get(f"{api}/contact").json()
    assert public["phone"] == "+442079460958"
    assert public["facebook_url"] == "https://www.facebook.com/scholara.official"


def test_extra_phone_numbers_survive_an_edit(client, api, auth, db):
    db.expire_all()
    row = db.get(SiteSetting, 1)
    row.phones = ["+8801711223344", "+880255550000"]
    db.commit()

    assert client.put(f"{api}/admin/settings/contact", headers=auth,
                      json=_valid_payload(primary_phone="+8801711000000")).status_code == 200
    db.expire_all()
    phones = db.get(SiteSetting, 1).phones
    assert phones[0] == "+8801711000000"
    # A second office number is not silently dropped when the first one is edited.
    assert "+880255550000" in phones


def test_the_full_phone_list_can_be_sent_so_an_old_number_can_be_retired(client, api, auth, db):
    """Editing only the primary number used to keep the old one published for ever."""
    db.expire_all()
    row = db.get(SiteSetting, 1)
    row.phones = ["+8801711223344", "+880255550000"]
    row.emails = ["old@scholara.com.bd", "second@scholara.com.bd"]
    db.commit()

    res = client.put(f"{api}/admin/settings/contact", headers=auth,
                     json=_valid_payload(primary_phone="+8801711000000",
                                         email="new@scholara.com.bd",
                                         extra_phones=["+8801911223344"],
                                         extra_emails=[]))
    assert res.status_code == 200, res.text
    db.expire_all()
    row = db.get(SiteSetting, 1)
    assert row.phones == ["+8801711000000", "+8801911223344"]
    assert row.emails == ["new@scholara.com.bd"]
    assert res.json()["extra_phones"] == ["+8801911223344"]


def test_extra_channels_are_validated_like_the_primary_one(client, api, auth):
    bad_phone = client.put(f"{api}/admin/settings/contact", headers=auth,
                           json=_valid_payload(extra_phones=["call the office"]))
    assert bad_phone.status_code == 422
    bad_email = client.put(f"{api}/admin/settings/contact", headers=auth,
                           json=_valid_payload(extra_emails=["second@"]))
    assert bad_email.status_code == 422


@pytest.mark.parametrize("field,value,expected_fragment", [
    ("primary_phone", "call-me", "phone"),
    ("whatsapp_number", "+++", "WhatsApp"),
    ("email", "nope", "email"),
    ("facebook_url", "javascript:alert(1)", "https"),
    ("facebook_url", "https://notfacebook.example/page", "facebook.com"),
])
def test_invalid_contact_values_are_refused_with_a_reason(client, api, auth, field, value,
                                                          expected_fragment):
    before = client.get(f"{api}/contact").json()
    res = client.put(f"{api}/admin/settings/contact", headers=auth,
                     json=_valid_payload(**{field: value}))
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    message = " ".join(str(item.get("msg", item)) for item in detail)
    assert expected_fragment.lower() in message.lower(), message
    # A rejected write must not partially persist.
    assert client.get(f"{api}/contact").json() == before


def test_contact_change_is_audited(client, api, auth):
    assert client.put(f"{api}/admin/settings/contact", headers=auth,
                      json=_valid_payload(email="audit@example.org")).status_code == 200
    events = client.get(f"{api}/admin/audit-logs?page_size=10", headers=auth).json()
    rows = events.get("items", events if isinstance(events, list) else [])
    actions = [r.get("action") for r in rows]
    assert "UPDATE_CONTACT_SETTINGS" in actions


# ------------------------------------------------- mode: generic settings form
def test_generic_settings_form_cannot_store_a_javascript_url(client, api, auth):
    res = client.put(f"{api}/admin/settings", headers=auth,
                     json={"facebook_page_url": "javascript:alert(1)"})
    assert res.status_code == 422
    assert client.get(f"{api}/contact").json()["facebook_url"] != "javascript:alert(1)"


def test_generic_settings_form_validates_phone_and_email_lists(client, api, auth):
    assert client.put(f"{api}/admin/settings", headers=auth,
                      json={"phones": ["nope"]}).status_code == 422
    assert client.put(f"{api}/admin/settings", headers=auth,
                      json={"emails": ["nope"]}).status_code == 422


def test_generic_settings_form_normalises_and_ignores_unknown_keys(client, api, auth):
    res = client.put(f"{api}/admin/settings", headers=auth,
                     json={"phones": ["01711990011"], "not_a_column": "x", "id": 99})
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == ["phones"]
    assert client.get(f"{api}/contact").json()["phone"] == "+8801711990011"


def test_generic_settings_write_is_audited_by_field_name(client, api, auth):
    client.put(f"{api}/admin/settings", headers=auth, json={"brand_short": "Scholara"})
    rows = client.get(f"{api}/admin/audit-logs?page_size=10", headers=auth).json()
    items = rows.get("items", rows if isinstance(rows, list) else [])
    assert any(r.get("action") == "UPDATE_SETTINGS" for r in items)


# ------------------------------------------------------- operator CLI fallback
def test_operator_cli_can_set_contact_without_a_browser(db):
    from app import mgmt_admin

    assert mgmt_admin.main(["--contact", "01711556677", "+8801711556677",
                            "desk@example.org", "https://www.facebook.com/scholara.bd"]) == 0
    db.expire_all()
    row = db.get(SiteSetting, 1)
    assert row.phones[0] == "+8801711556677"
    assert row.emails[0] == "desk@example.org"


def test_operator_cli_rejects_an_invalid_value(db):
    from app import mgmt_admin

    with pytest.raises(SystemExit):
        mgmt_admin.main(["--contact", "nope"])


def test_only_one_settings_row_exists(db):
    """No competing source of truth: the site reads row 1 and nothing else."""
    assert len(db.execute(select(SiteSetting)).scalars().all()) == 1
