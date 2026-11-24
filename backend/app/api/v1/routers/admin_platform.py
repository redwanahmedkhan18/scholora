"""
Admin control surface for the four things that used to require an engineer:

* **Credentials** - WhatsApp, Meta, and the three LLM vendors, set from the UI, encrypted
  at rest, with a live "test connection" for each.
* **Embeddings** - choose the provider, see how many stored vectors are stale, reindex.
* **Retrieval tuning** - try candidate weights against real queries side by side with the
  current ones before committing them.
* **Data import** - stage a CSV/JSON load, review the field-level diff, resolve conflicts,
  commit with provenance.

Everything here is ADMIN-only and audit logged. Secrets are never echoed back.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import embeddings as E
from app.core import llm, runtime
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import current_user, require_role
from app.models.crm import AuditLog, DataVerification, ImportBatch, ImportRow, User
from app.services import importer, readiness, retrieval, source_verification

router = APIRouter()


def _audit(db: Session, user: User, action: str, **extra: Any) -> None:
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action=action,
                    entity_type=extra.pop("entity_type", "platform"),
                    entity_id=extra.pop("entity_id", None),
                    detail=extra or {}))


# ===========================================================================  configuration
@router.get("/config")
def read_config(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    """Every runtime-editable setting, grouped. Secret values come back masked."""
    items = runtime.describe(db)
    groups: Dict[str, Dict[str, Any]] = {}
    for item in items:
        bucket = groups.setdefault(item["group"], {
            "key": item["group"], "label": item["group_label"], "items": []})
        bucket["items"].append(item)
    return {
        "groups": list(groups.values()),
        "configured": sum(1 for i in items if i["has_value"]),
        "total": len(items),
        "note": "Values set here are stored in the database and override the environment. "
                "Clearing a field hands the key back to the environment variable.",
    }


class ConfigIn(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


@router.put("/config")
def write_config(body: ConfigIn, db: Session = Depends(get_db),
                 user: User = Depends(require_role("ADMIN"))):
    try:
        # commit=False so the credential change and its audit entry land together; a
        # security-relevant change that is not logged is worse than one that fails.
        result = runtime.apply(db, body.values, actor=user.email, commit=False)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    # Log the keys touched, never the values.
    _audit(db, user, "UPDATE_RUNTIME_CONFIG",
           changed=result["changed"], cleared=result["cleared"])
    db.commit()
    runtime.invalidate()
    return result


# ---------------------------------------------------------------- connection tests
def _ok(label: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": True, "label": label, "detail": detail, **extra}


def _fail(label: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "label": label, "detail": detail[:400], **extra}


@router.post("/config/test/{target}")
def test_integration(target: str, db: Session = Depends(get_db),
                     user: User = Depends(require_role("ADMIN"))):
    """Prove a credential actually works, from the server, right now.

    A saved token that is wrong is worse than no token, because the failure shows up later
    as a silently undelivered message. Each test performs the cheapest real call the vendor
    offers and reports the verbatim error on failure.
    """
    from app.core.config import settings                       # dynamic view, DB overlay on
    target = target.lower()
    _audit(db, user, "TEST_INTEGRATION", target=target)
    db.commit()

    try:
        if target == "whatsapp":
            token = (settings.whatsapp_access_token or "").strip()
            phone_id = (settings.whatsapp_phone_number_id or "").strip()
            if not token or not phone_id:
                return _fail("WhatsApp Business", "Access token and phone number ID are both required.")
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{settings.meta_graph_base_url}/{phone_id}",
                               params={"fields": "verified_name,display_phone_number,quality_rating"},
                               headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                d = r.json()
                return _ok("WhatsApp Business",
                           f"Connected to {d.get('verified_name') or 'number'} "
                           f"({d.get('display_phone_number') or phone_id}).", payload=d)
            return _fail("WhatsApp Business", f"HTTP {r.status_code}: {r.text}")

        if target == "meta_capi":
            token = (settings.meta_capi_access_token or "").strip()
            pixel = (settings.meta_pixel_id or "").strip()
            if not token or not pixel:
                return _fail("Meta Conversions API", "Pixel ID and Conversions API token are both required.")
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{settings.meta_graph_base_url}/{pixel}",
                               params={"fields": "name,id"},
                               headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                return _ok("Meta Conversions API", f"Pixel reachable: {r.json().get('name') or pixel}.")
            return _fail("Meta Conversions API", f"HTTP {r.status_code}: {r.text}")

        if target == "facebook_page":
            token = (settings.facebook_page_access_token or "").strip()
            if not token:
                return _fail("Facebook page", "No page access token set.")
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{settings.meta_graph_base_url}/me",
                               params={"fields": "name,link,fan_count"},
                               headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                d = r.json()
                return _ok("Facebook page", f"Linked to {d.get('name')}.", payload=d)
            return _fail("Facebook page", f"HTTP {r.status_code}: {r.text}")

        if target in llm.PROVIDER_META:
            result = llm.ping(target)
            label = llm.PROVIDER_META[target].get("label", target)
            if result.get("ok"):
                return _ok(label, result.get("detail") or f"{result.get('model', '')} responded.",
                           payload=result)
            return _fail(label, str(result.get("error") or "no response"), payload=result)

        if target == "embeddings":
            backend = E.active_backend()
            vector = E.embed_query("scholarship eligibility for a Bangladeshi student")
            if not vector:
                return _fail("Embeddings", "The provider returned no vector.")
            return _ok("Embeddings",
                       f"{E.active_model_name()} returned a {len(vector)}-dimension vector.",
                       backend=backend, dim=len(vector),
                       matches_index=len(vector) == E.dim())

        if target == "llm":
            # Until now an LLM key could be pasted, saved and never exercised - the first
            # proof it was wrong would be a student getting silence from the assistant.
            configured = llm.configured_providers()
            if not configured:
                return _fail("Assistant model",
                             "No provider is configured. Add an xAI, Mistral or Gemini key.")
            results = []
            for provider in configured:
                try:
                    reply = llm._post(provider,
                                      [{"role": "user", "content": "Reply with the word OK."}],
                                      max_tokens=8, temperature=0.0)
                    results.append({"provider": provider.key, "label": provider.label,
                                    "ok": bool(reply), "detail": (reply or "")[:80]})
                except Exception as exc:                              # noqa: BLE001
                    results.append({"provider": provider.key, "label": provider.label,
                                    "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:160]})
            working = [r for r in results if r["ok"]]
            if not working:
                return _fail("Assistant model",
                             "Every configured provider refused: "
                             + "; ".join(f"{r['label']} - {r['detail']}" for r in results),
                             providers=results)
            return _ok("Assistant model",
                       f"{len(working)} of {len(results)} configured providers responded "
                       f"({', '.join(r['label'] for r in working)}).",
                       providers=results)

        raise HTTPException(404, f"unknown test target '{target}'")
    except HTTPException:
        raise
    except Exception as exc:                                          # noqa: BLE001
        return _fail(target, f"{type(exc).__name__}: {exc}")


# ===========================================================================  embeddings
@router.get("/embeddings/status")
def embeddings_status(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    drift = retrieval.embedding_drift(db)
    return {
        "health": E.health(),
        "drift": drift,
        "providers": [
            {"key": p.key, "label": p.label, "model": p.model, "configured": p.configured,
             "priority": i + 1}
            for i, p in enumerate(E.providers())
        ],
        "order": E.provider_order(),
        "advice": (
            "Stored vectors were produced by a different model. Search quality is unreliable "
            "until you reindex." if drift["reindex_required"] else
            "Every stored vector matches the active model."
        ),
    }


@router.post("/embeddings/reindex")
def embeddings_reindex(background: bool = Query(default=True),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_role("ADMIN"))):
    """Rebuild the corpus. Queued to Celery when a broker is reachable, inline otherwise."""
    _audit(db, user, "REINDEX_EMBEDDINGS", background=background)
    db.commit()
    if background:
        try:
            from app.services.tasks import celery_app
            async_result = celery_app.send_task("scholara.reindex_knowledge")
            return {"queued": True, "task_id": str(async_result.id),
                    "detail": "Reindex queued. Progress appears in Operations > Jobs."}
        except Exception as exc:                                      # noqa: BLE001
            return {"queued": False, "detail": f"Broker unavailable ({exc}); run it inline instead."}
    result = retrieval.rebuild_index(db)
    return {"queued": False, "result": result, "drift": retrieval.embedding_drift(db)}


# ===========================================================================  retrieval tuning
class TuneIn(BaseModel):
    query: str
    dense_weight: Optional[float] = None
    min_score: Optional[float] = None
    limit: int = 8


@router.post("/retrieval/preview")
def retrieval_preview(body: TuneIn, db: Session = Depends(get_db),
                      user: User = Depends(require_role("ADMIN"))):
    """Run one query under the current weights and under candidate weights, side by side.

    The defaults (0.65 / 0.10) were calibrated against the local hashing vectoriser on demo
    content. A hosted embedding model has a different score distribution, so the only honest
    way to retune is to look at real results - not to guess a number.
    """
    from app.core.config import settings
    if not (body.query or "").strip():
        raise HTTPException(400, "query is required")

    current = {"dense_weight": float(settings.retrieval_dense_weight),
               "min_score": float(settings.retrieval_min_score)}
    candidate = {"dense_weight": float(body.dense_weight if body.dense_weight is not None
                                       else current["dense_weight"]),
                 "min_score": float(body.min_score if body.min_score is not None
                                    else current["min_score"])}
    for name, cfg in (("current", current), ("candidate", candidate)):
        if not 0.0 <= cfg["dense_weight"] <= 1.0 or not 0.0 <= cfg["min_score"] <= 1.0:
            raise HTTPException(400, f"{name} weights must be between 0 and 1")

    def run(cfg: Dict[str, float]) -> List[Dict[str, Any]]:
        hits = retrieval.search(db, body.query, limit=max(1, min(body.limit, 20)),
                                dense_weight=cfg["dense_weight"], min_score=cfg["min_score"])
        return [{
            "rank": i + 1, "title": h.get("title"), "entity_type": h.get("entity_type"),
            "url_path": h.get("url_path"), "score": round(float(h.get("score") or 0), 4),
            "dense": round(float(h.get("dense") or 0), 4),
            "lexical": round(float(h.get("lexical") or 0), 4),
            "confidence": h.get("confidence"),
        } for i, h in enumerate(hits)]

    before, after = run(current), run(candidate)
    before_ids = [r["title"] for r in before]
    after_ids = [r["title"] for r in after]
    moved = [t for i, t in enumerate(after_ids)
             if i >= len(before_ids) or before_ids[i] != t]
    return {
        "query": body.query,
        "current": {"weights": current, "results": before},
        "candidate": {"weights": candidate, "results": after},
        "changed": before_ids != after_ids,
        "entered": [t for t in after_ids if t not in before_ids],
        "left": [t for t in before_ids if t not in after_ids],
        "reordered": moved,
        "embedding_model": E.active_model_name(),
    }


@router.post("/retrieval/sweep")
def retrieval_sweep(queries: List[str] = Body(..., embed=True),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_role("ADMIN"))):
    """Score a grid of dense weights across several real queries.

    Reports, for each weight, how many queries returned anything at all and the mean score of
    the top hit. It deliberately does not declare a winner: without labelled relevance
    judgements that would be a fabricated metric, which is exactly what this platform refuses
    to do elsewhere.
    """
    probes = [q.strip() for q in queries if q and q.strip()][:12]
    if not probes:
        raise HTTPException(400, "at least one query is required")

    from app.core.config import settings
    grid = [round(x / 10, 1) for x in range(0, 11)]
    rows = []
    for weight in grid:
        covered, tops = 0, []
        for probe in probes:
            hits = retrieval.search(db, probe, limit=5, dense_weight=weight,
                                    min_score=float(settings.retrieval_min_score))
            if hits:
                covered += 1
                tops.append(float(hits[0].get("score") or 0))
        rows.append({
            "dense_weight": weight,
            "queries_with_results": covered,
            "coverage_pct": round(covered / len(probes) * 100, 1),
            "mean_top_score": round(sum(tops) / len(tops), 4) if tops else 0.0,
        })
    return {
        "queries": probes,
        "current_dense_weight": float(settings.retrieval_dense_weight),
        "grid": rows,
        "embedding_model": E.active_model_name(),
        "caveat": "Coverage and mean score are diagnostics, not relevance. Read the "
                  "side-by-side preview before changing the weight.",
    }


# ===========================================================================  data import
@router.get("/import/schemas")
def import_schemas(user: User = Depends(require_role("DATA_MANAGER"))):
    return {"entity_types": importer.entity_types(),
            "source_types": list(__import__("app.models.base", fromlist=["x"]).SOURCE_TIERS),
            "confidence_states": list(__import__("app.models.base", fromlist=["x"]).CONFIDENCE_STATES)}


@router.get("/import/template/{entity_type}")
def import_template(entity_type: str, user: User = Depends(require_role("DATA_MANAGER"))):
    try:
        return {"entity_type": entity_type, "csv": importer.template_csv(entity_type)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class StageIn(BaseModel):
    entity_type: str
    content: str
    filename: str = ""
    source_url: str = ""
    source_type: str = "TIER_1_OFFICIAL_UNIVERSITY"
    notes: str = ""


@router.post("/import/stage")
def import_stage(body: StageIn, db: Session = Depends(get_db),
                 user: User = Depends(require_role("DATA_MANAGER"))):
    """Parse and diff an upload. Writes nothing to live tables."""
    try:
        rows = importer.parse_payload(body.content, body.filename)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"could not parse the file: {exc}")
    if not rows:
        raise HTTPException(400, "the file contained no rows")
    if len(rows) > 2000:
        raise HTTPException(400, "split files larger than 2000 rows")

    try:
        batch = importer.stage(db, entity_type=body.entity_type, rows=rows,
                               filename=body.filename, source_url=body.source_url,
                               source_type=body.source_type, notes=body.notes,
                               actor=user.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    _audit(db, user, "STAGE_IMPORT", entity_type="import_batch", entity_id=batch.id,
           reference=batch.reference, rows=batch.row_count)
    db.commit()
    return _batch_payload(batch, include_rows=True)


@router.get("/import/batches")
def import_batches(status: Optional[str] = None, limit: int = Query(default=30, le=100),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role("DATA_MANAGER"))):
    stmt = select(ImportBatch).order_by(desc(ImportBatch.created_at))
    if status:
        stmt = stmt.where(ImportBatch.status == status.upper())
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return {"total": db.scalar(select(func.count()).select_from(ImportBatch)) or 0,
            "items": [_batch_payload(b, include_rows=False) for b in rows]}


@router.get("/import/batches/{batch_id}")
def import_batch_detail(batch_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_role("DATA_MANAGER"))):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    return _batch_payload(batch, include_rows=True)


class DecisionIn(BaseModel):
    decisions: Dict[int, str] = Field(default_factory=dict)   # row id -> APPROVE|SKIP


@router.post("/import/batches/{batch_id}/decide")
def import_decide(batch_id: int, body: DecisionIn, db: Session = Depends(get_db),
                  user: User = Depends(require_role("DATA_MANAGER"))):
    """Accept or reject individual rows - this is how a conflict gets resolved."""
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    if batch.status != "STAGED":
        raise HTTPException(400, f"batch is {batch.status}")

    changed = 0
    for row_id, decision in body.decisions.items():
        decision = str(decision).upper()
        if decision not in {"APPROVE", "SKIP"}:
            raise HTTPException(400, "decision must be APPROVE or SKIP")
        row = db.get(ImportRow, int(row_id))
        if row is None or row.batch_id != batch.id:
            continue
        if row.errors:
            raise HTTPException(400, f"line {row.line_number} has validation errors and cannot be approved")
        row.decision = decision
        changed += 1
    db.commit()
    return {"updated": changed, "batch": _batch_payload(batch, include_rows=True)}


@router.post("/import/batches/{batch_id}/commit")
def import_commit(batch_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_role("DATA_MANAGER"))):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    try:
        result = importer.commit_batch(db, batch, actor=user.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(db, user, "COMMIT_IMPORT", entity_type="import_batch", entity_id=batch.id,
           reference=batch.reference, created=result["created"], updated=result["updated"])
    db.commit()
    return {**result, "batch": _batch_payload(batch, include_rows=False)}


@router.post("/import/batches/{batch_id}/discard")
def import_discard(batch_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_role("DATA_MANAGER"))):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    if batch.status == "COMMITTED":
        raise HTTPException(400, "a committed batch cannot be discarded")
    batch.status = "DISCARDED"
    _audit(db, user, "DISCARD_IMPORT", entity_type="import_batch", entity_id=batch.id)
    db.commit()
    return _batch_payload(batch, include_rows=False)


def _batch_payload(batch: ImportBatch, include_rows: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": batch.id, "reference": batch.reference, "entity_type": batch.entity_type,
        "filename": batch.filename, "source_url": batch.source_url,
        "source_type": batch.source_type, "notes": batch.notes, "status": batch.status,
        "row_count": batch.row_count, "created_rows": batch.created_rows,
        "updated_rows": batch.updated_rows, "conflict_rows": batch.conflict_rows,
        "skipped_rows": batch.skipped_rows, "uploaded_by": batch.uploaded_by,
        "committed_by": batch.committed_by, "committed_at": S._dt(batch.committed_at),
        "created_at": S._dt(batch.created_at),
    }
    if include_rows:
        out["rows"] = [{
            "id": r.id, "line_number": r.line_number, "natural_key": r.natural_key,
            "action": r.action, "target_id": r.target_id, "decision": r.decision,
            "applied": r.applied, "errors": r.errors or [], "message": r.message,
            "diff": r.diff or {},
        } for r in sorted(batch.rows, key=lambda r: r.line_number)]
    return out


# ===========================================================================  verification queue
@router.get("/verification/queue")
def verification_queue(status: Optional[str] = None, entity_type: Optional[str] = None,
                       limit: int = Query(default=50, le=200),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_role("DATA_MANAGER"))):
    """Field-level provenance records needing attention, newest first."""
    stmt = select(DataVerification).order_by(desc(DataVerification.created_at))
    if status:
        stmt = stmt.where(DataVerification.status == status.upper())
    if entity_type:
        stmt = stmt.where(DataVerification.entity_type == entity_type)
    rows = db.execute(stmt.limit(limit)).scalars().all()

    counts = dict(db.execute(
        select(DataVerification.status, func.count()).group_by(DataVerification.status)
    ).all())
    return {
        "counts": {k: int(v) for k, v in counts.items()},
        "total": sum(int(v) for v in counts.values()),
        "items": [{
            "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
            "field_name": r.field_name, "field_value": r.field_value,
            "source_url": r.source_url, "source_type": r.source_type,
            "status": r.status, "verified_by": r.verified_by,
            "retrieved_at": S._dt(r.retrieved_at), "verified_at": S._dt(r.verified_at),
            "review_due_at": S._dt(r.review_due_at),
            "conflict_with": json.loads(r.conflict_with) if r.conflict_with else None,
        } for r in rows],
    }


class ResolveIn(BaseModel):
    status: str
    note: str = ""


@router.post("/verification/{verification_id}/resolve")
def verification_resolve(verification_id: int, body: ResolveIn,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role("DATA_MANAGER"))):
    from app.models.base import CONFIDENCE_STATES
    record = db.get(DataVerification, verification_id)
    if not record:
        raise HTTPException(404, "verification record not found")
    new_status = body.status.upper()
    if new_status not in CONFIDENCE_STATES:
        raise HTTPException(400, f"status must be one of {', '.join(CONFIDENCE_STATES)}")

    record.status = new_status
    if new_status == "VERIFIED":
        record.verified_at = datetime.now(timezone.utc)
        record.verified_by = user.email

    # Keep the entity's own confidence column honest when a field is confirmed.
    target_model = {"country": "Country", "university": "University",
                    "program": "Program", "scholarship": "Scholarship"}.get(record.entity_type)
    if target_model:
        from app.models import education as edu
        model = getattr(edu, target_model, None)
        if model is not None:
            entity = db.get(model, record.entity_id)
            if entity is not None and hasattr(entity, "confidence"):
                entity.confidence = new_status
                if new_status == "VERIFIED":
                    entity.verified_at = datetime.now(timezone.utc)
                    entity.verified_by = user.email

    _audit(db, user, "RESOLVE_VERIFICATION", entity_type=record.entity_type,
           entity_id=record.entity_id, field=record.field_name, status=new_status,
           note=body.note or None)
    db.commit()
    return {"id": record.id, "status": record.status, "verified_by": record.verified_by}


# ----------------------------------------------------------- source-level verification
class VerifySourceIn(BaseModel):
    source_url: str
    verifier: str
    field_ids: Optional[List[int]] = None
    note: str = ""


@router.get("/verification/sources")
def verification_sources(state: Optional[str] = None, limit: int = 100,
                         db: Session = Depends(get_db),
                         _: User = Depends(require_role("DATA_MANAGER"))):
    """
    The backlog grouped by the page a verifier would actually open.

    Field-by-field the queue runs to thousands of rows across a few dozen pages, which
    nobody will ever work through. One university fee page typically carries 70-100 claims
    at once, so grouping turns the job from impossible into an afternoon.
    """
    rows = source_verification.source_queue(db, state=state, limit=limit)
    return {
        "total": len(rows),
        "states": {s: sum(1 for r in rows if r["link_state"] == s)
                   for s in source_verification.STATES},
        "legend": {
            "OK": "the page answered normally",
            "BROKEN": "the page is genuinely gone (404/410) - the citation needs replacing",
            "BLOCKED": "the site refused an automated request; the citation may be fine, open it by hand",
            "UNREACHABLE": "the check itself could not run - this says nothing about the page",
            "UNCHECKED": "never checked",
        },
        "items": rows,
    }


@router.post("/verification/sources/check")
def verification_sources_check(limit: int = 50, only_unchecked: bool = False,
                               db: Session = Depends(get_db),
                               _: User = Depends(require_role("DATA_MANAGER"))):
    """Reachability only. This never changes a record's confidence - a machine cannot
    confirm that a page says what we claim it says."""
    return source_verification.sweep(db, limit=limit, only_unchecked=only_unchecked)


@router.post("/verification/sources/verify")
def verification_sources_verify(body: VerifySourceIn, db: Session = Depends(get_db),
                                actor: User = Depends(require_role("DATA_MANAGER"))):
    """
    Mark every claim citing one page as VERIFIED, under a named person.

    The verifier is recorded verbatim and defaults to nobody: the entire provenance model
    depends on a human being answerable for that word, so this cannot be reached from a
    scheduled job.
    """
    try:
        result = source_verification.verify_source(
            db, source_url=body.source_url,
            verifier=body.verifier or actor.full_name or actor.email,
            fields=body.field_ids, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _audit(db, actor, "VERIFY_SOURCE", entity_type="data_verification",
           source_url=body.source_url, claims=result["verified"],
           verified_by=result["verified_by"])
    db.commit()
    return result


# -------------------------------------------------------------------- go-live readiness
@router.get("/readiness")
def go_live_readiness(db: Session = Depends(get_db),
                      _: User = Depends(require_role("ADMIN"))):
    """
    What still needs an account the consultancy has to open, and exactly where to get it.

    Returns no secrets - only whether each setting has a value.
    """
    return readiness.report(db)
