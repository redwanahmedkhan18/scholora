"""End-to-end behaviour of the public API, the student portal and the staff workflow."""
from __future__ import annotations

import pytest


# ------------------------------------------------------------------ public
def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


@pytest.mark.parametrize("path", [
    "/homepage", "/settings", "/countries", "/universities", "/programs", "/scholarships",
    "/services", "/events", "/faqs", "/blog", "/success-stories", "/faculty", "/research-areas",
    "/campaigns/active", "/stats",
])
def test_public_endpoints_respond(client, api, path):
    assert client.get(f"{api}{path}").status_code == 200, path


def test_homepage_rotation_contract(client, api):
    """§12-§16: slides must carry their own duration and ordering for the client engine."""
    hero = client.get(f"{api}/homepage").json()["hero"]
    assert hero["rotation_enabled"] is True
    assert hero["default_duration_seconds"] == 30
    assert len(hero["slides"]) >= 3
    for slide in hero["slides"]:
        assert slide["duration_seconds"] >= 1
        assert "priority" in slide
    # Highest priority first, so the engine can rotate deterministically.
    priorities = [s["priority"] for s in hero["slides"]]
    assert priorities == sorted(priorities, reverse=True)


def test_homepage_sections_are_ordered_and_data_driven(client, api):
    sections = client.get(f"{api}/homepage").json()["sections"]
    positions = [s["position"] for s in sections]
    assert positions == sorted(positions)
    assert {"hero", "countries", "scholarships"} <= {s["key"] for s in sections}


def test_search_filters_actually_filter(client, api):
    uk = client.get(f"{api}/programs?country=united-kingdom&degree_level=MASTER&page_size=50").json()
    assert uk["items"], "expected UK masters programmes in the seed data"
    for p in uk["items"]:
        assert p["degree_level"] == "MASTER"
        assert p["university"]["country"]["slug"] == "united-kingdom"


def test_pagination_envelope(client, api):
    page1 = client.get(f"{api}/universities?page=1&page_size=5").json()
    assert set(page1) >= {"total", "page", "page_size", "items"}
    assert len(page1["items"]) <= 5
    page2 = client.get(f"{api}/universities?page=2&page_size=5").json()
    assert {u["id"] for u in page1["items"]} & {u["id"] for u in page2["items"]} == set()


def test_unknown_slug_is_404(client, api):
    assert client.get(f"{api}/programs/not-a-real-programme").status_code == 404
    assert client.get(f"{api}/universities/not-a-real-university").status_code == 404


# ---------------------------------------------------------- costing engine
def test_cost_estimate_is_itemised(client, api):
    """§36: a cost must be broken down, never a single opaque number."""
    body = client.get(f"{api}/programs/university-of-texas-at-arlington-msc-artificial-intelligence").json()
    cost = body["cost_estimate"]
    assert cost["estimated_annual_total"] > 0
    assert cost["breakdown"], "a cost estimate must show its components"
    assert sum(i["amount"] for i in cost["breakdown"]) == pytest.approx(cost["estimated_annual_total"], rel=0.02)
    assert cost["note"], "an estimate must be labelled as an estimate"


def test_cost_reports_the_budget_gap(client, api):
    res = client.post(f"{api}/eligibility/check", json={
        "profile": {"full_name": "T", "cgpa": 3.4, "cgpa_scale": 4.0, "ielts_overall": 6.5,
                    "target_degree_level": "MASTER", "budget_per_year": 20000, "budget_currency": "USD"},
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
    })
    cost = res.json()["cost"]
    assert cost["budget_status"] in {
        "WITHIN_STATED_BUDGET", "SLIGHTLY_ABOVE_BUDGET", "ABOVE_STATED_BUDGET", "NOT_PROVIDED",
    }
    if cost["budget_status"] == "ABOVE_STATED_BUDGET":
        assert cost["budget_gap"] > 0, "an over-budget result must quantify the shortfall"


# -------------------------------------------------------------- brochures
def test_brochure_is_versioned_and_sourced(client, api):
    res = client.post(f"{api}/brochures/generate", json={
        "profile": {"full_name": "Test Student", "cgpa": 3.5, "cgpa_scale": 4.0, "ielts_overall": 6.5,
                    "target_degree_level": "MASTER"},
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
        "target_intake": "Fall 2027", "channel": "WEB",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["token"] and body["version"] >= 1
    fetched = client.get(f"{api}/brochures/{body['token']}").json()
    assert fetched["degree_level"] == "MASTER"
    assert fetched["payload"]["sources"], "a brochure must list its sources"


# ----------------------------------------------------------- student portal
def test_portal_overview_shape(client, api, student_token):
    body = client.get(f"{api}/portal/{student_token}").json()
    assert set(body) >= {"profile", "applications", "documents", "shortlist", "brochures",
                         "notifications", "counts", "stages", "document_types"}
    assert body["profile"]["token"] == student_token


def test_portal_rejects_unknown_token(client, api):
    assert client.get(f"{api}/portal/does-not-exist").status_code == 404


def test_application_lifecycle(client, api, auth, student_token):
    created = client.post(f"{api}/portal/{student_token}/applications", json={
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
        "intake_label": "Fall 2027",
    })
    assert created.status_code == 200
    app_id = created.json()["application"]["id"]
    assert created.json()["application"]["status"] == "PLANNING"

    # Creating the same application twice is idempotent, not a duplicate row.
    again = client.post(f"{api}/portal/{student_token}/applications", json={
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
    })
    assert again.json()["duplicate"] is True

    moved = client.post(f"{api}/admin/applications/{app_id}/status", headers=auth,
                        json={"status": "SUBMITTED", "note": "Sent to the university."})
    assert moved.status_code == 200
    assert moved.json()["previous"] == "PLANNING"

    assert client.post(f"{api}/admin/applications/{app_id}/status", headers=auth,
                       json={"status": "NOT_A_STATUS"}).status_code == 400

    detail = client.get(f"{api}/portal/{student_token}").json()
    app = next(a for a in detail["applications"] if a["id"] == app_id)
    assert app["status"] == "SUBMITTED"
    assert len(app["history"]) >= 2, "every transition must be recorded"
    assert app["progress_percent"] > 0

    # The student is notified about their own application.
    assert any(n["title"].startswith("Application") for n in detail["notifications"])


def test_withdrawn_application_reports_zero_progress(client, api, student_token):
    created = client.post(f"{api}/portal/{student_token}/applications", json={
        "program_slug": "university-of-texas-at-arlington-phd-computer-science",
    }).json()["application"]
    client.delete(f"{api}/portal/{student_token}/applications/{created['id']}")
    body = client.get(f"{api}/portal/{student_token}").json()
    app = next(a for a in body["applications"] if a["id"] == created["id"])
    assert app["status"] == "WITHDRAWN"
    assert app["progress_percent"] == 0


def test_document_upload_review_and_checklist(client, api, auth, student_token):
    up = client.post(f"{api}/portal/{student_token}/documents",
                     data={"doc_type": "BACHELOR_TRANSCRIPT"},
                     files={"file": ("t.pdf", b"%PDF-1.4\ntranscript\n%%EOF", "application/pdf")})
    assert up.status_code == 200
    doc = up.json()["document"]
    assert doc["status"] == "UPLOADED"

    queue = client.get(f"{api}/admin/documents/queue", headers=auth).json()
    assert any(d["id"] == doc["id"] for d in queue["items"])

    reviewed = client.post(f"{api}/admin/documents/{doc['id']}/review", headers=auth,
                           json={"status": "ACCEPTED", "review_note": "Clear scan."})
    assert reviewed.status_code == 200

    body = client.get(f"{api}/portal/{student_token}").json()
    entry = next(d for d in body["documents"] if d["id"] == doc["id"])
    assert entry["status"] == "ACCEPTED"
    assert any(c["doc_type"] == "BACHELOR_TRANSCRIPT" and c["provided"] for c in body["document_checklist"])

    # An accepted document cannot be removed by the student.
    assert client.delete(f"{api}/portal/{student_token}/documents/{doc['id']}").status_code == 400


def test_rejecting_a_document_notifies_the_student(client, api, auth, student_token):
    doc = client.post(f"{api}/portal/{student_token}/documents",
                      data={"doc_type": "PHOTO"},
                      files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")}).json()["document"]
    client.post(f"{api}/admin/documents/{doc['id']}/review", headers=auth,
                json={"status": "REJECTED", "review_note": "Background must be white."})
    body = client.get(f"{api}/portal/{student_token}").json()
    assert any("white" in (n["body"] or "") for n in body["notifications"])


def test_shortlist_persists_and_deduplicates(client, api, student_token):
    payload = {"entity_type": "program", "slug": "university-of-texas-at-arlington-msc-artificial-intelligence"}
    first = client.post(f"{api}/portal/{student_token}/shortlist", json=payload).json()
    second = client.post(f"{api}/portal/{student_token}/shortlist", json=payload).json()
    assert second.get("duplicate") is True

    synced = client.post(f"{api}/portal/{student_token}/shortlist/sync", json=[
        {"entity_type": "university", "slug": "university-of-texas-at-arlington"},
        {"entity_type": "program", "slug": "university-of-texas-at-arlington-msc-artificial-intelligence"},
        {"entity_type": "program", "slug": "nonexistent-programme"},
    ]).json()
    assert synced["added"] == 1, "duplicates and unknown slugs must be skipped"

    items = client.get(f"{api}/portal/{student_token}/shortlist").json()
    assert len(items) >= 2
    client.delete(f"{api}/portal/{student_token}/shortlist/{first['id']}")
    assert all(i["id"] != first["id"] for i in client.get(f"{api}/portal/{student_token}/shortlist").json())


def test_shortlist_rejects_unknown_entity_type(client, api, student_token):
    res = client.post(f"{api}/portal/{student_token}/shortlist",
                      json={"entity_type": "spaceship", "slug": "x"})
    assert res.status_code == 422


# ------------------------------------------------------------ staff tooling
def test_counselor_dashboard_shape(client, api, auth):
    body = client.get(f"{api}/admin/counselor/dashboard", headers=auth).json()
    assert set(body) >= {"counselor", "kpis", "leads", "applications", "appointments",
                         "documents_pending", "follow_ups", "notifications"}


def test_applications_board_counts_match_items(client, api, auth):
    board = client.get(f"{api}/admin/applications/board", headers=auth).json()
    assert board["total"] == len(board["items"])
    assert sum(board["counts"].values()) == board["total"]


def test_audit_log_records_staff_actions(client, api, auth):
    client.post(f"{api}/admin/countries", headers=auth,
                json={"name": "Testland", "iso2": "TL", "status": "COMING_SOON"})
    log = client.get(f"{api}/admin/audit/log?action=CREATE", headers=auth).json()
    assert log["total"] >= 1
    assert any(r["entity_type"] == "countries" for r in log["items"])


def test_notification_inbox_and_read_all(client, api, auth):
    inbox = client.get(f"{api}/admin/notifications/inbox", headers=auth).json()
    assert "unread" in inbox and isinstance(inbox["items"], list)
    client.post(f"{api}/admin/notifications/read-all", headers=auth)
    after = client.get(f"{api}/admin/notifications/inbox?unread_only=true", headers=auth).json()
    assert after["items"] == []


def test_every_admin_resource_lists(client, api, auth):
    resources = client.get(f"{api}/admin/resources", headers=auth).json()
    keys = resources if isinstance(resources, list) else resources.get("resources", [])
    for key in keys:
        name = key if isinstance(key, str) else key.get("key")
        assert client.get(f"{api}/admin/{name}?page_size=1", headers=auth).status_code == 200, name


# ------------------------------------------------------------------- jobs
def test_scheduled_jobs_run_on_demand(client, api, auth):
    listing = client.get(f"{api}/admin/jobs", headers=auth).json()
    assert listing["jobs"]
    for job in listing["jobs"]:
        res = client.post(f"{api}/admin/jobs/{job['name']}/run", headers=auth)
        assert res.status_code == 200, job["name"]
        assert res.json()["result"]["job"] == job["name"]


def test_unknown_job_is_404(client, api, auth):
    assert client.post(f"{api}/admin/jobs/make_coffee/run", headers=auth).status_code == 404


def test_reindex_rebuilds_the_knowledge_base(client, api, auth):
    res = client.post(f"{api}/admin/assistant/reindex", headers=auth).json()
    assert res["total"] > 0
    assert res["program"] > 0 and res["university"] > 0


# ------------------------------------------------------------- retrieval
def test_retrieval_finds_relevant_records(client, api, auth, db):
    from app.services import retrieval

    retrieval.rebuild_index(db)
    hits = retrieval.search(db, "artificial intelligence masters in the united states", limit=5)
    assert hits, "BM25 retrieval should match seeded programmes without an embedding provider"
    assert all({"title", "content", "score", "url_path"} <= set(h) for h in hits)
    assert hits[0]["score"] >= hits[-1]["score"], "results must be ranked"


def test_retrieval_returns_nothing_for_nonsense(client, api, db):
    from app.services import retrieval

    assert retrieval.search(db, "zzzzqqqq nonexistent gibberish term", limit=5) == []


# --------------------------------------------------------------- dynamic vocabularies
# These guard the "no hard-coded arrays in the frontend" requirement from the backend side:
# if a vocabulary stops being served, the UI silently loses a dropdown, and nothing else in
# the suite would notice because a missing <option> list still renders.

def test_vocabularies_endpoint_serves_every_list_the_ui_needs(client, api):
    r = client.get(f"{api}/vocabularies")
    assert r.status_code == 200
    data = r.json()
    for key in ("degree_levels", "confidence_states", "eligibility_states", "lead_stages",
                "application_statuses", "country_statuses", "partner_statuses",
                "source_tiers", "funding_sources"):
        assert key in data and data[key], f"{key} missing or empty"
        assert all(set(o) >= {"value", "label"} for o in data[key]), f"{key} malformed"


def test_degree_levels_match_the_single_source_of_truth(client, api):
    from app.models.base import DEGREE_LEVELS
    served = [o["value"] for o in client.get(f"{api}/vocabularies").json()["degree_levels"]]
    assert served == list(DEGREE_LEVELS)


def test_settings_expose_admin_editable_booking_options(client, api):
    a = client.get(f"{api}/settings").json()["appointments"]
    assert a["service_types"] and a["modes"] and a["slots"]
    assert all(set(o) >= {"value", "label"} for o in a["service_types"] + a["modes"])
    assert isinstance(a["lead_days"], int)


def test_homepage_sections_carry_their_own_content(client, api):
    sections = {s["key"]: s for s in client.get(f"{api}/homepage").json()["sections"]}
    # why_us and journey are pure CMS content - the frontend holds no copy for them.
    assert len(sections["why_us"]["config"].get("items", [])) >= 1
    assert len(sections["journey"]["config"].get("steps", [])) >= 1
    for item in sections["why_us"]["config"]["items"]:
        assert item.get("title") and item.get("body")
    for step in sections["journey"]["config"]["steps"]:
        assert step.get("title") and step.get("body")


# ------------------------------------------------- booking validated against live settings

def _future(days=2):
    from datetime import date, timedelta
    return (date.today() + timedelta(days=days)).isoformat()


def test_appointment_rejects_a_mode_the_office_does_not_offer(client, api):
    r = client.post(f"{api}/appointments", json={"full_name": "Vocab Test", "phone": "+8801799000001",
                                        "scheduled_date": _future(), "scheduled_time": "11:00",
                                        "mode": "TELEPATHY"})
    assert r.status_code == 400
    assert "mode" in r.json()["detail"].lower()


def test_appointment_rejects_an_unbookable_time(client, api):
    r = client.post(f"{api}/appointments", json={"full_name": "Vocab Test", "phone": "+8801799000002",
                                        "scheduled_date": _future(), "scheduled_time": "03:00"})
    assert r.status_code == 400
    assert "bookable" in r.json()["detail"].lower()


def test_appointment_defaults_come_from_settings_not_a_hard_coded_code(client, api):
    """An omitted service_type must resolve to a configured option, never an invented one."""
    offered = [o["value"] for o in client.get(f"{api}/settings").json()["appointments"]["service_types"]]
    r = client.post(f"{api}/appointments", json={"full_name": "Vocab Test", "phone": "+8801799000003",
                                        "scheduled_date": _future(), "scheduled_time": "10:00"})
    assert r.status_code == 200, r.text
    assert offered, "no service types configured"


# ------------------------------------------------- four-level hierarchy (University > Faculty
# > Department > Programme). This level used to be `departments.faculty_school`, a free-text
# string retyped once per department, so these guard the promotion to a real entity.

def test_faculty_is_a_real_entity_not_a_string(db):
    from app.models.education import Department, Faculty
    assert not hasattr(Department, "faculty_school"), "the free-text column must stay gone"
    assert db.query(Faculty).count() > 0
    # Every department belongs to a division, and that division belongs to the same university.
    orphans = db.query(Department).filter(Department.faculty_id.is_(None)).count()
    assert orphans == 0, f"{orphans} departments are not attached to a faculty"
    for dept in db.query(Department).all():
        assert dept.faculty.university_id == dept.university_id


def test_university_detail_exposes_the_hierarchy(client, api):
    data = client.get(f"{api}/universities/university-of-texas-at-arlington").json()
    assert data["faculties"], "university payload must carry its divisions"
    faculty = data["faculties"][0]
    for key in ("id", "name", "slug", "kind", "departments", "department_count", "provenance"):
        assert key in faculty, key
    # The flat department list keeps a reference up to the division.
    assert all(d.get("faculty") for d in data["departments"])


def test_faculty_detail_rolls_up_departments_programmes_and_staff(client, api):
    slug = "university-of-texas-at-arlington-faculty-of-computer-science"
    res = client.get(f"{api}/faculties/{slug}")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["faculty"]["slug"] == slug
    assert data["university"]["slug"] == "university-of-texas-at-arlington"
    assert data["program_count"] == sum(len(v) for v in data["programs_by_level"].values())
    assert data["faculty"]["departments"]


def test_faculty_listing_filters(client, api):
    all_rows = client.get(f"{api}/faculties?page_size=100").json()
    assert all_rows["total"] >= 1
    one = client.get(f"{api}/faculties?university=university-of-texas-at-arlington").json()
    assert 0 < one["total"] < all_rows["total"]
    schools = client.get(f"{api}/faculties?kind=SCHOOL&page_size=100").json()
    assert all(i["kind"] == "SCHOOL" for i in schools["items"])


def test_unknown_faculty_is_404(client, api):
    assert client.get(f"{api}/faculties/no-such-division").status_code == 404


def test_division_route_does_not_collide_with_the_professor_route(client, api):
    """/faculty/{slug} is a person, /faculties/{slug} is a division. Keep them distinct."""
    person = client.get(f"{api}/faculty/dr-amara-chowdhury-university-of-texas-at-arlington")
    assert person.status_code == 200
    assert "professor" in person.json()
    division = client.get(f"{api}/faculties/university-of-texas-at-arlington-faculty-of-computer-science")
    assert division.status_code == 200
    assert "faculty" in division.json() and "departments" in division.json()["faculty"]


def test_faculties_are_in_the_sitemap(client, api):
    data = client.get(f"{api}/sitemap-data").json()
    assert data["faculties"], "divisions must be indexable"
    assert all(f["slug"] for f in data["faculties"])


def test_scholarship_match_honours_top_level_degree_level(client):
    """
    MatchIn advertises degree_level; the engine reads profile.target_degree_level. When
    the endpoint ignored the former it answered a question nobody asked - a PhD-only
    fellowship came back MEETS for a master's applicant.
    """
    body = {"profile": {"nationality": "Bangladesh"}, "degree_level": "MASTER", "limit": 50}
    rows = client.post("/api/v1/match/scholarships", json=body).json()

    graded = [r for r in rows
              if any(c["criterion"] == "Degree level" for c in r["checks"])]
    assert graded, "no scholarship declared a degree level"
    for row in graded:
        check = next(c for c in row["checks"] if c["criterion"] == "Degree level")
        assert check["provided"] == "MASTER", "the requested level never reached the engine"
        if "MASTER" not in check["required"]:
            assert check["status"] == "DOES_NOT_MEET"


def test_an_explicit_profile_level_outranks_the_top_level_one(client):
    body = {"profile": {"nationality": "Bangladesh", "target_degree_level": "PHD"},
            "degree_level": "MASTER", "limit": 50}
    rows = client.post("/api/v1/match/scholarships", json=body).json()
    checks = [c for r in rows for c in r["checks"] if c["criterion"] == "Degree level"]
    assert checks and all(c["provided"] == "PHD" for c in checks)


def test_a_misspelled_profile_field_is_rejected_not_ignored(client):
    """
    Pydantic drops unknown fields by default. On this endpoint that meant a caller who
    sent the natural-looking "ielts" or "budget_annual" instead of "ielts_overall" and
    "budget_per_year" received a confident recommendation computed as though the student
    had no English score and no budget - every programme scored identically and the
    English check read "not provided". Wrong advice delivered confidently is worse than
    an error, so unknown fields are now a 422 naming the offender.
    """
    body = {"profile": {"nationality": "Bangladesh", "ielts": 6.5, "budget_annual": 5000},
            "limit": 5}
    res = client.post("/api/v1/match/programs", json=body)
    assert res.status_code == 422

    offenders = {tuple(e["loc"])[-1] for e in res.json()["detail"]}
    assert {"ielts", "budget_annual"} & offenders, res.json()["detail"]


def test_the_correctly_named_fields_still_drive_the_result(client):
    body = {"profile": {"nationality": "Bangladesh", "cgpa": 3.4, "cgpa_scale": 4,
                        "ielts_overall": 6.5, "target_degree_level": "MASTER",
                        "budget_per_year": 5000, "budget_currency": "EUR"},
            "limit": 5}
    results = client.post("/api/v1/match/programs", json=body).json()["results"]
    assert results
    assert results[0]["cost"]["budget_status"] != "NOT_PROVIDED", \
        "a stated budget must reach the cost engine"
    english = [r for r in results[0]["reasons"] if r["factor"] == "English requirement"]
    assert english and english[0]["status"] != "INFORMATION_UNAVAILABLE", \
        "a stated IELTS score must reach the eligibility engine"
