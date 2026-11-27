"""W2 acceptance tests - schema-drift detection and the degraded assistant mode.

A database that is behind the code (an un-migrated demo file, or a deployment where
``alembic upgrade head`` was skipped) must produce an honest, controlled answer: no 500, no
invented figures and no quiet fall back to demonstration rows.
"""
from __future__ import annotations

from app.core import diagnostics
from app.core import verification_policy as policy
from app.core.database import Base
from app.services import assistant as tools_mod
from app.services import assistant_engine, retrieval
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from task2_helpers import financial_numbers


def _schema_error() -> OperationalError:
    return OperationalError(
        "SELECT knowledge_chunks.id FROM knowledge_chunks",
        {},
        Exception("no such column: knowledge_chunks.provenance"),
    )


def test_schema_drift_reports_missing_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["countries"]])
    with Session(engine) as session:
        drift = diagnostics.schema_drift(session)
    assert drift["detected"] is True
    assert "programs" in drift["missing_tables"]
    assert "knowledge_chunks" in drift["missing_tables"]
    assert drift["missing_table_count"] >= 2


def test_schema_drift_reports_a_missing_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dropped-column.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_knowledge_chunks_provenance")
        connection.exec_driver_sql("ALTER TABLE knowledge_chunks DROP COLUMN provenance")
    with Session(engine) as session:
        drift = diagnostics.schema_drift(session)
    assert drift["detected"] is True
    assert "knowledge_chunks.provenance" in drift["missing_columns"]


def test_retrieval_schema_failure_returns_degraded_mode_without_inventing_figures(monkeypatch, db):
    calls = {"llm": 0}

    def fail(*args, **kwargs):
        raise _schema_error()

    monkeypatch.setattr(retrieval, "search", fail)
    monkeypatch.setattr(assistant_engine, "_llm_available", lambda: True)
    monkeypatch.setattr(assistant_engine, "_call_llm",
                        lambda *a, **k: calls.__setitem__("llm", calls["llm"] + 1))

    result = assistant_engine.ask(
        db, "What is the annual tuition for a Master's in Canada?",
        session_id="task2-degraded-retrieval",
    )
    assert result["mode"] == "degraded"
    assert result["grounded"] is False
    assert result["data"] == {}
    assert result["sources"] == []
    assert result["answer"] == assistant_engine.DEGRADED_MESSAGE
    assert "temporarily unavailable" in result["answer"]
    assert financial_numbers(result["answer"]) == [], "a degraded answer must quote no figures"
    assert calls["llm"] == 0, "no answer-generation model may run without verified records"


def test_tool_data_access_failure_returns_degraded_mode(monkeypatch, db):
    def fail(*args, **kwargs):
        raise _schema_error()

    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    monkeypatch.setattr(tools_mod, "answer", fail)

    result = assistant_engine.ask(
        db, "Are there scholarships for a Master's in Germany?",
        session_id="task2-degraded-tools",
    )
    assert result["mode"] == "degraded"
    assert result["grounded"] is False
    assert result["sources"] == []
    assert result["data"] == {}
    assert financial_numbers(result["answer"]) == []


def test_the_public_ask_endpoint_never_turns_a_schema_failure_into_a_500(client, api, monkeypatch):
    def fail(*args, **kwargs):
        raise _schema_error()

    monkeypatch.setattr(retrieval, "search", fail)
    res = client.post(f"{api}/assistant/ask", json={
        "question": "What is the tuition for a Master's in Spain?",
        "session_id": "task2-degraded-http",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "degraded"
    assert body["grounded"] is False
    assert body["answer"] == assistant_engine.DEGRADED_MESSAGE
    assert financial_numbers(body["answer"]) == []


def test_the_health_endpoint_reports_readiness_drift_counts_and_policy(client, api, db):
    from app.models.crm import KnowledgeChunk
    from sqlalchemy import select

    # Build the corpus here rather than relying on an earlier test in the shared session to
    # have done it; otherwise this acceptance test fails when run on its own.
    retrieval.rebuild_index(db)
    db.commit()
    res = client.get(f"{api}/assistant/health")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["status"] == "ok"
    assert body["database"]["schema_drift"] is False
    # The endpoint's count is the policy's count, not a raw confidence count.
    usable = [c for c in db.scalars(select(KnowledgeChunk))
              if policy.evaluate_record(db, c, entity_type=c.entity_type).usable]
    assert body["corpus"]["chunks_verified"] == len(usable)
    assert body["corpus"]["chunks_demo"] > 0
    assert body["corpus"]["corpus_ready"] is bool(usable)
    assert all((c.provenance or "").upper() != "DEMO" for c in usable), \
        "no demonstration passage may be counted as a verified record"
    # 17 approved countries plus the Scotland region. Other test suites import extra catalogue
    # rows into this shared database, so the contract is "at least the approved set", and the
    # exact composition is asserted by the destination suite.
    assert body["catalogue"]["destinations"] >= 18
    listing = client.get(f"{api}/countries").json()
    codes = {c["iso_code"] for c in listing}
    assert {"US", "GB", "CA", "AU", "IE", "DE", "MY", "HU", "CY", "AE", "KR", "NZ",
            "SA", "JP", "IT", "FI", "ES"} <= codes
    assert next(c for c in listing if c["iso_code"] == "GB")["regions"][0]["iso_code"] == "GB-SCT"
    assert body["catalogue"]["universities"] > 0
    assert body["policy"]["policy_version"] == policy.POLICY_VERSION
    assert body["policy"]["max_age_days"] >= 1
    assert body["gate"]["refusal"]

    text = res.text.lower()
    for secret in ("sqlite", "database_url", "password", "postgres://", "postgresql://", "secret"):
        assert secret not in text, f"the public health endpoint must not expose {secret!r}"
