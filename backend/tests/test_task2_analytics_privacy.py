"""Analytics ingestion must never persist a portal bearer capability."""
from __future__ import annotations

from app.models.crm import AnalyticsEvent
from sqlalchemy import select


def test_private_portal_path_is_removed_from_analytics_storage(client, api, db):
    secret = "analytics-secret-capability"
    res = client.post(f"{api}/track", json={
        "name": "page_view",
        "path": f"/my/{secret}",
        "utm": {"utm_source": "x", "landing_path": f"/my/{secret}"},
        "meta": {"path": f"/my/{secret}", "nested": {"ref": f"https://site/my/{secret}"}},
    })
    assert res.status_code == 200, res.text
    row = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert row is not None
    assert row.path is None
    assert secret not in repr(row.utm)
    assert secret not in repr(row.meta)


def test_public_analytics_path_is_preserved_without_query_or_fragment(client, api, db):
    res = client.post(f"{api}/track", json={
        "name": "page_view", "path": "/programs?utm_source=x#top",
        "meta": {"path": "/programs?utm_source=x"},
    })
    assert res.status_code == 200, res.text
    row = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert row is not None
    assert row.path == "/programs"
