"""Homepage rotation engine (§6/§7/§96/§97).

The server resolves WHICH slides are eligible right now (status, date window, time-of-day
scheduling, campaign priority) and returns the whole playlist in one payload so the browser
can rotate client-side every `duration_seconds` with zero extra requests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import AdCampaign, HomepageSlide, SiteSetting


def active_slides(db: Session, now: Optional[datetime] = None, local_hour: Optional[int] = None) -> List[HomepageSlide]:
    now = now or datetime.now(timezone.utc)
    hour = local_hour if local_hour is not None else now.hour
    rows = db.execute(select(HomepageSlide).where(HomepageSlide.status == "ACTIVE")).scalars().all()
    eligible: List[HomepageSlide] = []
    for s in rows:
        if s.start_at and s.start_at > now.replace(tzinfo=None):
            continue
        if s.end_at and s.end_at < now.replace(tzinfo=None):
            continue
        if s.day_start_hour is not None and s.day_end_hour is not None:
            start, end = s.day_start_hour, s.day_end_hour
            in_window = (start <= hour < end) if start <= end else (hour >= start or hour < end)
            if not in_window:
                continue
        eligible.append(s)
    boost: Dict[int, int] = {}
    for s in eligible:
        if s.campaign_id:
            camp = db.get(AdCampaign, s.campaign_id)
            if camp and camp.status == "RUNNING":
                boost[s.id] = 25
    eligible.sort(key=lambda s: (-(s.priority + boost.get(s.id, 0)), s.id))
    return eligible


def playlist(db: Session, local_hour: Optional[int] = None) -> Dict[str, Any]:
    settings_row = db.get(SiteSetting, 1)
    default_seconds = settings_row.hero_rotation_seconds if settings_row else 30
    slides = active_slides(db, local_hour=local_hour)
    if not slides:  # never show an empty hero
        slides = list(db.execute(
            select(HomepageSlide).order_by(HomepageSlide.priority.desc()).limit(3)
        ).scalars().all())
    return {
        "rotation_enabled": bool(settings_row.hero_rotation_enabled) if settings_row else True,
        "default_duration_seconds": default_seconds,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cycle_seconds": sum(s.duration_seconds or default_seconds for s in slides),
        "slides": slides,
    }
