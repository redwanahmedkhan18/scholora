"""Retrieval for the AI assistant (§85).

The corpus is built **only from our own verified records**, never from the open web.
Every chunk keeps the source URL, source type, verification date and confidence of the
record it came from, so an answer can cite exactly what it was grounded on.

Ranking strategy, in order of preference:
  1. pgvector cosine distance, when running on PostgreSQL with embeddings present.
  2. Dense cosine similarity over stored embeddings (any database).
  3. BM25-style lexical scoring over the sparse vector — always available, no API key.

The lexical path means the assistant is fully functional with no embedding provider
configured, which is how this build runs out of the box.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.core import embeddings as E
from app.core import local_semantic
from app.core import verification_policy as policy
from app.core.config import settings
from app.models.crm import KnowledgeChunk
from app.models.education import Country, Professor, Program, Scholarship, University
from app.services import scoping

log = logging.getLogger(__name__)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "how", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "that", "the",
    "their", "there", "they", "this", "to", "was", "what", "when", "where", "which",
    "who", "will", "with", "you", "your",
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-\.\+]*")


def tokenize(value: str) -> list[str]:
    return [t for t in TOKEN_RE.findall((value or "").lower()) if t not in STOPWORDS and len(t) > 1]


def lexical_vector(value: str) -> dict[str, int]:
    return dict(Counter(tokenize(value)))


# --------------------------------------------------------------------- scoring
def _bm25(query_terms: list[str], chunk: KnowledgeChunk, idf: dict[str, float], avg_len: float) -> float:
    k1, b = 1.5, 0.75
    vector: dict[str, int] = chunk.lexical_vector or {}
    length = max(1, chunk.token_count or sum(vector.values()) or 1)
    score = 0.0
    for term in query_terms:
        freq = vector.get(term, 0)
        if not freq:
            continue
        denom = freq + k1 * (1 - b + b * length / max(avg_len, 1.0))
        score += idf.get(term, 0.0) * (freq * (k1 + 1)) / denom
    return score


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _confidence_weight(confidence: Optional[str]) -> float:
    """A verified chunk outranks an unverified one at equal textual relevance."""
    return {
        "VERIFIED": 1.15,
        "NEEDS_REVIEW": 1.0,
        "OUTDATED": 0.8,
        "CONFLICTING": 0.85,
        "UNVERIFIED": 0.7,
    }.get((confidence or "").upper(), 0.9)


# --------------------------------------------------------------------- search
# ---------------------------------------------------------------------- dialect detection
def _is_postgres(db) -> bool:
    """Whether *this session* is PostgreSQL - not whether the process is configured for it.

    The two normally agree. They do not agree in a test that runs the suite against PostgreSQL
    while pointing one fixture at SQLite, and the old check made that difference a real failure:
    the rebuild took the pgvector branch against a SQLite database and threw "no such column:
    embedding_vec". Asking the connection is also the honest question - the SQL that follows is
    executed there.
    """
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:            # noqa: BLE001 - a detached session is simply not PostgreSQL
        return False


def search(
    db: Session,
    query: str,
    *,
    limit: int = 6,
    entity_types: Optional[Iterable[str]] = None,
    query_embedding: Optional[list[float]] = None,
    dense_weight: Optional[float] = None,
    min_score: Optional[float] = None,
    apply_policy: bool = True,
    academic_year: Optional[Any] = None,
    scope: Optional[scoping.Scope] = None,
) -> list[dict[str, Any]]:
    """Ranked passages for a question.

    ``apply_policy`` is on by default and enforces ``core.verification_policy``: a chunk that
    cannot be presented as verified and current is removed *before* ranking, so an unverified
    record can never be cited, quoted or counted as grounding. The admin tools pass
    ``apply_policy=False`` because staff need to see what is waiting to be verified - that is a
    curation surface, not an answer.
    """
    terms = tokenize(query)
    if not terms:
        return []

    # Embed the query once, here, so every caller gets dense retrieval without having to
    # know it exists. Local fallback means this never needs a key.
    if query_embedding is None and settings.embeddings_enabled:
        try:
            query_embedding = E.embed_query(query)
        except Exception:
            query_embedding = None

    stmt = select(KnowledgeChunk)
    # W6: the corpus is narrowed in SQL, before any scoring and before ``limit``. A passage from
    # a destination the student did not ask about can therefore never be ranked, quoted, or
    # counted as grounding - and an unresolved mandatory scope retrieves nothing at all rather
    # than everything.
    if scope is not None:
        if not scope.ok:
            return []
        scope_predicate = scoping.chunk_clause(db, scope)
        if scope_predicate is not None:
            stmt = stmt.where(scope_predicate)
    if entity_types:
        stmt = stmt.where(KnowledgeChunk.entity_type.in_(list(entity_types)))
    chunks = list(db.scalars(stmt))
    if apply_policy:
        # One conflict query for the whole candidate set, not one per passage. A chunk whose
        # entity has an unresolved verification conflict is withheld, so a disputed figure is
        # never quoted through retrieval.
        conflicts = policy.conflicts_in(db)
        programs: Dict[int, Program] = {}
        if academic_year:
            # A chunk carries no year of its own; its programme's intakes do. Load them once
            # and evaluate the programme, rather than reporting every chunk as YEAR_UNKNOWN.
            program_ids = {c.entity_id for c in chunks if c.entity_type == "program"}
            if program_ids:
                rows = db.execute(
                    select(Program).options(selectinload(Program.intakes))
                    .where(Program.id.in_(program_ids))
                ).scalars().unique()
                programs = {p.id: p for p in rows}
        kept: list[KnowledgeChunk] = []
        for chunk in chunks:
            if academic_year and chunk.entity_type == "program" and chunk.entity_id in programs:
                decision = policy.evaluate_record(db, programs[chunk.entity_id], conflicts=conflicts,
                                                  academic_year=academic_year)
            else:
                decision = policy.evaluate_record(db, chunk, entity_type=chunk.entity_type,
                                                  conflicts=conflicts, academic_year=academic_year)
            if decision.usable:
                kept.append(chunk)
        chunks = kept
    if not chunks:
        return []

    # ---- lexical BM25 scores (always computed: used alone and as the hybrid gate) ----
    total = len(chunks)
    doc_freq: Counter[str] = Counter()
    for chunk in chunks:
        for term in (chunk.lexical_vector or {}):
            if term in terms:
                doc_freq[term] += 1
    idf = {
        t: math.log(1 + (total - doc_freq.get(t, 0) + 0.5) / (doc_freq.get(t, 0) + 0.5))
        for t in terms
    }
    avg_len = sum(max(1, c.token_count or 1) for c in chunks) / max(1, total)
    lexical = {c.id: _bm25(terms, c, idf, avg_len) for c in chunks}

    # ---- dense scores: pgvector first, stored-JSON cosine as the portable fallback ----
    dense: dict[int, float] = {}
    if query_embedding:
        if _is_postgres(db):
            # The savepoint is load-bearing. On PostgreSQL a failed statement aborts the whole
            # transaction: catching the exception is not enough, and the *next* query in the
            # request then fails with "current transaction is aborted". A missing pgvector
            # extension, a column from an unrun migration or a row whose vector has a different
            # dimension would therefore take down every following query in the turn. Inside a
            # nested transaction the failure costs only the dense scores.
            try:
                with db.begin_nested():
                    vector_rows = db.execute(
                        text(
                            "SELECT id, 1 - (embedding_vec <=> CAST(:q AS vector)) AS score "
                            "FROM knowledge_chunks WHERE embedding_vec IS NOT NULL "
                            "ORDER BY embedding_vec <=> CAST(:q AS vector) LIMIT :k"
                        ),
                        {"q": str(query_embedding), "k": max(limit * 5, 25)},
                    ).all()
                dense = {int(r[0]): float(r[1]) for r in vector_rows}
            except Exception:
                dense = {}  # extension or column missing -> lexical only
        if not dense:
            dense = {
                c.id: _cosine(query_embedding, c.embedding or [])
                for c in chunks
                if c.embedding
            }

    allowed = {c.id for c in chunks}
    dense = {k: v for k, v in dense.items() if k in allowed}

    # ---- fusion ---------------------------------------------------------
    # A dense index always returns its k nearest neighbours, however far away they are, so
    # an unrelated query still gets a ranked list back. That is exactly the failure this
    # platform must not have: a weak neighbour presented as an answer is worse than saying
    # nothing. So a chunk survives only with real support - either a lexical term match, or
    # a dense score clearing an absolute floor (which is what lets a purely semantic match,
    # with no shared vocabulary, still win).
    # Admin tuning passes candidate weights so results can be compared side by side
    # without saving anything; everything else uses the configured values.
    floor = float(min_score if min_score is not None else settings.retrieval_min_score)
    weight = float(dense_weight if dense_weight is not None else settings.retrieval_dense_weight)
    candidates = {
        cid
        for cid in set(dense) | set(lexical)
        if lexical.get(cid, 0.0) > 0.0 or dense.get(cid, 0.0) >= floor
    }
    if not candidates:
        return []

    by_id = {c.id: c for c in chunks}
    max_lex = max(lexical.values()) if lexical else 0.0
    ranked: list[tuple[KnowledgeChunk, float]] = []
    for cid in candidates:
        d = dense.get(cid, 0.0)
        lex_raw = lexical.get(cid, 0.0)
        lex = (lex_raw / max_lex) if max_lex > 0 else 0.0
        if dense:
            fused = (weight * d) + (
                (1.0 - weight) * lex
            )
        else:
            fused = lex
        chunk = by_id[cid]
        ranked.append((chunk, fused * _confidence_weight(chunk.confidence)))

    ranked = [p for p in ranked if p[1] > 0]
    ranked.sort(key=lambda p: p[1], reverse=True)
    return [_as_hit(c, s) for c, s in ranked[:limit]]


def _as_hit(chunk: KnowledgeChunk, score: float) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "entity_type": chunk.entity_type,
        "entity_id": chunk.entity_id,
        "slug": chunk.slug,
        "title": chunk.title,
        "content": chunk.content,
        "url_path": chunk.url_path,
        "source_url": chunk.source_url,
        "source_type": chunk.source_type,
        "verified_at": chunk.verified_at.isoformat() if chunk.verified_at else None,
        "confidence": chunk.confidence,
        "provenance": getattr(chunk, "provenance", None),
        "verified": True,       # surviving the policy filter is what makes this true
        "policy_version": policy.POLICY_VERSION,
        "score": round(float(score), 4),
    }


# ------------------------------------------------------------------- indexing
def _provenance_of(obj: Any) -> Optional[str]:
    """DEMO for seed/placeholder records, otherwise None.

    Carried onto the chunk so the verification policy can reject demonstration data even after
    the values have been flattened into a passage: a demo paragraph must never look like an
    official statement just because it reads well.
    """
    haystack = " ".join(str(getattr(obj, f, "") or "") for f in ("verified_by", "source_type")).lower()
    return "DEMO" if any(m in haystack for m in policy.DEMO_MARKERS) else None


def _chunk_for(entity_type: str, obj: Any) -> Optional[dict[str, Any]]:
    """Flatten one record into a retrievable passage. Only published facts go in."""
    if entity_type == "program":
        req = getattr(obj, "requirement", None)
        parts = [
            f"{obj.name} at {obj.university.name if obj.university else ''}.",
            f"Degree level: {obj.degree_level}. Award: {obj.award or 'not published'}.",
            f"Duration: {obj.duration_months or 'not published'} months. Language: {obj.language or 'English'}.",
            f"Annual tuition: {obj.tuition_annual} {obj.currency}." if obj.tuition_annual else "",
            f"Overview: {obj.overview}" if obj.overview else "",
            f"Career outcomes: {obj.career_outcomes}" if getattr(obj, "career_outcomes", None) else "",
        ]
        if req and policy.is_answerable(req):
            parts += [
                f"Minimum CGPA {req.min_cgpa} on a {req.cgpa_scale} scale." if req.min_cgpa else "",
                f"IELTS overall {req.ielts_overall}, minimum band {req.ielts_band_min}." if req.ielts_overall else "",
                f"TOEFL iBT {req.toefl_ibt}." if req.toefl_ibt else "",
                "MOI accepted." if getattr(req, "moi_accepted", False) else "",
                f"Required documents: {', '.join(req.documents)}." if getattr(req, "documents", None) else "",
            ]
        return {
            "title": obj.name,
            "content": " ".join(p for p in parts if p),
            "url_path": f"/programs/{obj.slug}",
            "slug": obj.slug,
            "source_url": obj.source_url,
            "source_type": obj.source_type,
            "verified_at": obj.verified_at,
            "confidence": obj.confidence,
        }

    if entity_type == "university":
        parts = [
            f"{obj.name} is located in {obj.city or ''}, {obj.country.name if obj.country else ''}.",
            f"Institution type: {obj.institution_type}." if obj.institution_type else "",
            f"Established {obj.established_year}." if obj.established_year else "",
            obj.overview or "",
            f"Accreditation: {obj.accreditation}" if obj.accreditation else "",
            f"Official website: {obj.website}" if obj.website else "",
        ]
        return {
            "title": obj.name,
            "content": " ".join(p for p in parts if p),
            "url_path": f"/universities/{obj.country.slug if obj.country else 'world'}/{obj.slug}",
            "slug": obj.slug,
            "source_url": obj.source_url,
            "source_type": obj.source_type,
            "verified_at": obj.verified_at,
            "confidence": obj.confidence,
        }

    if entity_type == "scholarship":
        amount = (
            "full award" if obj.amount_type == "FULL"
            else f"{obj.percentage}% of tuition" if obj.amount_type == "PERCENTAGE"
            else f"{obj.amount_value} {obj.currency}"
        )
        parts = [
            f"{obj.name} offered by {obj.provider}. Value: {amount}.",
            f"Degree levels: {', '.join(obj.degree_levels or [])}." if obj.degree_levels else "",
            obj.eligibility_summary or "",
            f"Minimum CGPA {obj.min_cgpa}." if obj.min_cgpa else "",
            f"Minimum IELTS {obj.min_ielts}." if obj.min_ielts else "",
            f"Deadline {obj.deadline}." if obj.deadline else "",
            f"How to apply: {obj.application_method}" if obj.application_method else "",
        ]
        return {
            "title": obj.name,
            "content": " ".join(p for p in parts if p),
            "url_path": f"/scholarships/{obj.slug}",
            "slug": obj.slug,
            "source_url": obj.source_url,
            "source_type": obj.source_type,
            "verified_at": obj.verified_at,
            "confidence": obj.confidence,
        }

    if entity_type == "country":
        parts = [
            f"Studying in {obj.name}. {obj.tagline or ''}",
            f"Typical tuition {obj.tuition_min}-{obj.tuition_max} {obj.currency} per year." if obj.tuition_min else "",
            f"Living cost {obj.living_cost_min}-{obj.living_cost_max} {obj.currency} per year." if obj.living_cost_min else "",
            f"Visa: {obj.visa_summary}" if obj.visa_summary else "",
            f"Work rights: {obj.work_rights}" if obj.work_rights else "",
            f"Post-study work: {obj.post_study_work}" if obj.post_study_work else "",
            f"Popular subjects: {', '.join(obj.popular_subjects or [])}." if obj.popular_subjects else "",
        ]
        return {
            "title": f"Study in {obj.name}",
            "content": " ".join(p for p in parts if p),
            "url_path": f"/study-destinations/{obj.slug}",
            "slug": obj.slug,
            "source_url": None,
            "source_type": None,
            "verified_at": None,
            "confidence": "VERIFIED",
        }

    if entity_type == "professor":
        parts = [
            f"{obj.full_name}, {obj.title or 'faculty member'} at "
            f"{obj.university.name if obj.university else ''}.",
            f"Research interests: {', '.join(obj.research_interests or [])}." if obj.research_interests else "",
            f"Lab: {obj.lab_name}." if obj.lab_name else "",
            "Currently accepting PhD students." if obj.accepting_phd else "Not listed as accepting PhD students.",
            obj.bio or "",
        ]
        return {
            "title": obj.full_name,
            "content": " ".join(p for p in parts if p),
            "url_path": f"/faculty/{obj.slug}",
            "slug": obj.slug,
            "source_url": obj.source_url,
            "source_type": obj.source_type,
            "verified_at": obj.verified_at,
            "confidence": obj.confidence,
        }
    return None


# Typed registry. The instance union keeps ``obj.id`` resolvable without an Any escape hatch.
SourceInstance = Program | University | Scholarship | Country | Professor
SOURCES: list[tuple[str, type[SourceInstance]]] = [
    ("program", Program),
    ("university", University),
    ("scholarship", Scholarship),
    ("country", Country),
    ("professor", Professor),
]


def rebuild_index(db: Session, embed: bool = True) -> dict[str, Any]:
    """Drop and rebuild the retrieval corpus. Safe to run repeatedly."""
    db.query(KnowledgeChunk).delete()
    counts: dict[str, Any] = {}

    for entity_type, model in SOURCES:
        rows = cast(list[SourceInstance], list(db.scalars(select(model))))
        made = 0
        for obj in rows:
            if entity_type == "program" and not getattr(obj, "is_published", True):
                continue
            payload = _chunk_for(entity_type, obj)
            if not payload or not payload["content"].strip():
                continue
            payload["provenance"] = _provenance_of(obj)
            vector = lexical_vector(f"{payload['title']} {payload['content']}")
            chunk = KnowledgeChunk(
                entity_type=entity_type,
                entity_id=obj.id,
                slug=payload["slug"],
                title=payload["title"],
                content=payload["content"][:4000],
                url_path=payload["url_path"],
                source_url=payload["source_url"],
                source_type=payload["source_type"],
                verified_at=payload["verified_at"],
                confidence=payload["confidence"],
                provenance=payload.get("provenance"),
                token_count=sum(vector.values()),
                lexical_vector=vector,
                embedding=[],
            )
            db.add(chunk)
            made += 1
        counts[entity_type] = made

    db.flush()

    # ---- dense vectors --------------------------------------------------
    # Always populated: with no API key this uses the local hashing vectoriser, so the
    # pgvector path is exercised identically. Batched to keep hosted calls cheap.
    written = 0
    if embed:
        pending = list(db.scalars(select(KnowledgeChunk)))

        # Fit the local semantic model on this corpus before embedding anything. It has
        # to happen here and only here: queries transform against a frozen model, and a
        # refit between indexing and querying would put stored vectors and query vectors
        # in different spaces, making every similarity score meaningless. Skipped
        # entirely when a hosted provider is configured, since then it is unused.
        if not E.hosted_available():
            corpus = [f"{c.title}\n{c.content}" for c in pending]
            fingerprint = local_semantic.fit(corpus, E.dim())
            if fingerprint:
                log.info("local semantic model fitted for this corpus: %s", fingerprint)

        batch_size = 64
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            vectors = E.embed_texts([f"{c.title}\n{c.content}" for c in batch])
            model_name = E.active_model_name()
            vector_width = E.dim()
            for chunk_row, embedding in zip(batch, vectors, strict=False):
                if not embedding:
                    continue
                chunk_row.embedding = embedding
                # Stamp what produced it, so a later provider change is visible as drift
                # rather than silently mixing incomparable vectors in one index.
                chunk_row.embedding_model = model_name
                chunk_row.embedding_dim = vector_width
                written += 1
        db.flush()
        if _is_postgres(db) and written:
            # Mirror into the pgvector column the HNSW index is built on.
            for chunk_row in pending:
                if not chunk_row.embedding:
                    continue
                db.execute(
                    text("UPDATE knowledge_chunks SET embedding_vec = CAST(:v AS vector) WHERE id = :i"),
                    {"v": str(list(chunk_row.embedding)), "i": chunk_row.id},
                )

    db.commit()
    counts["embedded"] = written
    counts["embedding_backend"] = E.active_backend()
    counts["embedding_model"] = E.active_model_name()
    counts["vector_store"] = "pgvector" if _is_postgres(db) else "json+python-cosine"
    counts["total"] = sum(v for v in counts.values() if isinstance(v, int)) - written
    counts["indexed_at"] = int(datetime.utcnow().timestamp())
    return counts


def embedding_drift(db) -> Dict[str, Any]:
    """Report chunks whose vectors were produced by something other than the current model.

    Switching embedding provider is a one-line settings change, but it does not retrofit the
    117 vectors already stored - and cosine similarity between two different models' vectors
    is meaningless rather than merely worse. This is what the admin screen reads to decide
    whether to nag for a reindex.
    """
    current = E.active_model_name()
    rows = db.execute(
        select(KnowledgeChunk.embedding_model, func.count())
        .group_by(KnowledgeChunk.embedding_model)
    ).all()
    by_model = {(m or "unknown"): int(n) for m, n in rows}
    total = sum(by_model.values())
    stale = sum(n for m, n in by_model.items() if m != current)
    return {
        "current_model": current,
        "current_dim": E.dim(),
        "total_chunks": total,
        "stale_chunks": stale,
        "in_sync": stale == 0,
        "by_model": by_model,
        "reindex_required": stale > 0,
    }
