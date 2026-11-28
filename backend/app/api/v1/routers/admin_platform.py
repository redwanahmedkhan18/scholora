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
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import embeddings as E
from app.core import llm, runtime
from app.core.database import get_db
from app.core.deps import require_role
from app.models.crm import AuditLog, DataVerification, ImportBatch, ImportRow, User
from app.services import email as email_service
from app.services import email_templates, importer, readiness, retrieval, source_verification

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
        raise HTTPException(400, str(exc)) from exc
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
                     user: User = Depends(require_role("ADMIN")),
                     body: Optional[Dict[str, Any]] = Body(default=None)):
    """Prove a credential actually works, from the server, right now.

    A saved token that is wrong is worse than no token, because the failure shows up later
    as a silently undelivered message. Each test performs the cheapest real call the vendor
    offers and reports the verbatim error on failure.

    ``smtp`` is the exception that needs a body: an empty ``{}`` proves the connection and the
    credentials, and ``{"to": "<your own address>"}`` also sends one message. The recipient
    must be the caller's own account, so this can never be used to mail an arbitrary address.
    """
    from app.core.config import settings  # dynamic view, DB overlay on
    target = target.lower()
    _audit(db, user, "TEST_INTEGRATION", target=target)
    db.commit()

    try:
        if target == "smtp":
            state = email_service.status()
            if not state["configured"]:
                return _fail("Email (SMTP)", state["reason"] or "SMTP is not configured.",
                             status=state)
            if not body or not body.get("to"):
                probe = email_service.verify_connection()
                if probe.ok:
                    return _ok("Email (SMTP)",
                               f"Connected to {state['host']}:{state['port']} over "
                               f"{state['security']} as {state['username']}. "
                               "Send a test message to confirm delivery end to end.",
                               status=state)
                return _fail("Email (SMTP)", probe.error or "connection failed", status=state)

            requested = str(body.get("to") or "").strip().lower()
            if requested != (user.email or "").strip().lower():
                # No open relay: the owner verifies their own mailbox, nobody else's.
                return _fail("Email (SMTP)",
                             "A test message can only be sent to your own account address "
                             f"({user.email}).")
            rendered = email_templates.test_message(to=user.email, triggered_by=user.email)
            sent = email_service.send(user.email, rendered.subject, rendered.text, rendered.html)
            if sent.ok:
                return _ok("Email (SMTP)",
                           f"The mail server accepted the message for "
                           f"{email_service.mask_email(user.email)} on attempt {sent.attempts}. "
                           "Check that mailbox (and its spam folder) to confirm the last mile.",
                           status=state, attempts=sent.attempts)
            return _fail("Email (SMTP)", sent.error or "send failed",
                         status=state, attempts=sent.attempts)

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
                       f"({', '.join(str(r['label']) for r in working)}).",
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
            if celery_app is None:
                raise RuntimeError("Celery broker is not configured")
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
        # Curation surface: a retrieval tuner must see every indexed passage, including ones the
        # verification policy withholds from students. Otherwise the sample is empty on a corpus
        # that is still being verified and the weights cannot be tuned at all.
        hits = retrieval.search(db, body.query, limit=max(1, min(body.limit, 20)),
                                dense_weight=cfg["dense_weight"], min_score=cfg["min_score"],
                                apply_policy=False)
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
            # Same curation rule as the comparison above: sweep the raw index, not the
            # student-facing subset.
            hits = retrieval.search(db, probe, limit=5, dense_weight=weight,
                                    min_score=float(settings.retrieval_min_score),
                                    apply_policy=False)
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
        raise HTTPException(400, str(exc)) from exc


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
        raise HTTPException(400, f"could not parse the file: {exc}") from exc
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
        raise HTTPException(400, str(exc)) from exc

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
        raise HTTPException(400, str(exc)) from exc
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
    #: The state the reviewer was shown. When present and stale, the decision is refused (409)
    #: instead of overwriting a decision that arrived in between.
    expected_status: Optional[str] = None


class WithdrawIn(BaseModel):
    """Taking a claim out of the register (F-2). A reason is required; nothing is deleted."""

    reason: str = ""
    expected_status: Optional[str] = None


@router.post("/verification/{verification_id}/resolve")
def verification_resolve(verification_id: int, body: ResolveIn,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role("DATA_MANAGER"))):
    """Record one field decision, and derive the entity's state from its whole register.

    Replaces a handler that accepted any confidence state, stamped the *entity* VERIFIED from a
    single field, and let the uploader approve its own import. The rules live in
    ``services.review`` so every caller inherits them.
    """
    from app.services import review

    record = db.get(DataVerification, verification_id)
    if not record:
        raise HTTPException(404, "verification record not found")

    try:
        if (body.status or "").upper() == "VERIFIED":
            review.assert_review_authority(db, record, user.email)
        result = review.resolve_field(db, record, status=body.status,
                                      actor_email=user.email, note=body.note,
                                      expected_status=body.expected_status)
    except review.ReviewError as exc:
        db.rollback()
        raise HTTPException(exc.code, str(exc)) from None

    _audit(db, user, "RESOLVE_VERIFICATION", entity_type=record.entity_type,
           entity_id=record.entity_id, field=record.field_name,
           status=result["field_status"], note=body.note or None)
    db.commit()
    return result


@router.post("/verification/{verification_id}/withdraw")
def verification_withdraw(verification_id: int, body: WithdrawIn,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role("DATA_MANAGER"))):
    """Withdraw a field's evidence without deleting its history (F-2).

    This is the replacement for the generic ``DELETE /admin/verifications/{id}``, which no longer
    exists. The row keeps its recorded value and source; its status becomes ``WITHDRAWN``; the
    entity is recomputed in the same transaction and can only be withheld, never promoted. The
    actor and the reason are recorded twice: on the row's audit entry, and in this response.
    """
    from app.services import review

    record = db.get(DataVerification, verification_id)
    if not record:
        raise HTTPException(404, "verification record not found")

    try:
        result = review.withdraw_field(db, record, actor_email=user.email,
                                       note=body.reason, expected_status=body.expected_status)
    except review.ReviewError as exc:
        db.rollback()
        raise HTTPException(exc.code, str(exc)) from None

    _audit(db, user, "WITHDRAW_VERIFICATION", entity_type=record.entity_type,
           entity_id=record.entity_id, field=record.field_name,
           status=result["field_status"], note=body.reason.strip() or None)
    db.commit()
    return result


@router.get("/verification/changes")
def verification_changes(status: str = "PENDING", classification: Optional[str] = None,
                         limit: int = 50, offset: int = 0,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role("DATA_MANAGER"))):
    """Material catalogue changes waiting for a decision, newest first and bounded."""
    from app.services import catalogue_changes

    page = catalogue_changes.queue(db, status=status, limit=limit, offset=offset,
                                   classification=classification)
    return {"total": page["total"], "limit": page["limit"], "offset": page["offset"],
            "items": [{
                "id": c.id, "entity_type": c.entity_type, "entity_id": c.entity_id,
                "field_name": c.field_name, "previous_value": c.previous_value,
                "new_value": c.new_value, "classification": c.classification,
                "status": c.status, "version": c.version, "actor": c.actor,
                "source_url": c.source_url, "source_type": c.source_type,
                "retrieved_at": c.retrieved_at.isoformat() if c.retrieved_at else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            } for c in page["items"]]}


class ChangeDecisionIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/verification/changes/{change_id}/decide")
def verification_change_decide(change_id: int, body: ChangeDecisionIn,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_role("DATA_MANAGER"))):
    from app.services import catalogue_changes

    change = db.get(catalogue_changes.CatalogueChange, change_id)
    if not change:
        raise HTTPException(404, "change not found")
    status = (body.status or "").upper()
    if status not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "status must be APPROVED or REJECTED")
    if status == "REJECTED" and not (body.note or "").strip():
        raise HTTPException(400, "a note is required when a change is rejected")
    try:
        catalogue_changes.decide(db, change, status=status, actor=user.email, note=body.note)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None

    # An approved change is a statement about the evidence, not about the value: the stored
    # record keeps NEEDS_REVIEW until a reviewer confirms the field itself, so this action can
    # never promote a figure straight to "official".
    _audit(db, user, "DECIDE_CATALOGUE_CHANGE", entity_type=change.entity_type,
           entity_id=change.entity_id, field=change.field_name, status=status,
           note=body.note or None)
    db.commit()
    return {"id": change.id, "status": change.status, "decided_by": change.decided_by}


@router.get("/verification/entity/{entity_type}/{entity_id}")
def verification_entity(entity_type: str, entity_id: int,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role("DATA_MANAGER"))):
    """Review detail: stored values, field evidence, pending changes and history."""
    from app.services import review

    try:
        return review.entity_review_detail(db, entity_type, entity_id)
    except review.ReviewError as exc:
        raise HTTPException(exc.code, str(exc)) from None


@router.get("/catalogue/integrity")
def catalogue_integrity(sample: int = 5, db: Session = Depends(get_db),
                        user: User = Depends(require_role("DATA_MANAGER"))):
    """Where the catalogue is not yet trustworthy. A report; it repairs nothing."""
    from app.services import catalogue_integrity as integrity

    return integrity.audit(db, sample=sample)


# ----------------------------------------------------------- source-level verification
class VerifySourceIn(BaseModel):
    source_url: str
    #: The reviewer's own name. Required, and it must match the signed-in account: the decision is
    #: recorded against that account, not against whatever text arrives here (F-2).
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
    Mark every claim citing one page as VERIFIED, under the signed-in reviewer.

    The verifier is recorded against the acting account and defaults to nobody: the entire
    provenance model depends on a human being answerable for that word, so this cannot be reached
    from a scheduled job - and a caller cannot name somebody else as the person who decided (F-2).
    """
    try:
        result = source_verification.verify_source(
            db, source_url=body.source_url, verifier=body.verifier,
            actor_email=actor.email, actor_name=actor.full_name,
            fields=body.field_ids, note=body.note)
    except ValueError as exc:
        db.rollback()
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
