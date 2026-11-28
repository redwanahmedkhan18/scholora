"""Operational self-checks for the assistant: schema drift, corpus readiness, policy.

Two questions are answered here, and they are different:

* **Is the database the code expects?** A tracked demo database, or a production database that
  has not had the latest migration applied, is missing tables and columns. Without a check that
  shows up as a 500 from ``/assistant/ask`` with no useful explanation.
* **Can the assistant actually answer?** A corpus of 117 chunks, none of which is verified, is
  *ready to serve nothing* - which is a different state from "broken" and must be reported as
  such rather than papered over.

Nothing returned from here includes credentials, connection strings, student records or
question text. Schema *names* are returned only to staff; the public endpoint reports counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import verification_policy as policy
from app.models.crm import KnowledgeChunk
from app.models.education import Country, Program, Scholarship, University

log = logging.getLogger(__name__)


def schema_drift(db: Session) -> Dict[str, Any]:
    """Tables and columns the ORM expects but the live database does not have.

    Only *missing* things are reported. A database carrying extra columns (a pgvector column the
    model does not map, an older feature's leftovers) is not a problem, so it is ignored.
    """
    from app.core.database import Base

    result: Dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "missing_tables": [],
        "missing_columns": [],
        "error": None,
    }
    try:
        inspector = inspect(db.get_bind())
        present = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in present:
                result["missing_tables"].append(table.name)
                continue
            columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in columns:
                    result["missing_columns"].append(f"{table.name}.{column.name}")
    except SQLAlchemyError as exc:  # pragma: no cover - database unreachable
        result["error"] = type(exc).__name__
    drift = bool(result["missing_tables"] or result["missing_columns"] or result["error"])
    result["detected"] = drift
    result["missing_table_count"] = len(result["missing_tables"])
    result["missing_column_count"] = len(result["missing_columns"])
    return result


def corpus_status(db: Session) -> Dict[str, Any]:
    """How much of the corpus can actually be quoted today."""
    status: Dict[str, Any] = {
        "chunks_total": 0, "chunks_verified": 0, "chunks_demo": 0,
        "by_confidence": {}, "latest_verification": None,
        "corpus_ready": False, "reason": "empty",
    }
    try:
        rows = db.execute(
            select(KnowledgeChunk.confidence, KnowledgeChunk.provenance, func.count())
            .group_by(KnowledgeChunk.confidence, KnowledgeChunk.provenance)
        ).all()
        by_confidence: Dict[str, int] = {}
        for confidence, provenance, count in rows:
            key = (confidence or "UNKNOWN").upper()
            by_confidence[key] = by_confidence.get(key, 0) + int(count)
            status["chunks_total"] += int(count)
            if (provenance or "").upper() == "DEMO":
                status["chunks_demo"] += int(count)
        status["by_confidence"] = by_confidence

        verified = list(db.execute(
            select(KnowledgeChunk).where(func.upper(KnowledgeChunk.confidence) == "VERIFIED")
        ).scalars())
        conflicts = policy.conflicts_in(db)
        usable = [c for c in verified
                  if policy.evaluate_record(db, c, entity_type=c.entity_type,
                                            conflicts=conflicts).usable]
        status["chunks_verified"] = len(usable)
        stamps = [c.verified_at for c in usable if c.verified_at]
        if stamps:
            status["latest_verification"] = max(stamps).isoformat()
    except SQLAlchemyError as exc:  # pragma: no cover
        status["reason"] = f"error:{type(exc).__name__}"
        return status

    if status["chunks_total"] == 0:
        status["reason"] = "empty"
    elif status["chunks_verified"] == 0:
        status["reason"] = "no_verified_records"
    else:
        status["corpus_ready"] = True
        status["reason"] = "ready"
    return status


def record_counts(db: Session) -> Dict[str, int]:
    """Catalogue size, and how much of it is quotable. Counts only - never contents."""
    counts: Dict[str, int] = {}
    try:
        counts["destinations"] = int(db.scalar(
            select(func.count()).select_from(Country).where(Country.is_study_destination.is_(True))
        ) or 0)
        counts["universities"] = int(db.scalar(select(func.count()).select_from(University)) or 0)
        counts["programs"] = int(db.scalar(select(func.count()).select_from(Program)) or 0)
        counts["scholarships"] = int(db.scalar(select(func.count()).select_from(Scholarship)) or 0)
    except SQLAlchemyError:
        pass
    return counts


def assistant_health(db: Session) -> Dict[str, Any]:
    """The public health payload. Safe to return to the widget: counts and versions only."""
    drift = schema_drift(db)
    corpus = corpus_status(db)
    return {
        "status": "ok" if not drift["detected"] else "degraded",
        "database": {
            "schema_drift": drift["detected"],
            "missing_tables": drift["missing_table_count"],
            "missing_columns": drift["missing_column_count"],
        },
        "corpus": corpus,
        "catalogue": record_counts(db),
        "policy": policy.deployment_summary(),
        "gate": {"domain_gate_enabled": None},   # filled in by the router
        "checked_at": drift["checked_at"],
    }


def log_schema_drift(db: Session) -> List[str]:
    """Startup diagnostic: say plainly what is missing, without failing the boot.

    A missing column is an operational fact, not a reason to refuse to start - the assistant has
    a degraded mode that reports it honestly, and the rest of the site does not depend on it.
    """
    drift = schema_drift(db)
    if drift["error"]:
        log.warning("assistant: schema inspection failed (%s)", drift["error"])
        return []
    if not drift["detected"]:
        log.info("assistant: database schema matches the models")
        return []
    missing = list(drift["missing_tables"]) + list(drift["missing_columns"])
    log.warning(
        "assistant: database schema is behind the code - %d table(s) and %d column(s) missing: %s. "
        "Run `alembic upgrade head`; until then the assistant answers in degraded mode.",
        drift["missing_table_count"], drift["missing_column_count"], ", ".join(missing[:12]),
    )
    return missing
