"""W4 profile-continuity isolation tests.

The domain gate allows a short anaphoric follow-up only when the immediately preceding turn
belongs to the same verified owner. A session row survives a profile switch, so these tests
prove that the conversation lookup is scoped to the current profile rather than the row id.
"""
from __future__ import annotations

from app.core import assistant_sessions as sessions
from app.models.crm import AssistantConversation
from app.services import assistant_engine
from sqlalchemy import select

from task2_helpers import clear_assistant_cookies, open_assistant_session

FOLLOW_UP = "what about it?"


def _conversation(db, session_ref_id, profile_id, intent="cost"):
    row = AssistantConversation(
        session_id="continuity-test", session_ref_id=session_ref_id,
        student_profile_id=profile_id, question="masters in computer science",
        answer="an allowed prior answer", intent=intent, grounded=True,
    )
    db.add(row)
    db.commit()
    return row


def _row(db, sid):
    db.expire_all()
    return db.execute(select(sessions.AssistantSession).where(
        sessions.AssistantSession.sid == sid)).scalars().first()


def _profile(client, api) -> str:
    import uuid
    suffix = uuid.uuid4().hex[:10]
    res = client.post(f"{api}/profiles", json={
        "full_name": f"Continuity Student {suffix}",
        "email": f"continuity.{suffix}@example.com",
        "phone": "+88018" + suffix[:8], "highest_qualification": "Bachelor",
        "major": "Computer Science", "cgpa": 3.7, "cgpa_scale": 4.0,
        "target_degree_level": "MASTER", "target_discipline": "Computer Science",
    })
    assert res.status_code == 200, res.text
    return res.json()["token"]


def test_previous_turn_is_scoped_to_the_exact_profile(db):
    token_a = _profile_for_db(db, "A")
    token_b = _profile_for_db(db, "B")
    raw, row = sessions.issue(db, profile_token=token_a)
    _conversation(db, row.id, row.student_profile_id)

    assert assistant_engine._previous_turn(db, row.id, row.student_profile_id) == (True, "cost")
    profile_b = db.execute(select(sessions.StudentProfile).where(
        sessions.StudentProfile.public_token == token_b)).scalars().first()
    assert assistant_engine._previous_turn(db, row.id, profile_b.id) == (False, "")
    assert assistant_engine._previous_turn(db, row.id, None) == (False, "")


def test_a_profile_bound_conversation_cannot_authorize_anonymous_continuity(db):
    token_a = _profile_for_db(db, "A")
    _raw, anon = sessions.issue(db)
    profile = db.execute(select(sessions.StudentProfile).where(
        sessions.StudentProfile.public_token == token_a)).scalars().first()
    _conversation(db, anon.id, profile.id)
    assert assistant_engine._previous_turn(db, anon.id, None) == (False, "")


def test_legacy_unknown_ownership_never_authorizes_a_profile(db):
    token_a = _profile_for_db(db, "A")
    _raw, row = sessions.issue(db, profile_token=token_a)
    _conversation(db, row.id, None)
    assert assistant_engine._previous_turn(db, row.id, row.student_profile_id) == (False, "")


def test_a_previous_allowed_turn_cannot_authorize_student_b(client, api, db):
    token_a = _profile(client, api)
    token_b = _profile(client, api)
    try:
        state, headers = open_assistant_session(client, api, profile_token=token_a)
        row = _row(db, state["sid"])
        _conversation(db, row.id, row.student_profile_id)
        switched = client.post(f"{api}/assistant/session/bind", headers=headers,
                               json={"profile_token": token_b})
        assert switched.status_code == 200, switched.text
        headers = {sessions.CSRF_HEADER: switched.json()["csrf_token"]}
        answer = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": FOLLOW_UP, "profile_token": token_b,
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["domain"]["decision"] == "REFUSE"
        assert answer.json()["domain"]["reason"] == "out_of_scope"
    finally:
        clear_assistant_cookies(client)


def test_same_profile_follow_up_still_works(client, api, db):
    token_a = _profile(client, api)
    try:
        state, headers = open_assistant_session(client, api, profile_token=token_a)
        row = _row(db, state["sid"])
        _conversation(db, row.id, row.student_profile_id)
        answer = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": FOLLOW_UP, "profile_token": token_a,
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["domain"]["decision"] == "ALLOW"
        assert answer.json()["domain"]["reason"] == "follow_up"
    finally:
        clear_assistant_cookies(client)


def test_anonymous_context_cannot_authorize_a_profile_bound_follow_up(client, api, db):
    token_b = _profile(client, api)
    try:
        state, headers = open_assistant_session(client, api)
        row = _row(db, state["sid"])
        _conversation(db, row.id, None)
        bound = client.post(f"{api}/assistant/session/bind", headers=headers,
                            json={"profile_token": token_b})
        assert bound.status_code == 200, bound.text
        headers = {sessions.CSRF_HEADER: bound.json()["csrf_token"]}
        answer = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": FOLLOW_UP, "profile_token": token_b,
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["domain"]["decision"] == "REFUSE"
    finally:
        clear_assistant_cookies(client)


def _profile_for_db(db, label: str) -> str:
    import secrets

    from app.models.crm import StudentProfile

    token = secrets.token_urlsafe(12)
    db.add(StudentProfile(public_token=token, full_name=f"Continuity {label}"))
    db.commit()
    return token
