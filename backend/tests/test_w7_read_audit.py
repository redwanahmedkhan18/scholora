"""W7-T4 (D3): one audit event per staff request that exposes student data.

Assertions read the ``audit_logs`` table directly. A watermark on ``audit_logs.id`` scopes each
test, because SQLite recycles integer primary keys and the suite shares one database - without it
a row from an earlier test can look like a new event.
"""
from __future__ import annotations

import os

import pytest
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.crm import AuditLog, Lead, StudentProfile, User
from app.services import read_audit
from sqlalchemy import func, select


# --------------------------------------------------------------------- helpers
def session():
    return SessionLocal()


def watermark() -> int:
    with session() as db:
        return int(db.execute(select(func.coalesce(func.max(AuditLog.id), 0))).scalar_one())


def reads_since(mark: int) -> list[AuditLog]:
    with session() as db:
        return list(db.execute(
            select(AuditLog).where(AuditLog.id > mark, AuditLog.action == read_audit.ACTION)
            .order_by(AuditLog.id)).scalars().all())


def make_leads(count: int, marker: str) -> list[int]:
    ids = []
    with session() as db:
        for i in range(count):
            row = Lead(reference=f"{marker}-{i}-{os.urandom(3).hex()}",
                       full_name=f"{marker} Student{i}", phone=f"+8801712{900000 + i}",
                       email=f"{marker.lower()}{i}@example.com", stage="NEW",
                       consent_contact=True)
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    return ids


def staff_token(role: str = "COUNSELOR") -> dict:
    with session() as db:
        user = User(email=f"read-audit-{os.urandom(4).hex()}@example.com", full_name="Probe",
                    password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}"}


# ------------------------------------------------- 1. one event per request
def test_an_authorized_read_creates_exactly_one_event(client, api, auth):
    mark = watermark()
    res = client.get(f"{api}/admin/leads", headers=auth)
    assert res.status_code == 200, res.text
    events = reads_since(mark)
    assert len(events) == 1
    assert events[0].action == "READ"
    assert events[0].entity_type == "leads"
    assert events[0].detail == {"operation": "list"}


def test_many_rows_still_produce_one_event(client, api, auth):
    make_leads(6, "ManyRows")
    mark = watermark()
    res = client.get(f"{api}/admin/leads", headers=auth)
    assert res.status_code == 200
    assert len(res.json()["items"]) >= 6, "the response must actually contain several rows"
    assert len(reads_since(mark)) == 1, "auditing is per request, never per row"


def test_an_empty_result_still_produces_one_event(client, api, auth):
    mark = watermark()
    res = client.get(f"{api}/admin/leads", headers=auth, params={"q": "zzz-no-such-lead-zzz"})
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert len(reads_since(mark)) == 1, "the resource was accessed even though nothing matched"


def test_pagination_and_filters_produce_one_event_each_request(client, api, auth):
    make_leads(3, "Paged")
    mark = watermark()
    for page in (1, 2, 3):
        r = client.get(f"{api}/admin/leads", headers=auth,
                       params={"page": page, "page_size": 1, "q": "Paged"})
        assert r.status_code == 200
    assert len(reads_since(mark)) == 3, "three requests, three events"


def test_detail_read_records_the_row_identifier(client, api, auth):
    [lead_id] = make_leads(1, "DetailRead")
    mark = watermark()
    res = client.get(f"{api}/admin/leads/{lead_id}", headers=auth)
    assert res.status_code == 200
    event = reads_since(mark)[0]
    assert event.entity_type == "leads"
    assert event.entity_id == lead_id
    assert event.detail == {"operation": "detail"}


def test_each_staff_surface_is_audited_with_its_own_operation(client, api, auth):
    mark = watermark()
    assert client.get(f"{api}/admin/dashboard", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/counselor/dashboard", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/applications/board", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/documents/queue", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/notifications/inbox", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/assistant/conversations", headers=auth).status_code == 200

    pairs = [(e.entity_type, e.detail["operation"]) for e in reads_since(mark)]
    assert pairs == [
        ("dashboard", "dashboard"),
        ("counselor-dashboard", "dashboard"),
        ("applications", "board"),
        ("documents", "queue"),
        ("notifications", "inbox"),
        ("assistant-conversations", "list"),
    ]


# ------------------------------------------- 5. denied requests are not audited
def test_a_student_role_cannot_read_and_leaves_no_read_event(client, api):
    headers = staff_token("STUDENT")
    mark = watermark()
    res = client.get(f"{api}/admin/leads", headers=headers)
    assert res.status_code in (401, 403), res.text
    assert reads_since(mark) == [], "a refused request must not be recorded as a successful read"


def test_an_unauthenticated_request_leaves_no_read_event(client, api):
    mark = watermark()
    assert client.get(f"{api}/admin/leads").status_code == 401
    assert reads_since(mark) == []


# ------------------------------- 6, 7. correct classification of other reads
def test_the_student_portal_is_not_a_staff_read(client, api):
    """The portal is student-owned (bearer token), not a staff read, and must not be audited."""
    res = client.post(f"{api}/profiles", json={"full_name": "Portal Owner", "phone": "+8801712999999"})
    token = res.json()["token"]
    mark = watermark()
    assert client.get(f"{api}/portal/{token}").status_code == 200
    assert reads_since(mark) == []


@pytest.mark.parametrize("resource", ["programs", "universities", "faqs"])
def test_non_student_admin_reads_are_not_audited(client, api, auth, resource):
    mark = watermark()
    res = client.get(f"{api}/admin/{resource}", headers=auth)
    assert res.status_code == 200, res.text
    assert reads_since(mark) == [], f"{resource} is institutional content, not student data"


def test_settings_and_resource_listing_are_not_audited(client, api, auth):
    mark = watermark()
    assert client.get(f"{api}/admin/resources", headers=auth).status_code == 200
    assert client.get(f"{api}/admin/settings", headers=auth).status_code == 200
    assert reads_since(mark) == []


def test_reading_the_audit_log_is_not_itself_audited(client, api, auth):
    """Avoiding a recursive audit stream: the viewer read is deliberately not recorded."""
    mark = watermark()
    res = client.get(f"{api}/admin/audit/log", headers=auth)
    assert res.status_code == 200, res.text
    assert reads_since(mark) == []


# --------------------------------------------- 8. event fields are correct
def test_event_carries_actor_resource_operation_time_and_id(client, api, auth):
    [lead_id] = make_leads(1, "FieldsCheck")
    mark = watermark()
    client.get(f"{api}/admin/leads/{lead_id}", headers=auth)
    event = reads_since(mark)[0]
    assert event.actor_id is not None
    assert event.actor_email and "@" in event.actor_email
    assert event.entity_type == "leads"
    assert event.entity_id == lead_id
    assert event.detail == {"operation": "detail"}
    assert event.created_at is not None


# --------------------------------------------- 9. no PII or caller input
def test_audit_metadata_contains_no_pii_or_caller_supplied_strings(client, api, auth):
    make_leads(2, "SecretPii")
    mark = watermark()
    # A filter that is itself a student's name: it must not be copied into the event.
    client.get(f"{api}/admin/leads", headers=auth, params={"q": "SecretPii Student0"})
    client.get(f"{api}/admin/leads", headers=auth)
    client.get(f"{api}/admin/counselor/dashboard", headers=auth)
    client.get(f"{api}/admin/documents/queue", headers=auth)
    client.get(f"{api}/admin/assistant/conversations", headers=auth)

    events = reads_since(mark)
    assert events
    blob = repr([(e.action, e.entity_type, e.entity_id, e.detail, e.actor_email, e.ip_address)
                 for e in events])
    for forbidden in ("SecretPii", "Student0", "@example.com", "+88017", "8801712900000",
                      "q=", "page_size", "token", "password", "credential"):
        assert forbidden not in blob, f"audit metadata leaked {forbidden!r}"

    # Only the three declared keys may ever be populated.
    allowed_keys = {"action", "entity_type", "entity_id", "detail", "actor_id", "actor_email",
                    "created_at", "updated_at", "id", "ip_address"}
    for e in events:
        populated = {c.name for c in e.__table__.columns if getattr(e, c.name) not in (None, {}, "")}
        assert populated <= allowed_keys, populated
        assert set(e.detail) == {"operation"}


def test_the_service_rejects_a_resource_outside_its_allowlist():
    """A path segment must never become an audit label."""
    with session() as db:
        # Any staff user will do. Naming id 1 assumed a database whose sequences had never been
        # used; when the tables were not dropped between runs the seed allocated new ids and this
        # test failed for a reason that had nothing to do with read auditing.
        user = db.scalars(select(User).limit(1)).first()
        with pytest.raises(ValueError):
            read_audit.audit_student_read(db, user, resource="../../etc/passwd", operation="list")
        with pytest.raises(ValueError):
            read_audit.audit_student_read(db, user, resource="leads", operation="DROP TABLE")
        assert not read_audit.is_student_resource("programs")


# ------------------------------------- 10. audit failure must not leak data
def test_an_audit_failure_blocks_the_student_data_response(client, api, auth, monkeypatch):
    def boom(*a, **kw):
        raise read_audit.AuditUnavailable("audit store unavailable")

    monkeypatch.setattr(read_audit, "audit_student_read", boom)

    mark = watermark()
    res = client.get(f"{api}/admin/leads", headers=auth)
    assert res.status_code == 503, res.text
    assert "items" not in res.json(), "student data must not escape unaudited"
    assert reads_since(mark) == []


def test_the_service_raises_when_its_commit_fails():
    class FailingSession:
        committed = False
        rolled_back = False

        def add(self, _obj):
            pass

        def commit(self):
            raise RuntimeError("database is down")

        def rollback(self):
            FailingSession.rolled_back = True

    with session() as db:
        # Any staff user will do. Naming id 1 assumed a database whose sequences had never been
        # used; when the tables were not dropped between runs the seed allocated new ids and this
        # test failed for a reason that had nothing to do with read auditing.
        user = db.scalars(select(User).limit(1)).first()
    with pytest.raises(read_audit.AuditUnavailable):
        read_audit.audit_student_read(FailingSession(), user, resource="leads", operation="list")
    assert FailingSession.rolled_back is True


# ------------------------------------- 12. repeated requests stay independent
def test_repeated_requests_produce_independent_events(client, api, auth):
    mark = watermark()
    for _ in range(5):
        assert client.get(f"{api}/admin/leads", headers=auth).status_code == 200
    events = reads_since(mark)
    assert len(events) == 5
    assert len({e.id for e in events}) == 5, "each request gets its own row"
    assert all(e.detail == {"operation": "list"} for e in events)


def test_a_write_audit_is_still_recorded_separately_from_the_read(client, api, auth):
    """W7 read auditing must not disturb the existing write audit trail."""
    mark = watermark()
    res = client.post(f"{api}/admin/leads", headers=auth, json={
        "reference": f"WRITE-{os.urandom(3).hex()}", "full_name": "Write Probe",
        "phone": "+8801712888888", "stage": "NEW"})
    assert res.status_code == 200, res.text
    with session() as db:
        rows = list(db.execute(select(AuditLog).where(AuditLog.id > mark)
                               .order_by(AuditLog.id)).scalars().all())
    actions = [r.action for r in rows]
    assert "CREATE" in actions
    assert read_audit.ACTION not in actions, "a write is not a read"


# ------------------------------------- assistant session metadata (W7-T4 remediation)
def _make_assistant_session() -> dict:
    """A real profile + bound assistant session, so the endpoint has metadata to expose."""
    with session() as db:
        profile = StudentProfile(public_token=f"tok-sess-{os.urandom(4).hex()}",
                                 full_name="Session Owner", email="session.owner@example.com",
                                 phone="+8801712555000", research_interests=[],
                                 completed_subjects=[], preferred_countries=[],
                                 preferred_universities=[], source="WEBSITE")
        db.add(profile)
        db.commit()
        profile_id, token = profile.id, profile.public_token

    from app.core import assistant_sessions as sessions_module

    with session() as db:
        credential, row = sessions_module.issue(db, profile_token=token)
        db.commit()
        return {"sid": row.sid, "profile_id": profile_id, "credential": credential}


def test_assistant_session_listing_creates_exactly_one_event(client, api, auth):
    _make_assistant_session()
    mark = watermark()
    res = client.get(f"{api}/admin/assistant/sessions", headers=auth)
    assert res.status_code == 200, res.text
    assert res.json()["items"], "the response must actually expose session metadata"
    events = reads_since(mark)
    assert len(events) == 1, "one request, one event"
    assert events[0].entity_type == "assistant-sessions"
    assert events[0].detail == {"operation": "list"}
    assert events[0].action == "READ"
    assert events[0].actor_email


def test_assistant_session_event_carries_no_session_or_profile_identifiers(client, api, auth):
    made = _make_assistant_session()
    mark = watermark()
    client.get(f"{api}/admin/assistant/sessions", headers=auth,
               params={"active": "false", "limit": 5})
    events = reads_since(mark)
    assert len(events) == 1
    event = events[0]

    blob = repr((event.action, event.entity_type, event.entity_id, event.detail,
                 event.actor_email, event.ip_address))
    for secret in (made["sid"], made["credential"], str(made["profile_id"]),
                   "Session Owner", "session.owner@example.com", "+88017",
                   "active", "limit", "ownership", "expires_at"):
        assert secret not in blob, f"audit metadata leaked {secret!r}"

    # Nothing in the row can point at the session or the student.
    assert event.entity_id is None
    assert set(event.detail) == {"operation"}


def test_assistant_session_listing_is_not_audited_for_a_non_admin(client, api):
    headers = staff_token("COUNSELOR")
    mark = watermark()
    res = client.get(f"{api}/admin/assistant/sessions", headers=headers)
    assert res.status_code == 403, res.text
    assert reads_since(mark) == [], "a refused request is not a successful read"


def test_assistant_session_audit_failure_withholds_the_metadata(client, api, auth, monkeypatch):
    _make_assistant_session()

    def boom(*a, **kw):
        raise read_audit.AuditUnavailable("audit store unavailable")

    monkeypatch.setattr(read_audit, "audit_student_read", boom)
    mark = watermark()
    res = client.get(f"{api}/admin/assistant/sessions", headers=auth)
    assert res.status_code == 503, res.text
    assert "items" not in res.json(), "session metadata must not escape unaudited"
    assert reads_since(mark) == []


def test_assistant_session_revoke_still_works_and_is_not_a_read(client, api, auth):
    """The neighbouring write path is untouched by the new read audit."""
    made = _make_assistant_session()
    mark = watermark()
    res = client.post(f"{api}/admin/assistant/sessions/{made['sid']}/revoke", headers=auth)
    assert res.status_code == 200, res.text
    with session() as db:
        actions = [r.action for r in db.execute(
            select(AuditLog).where(AuditLog.id > mark).order_by(AuditLog.id)).scalars().all()]
    assert read_audit.ACTION not in actions
