"""Student portal: applications, documents, shortlist, brochures, notifications (§30, §53-§60).

Authentication is the student's own opaque profile token — the same one the assessment
returns. Every route re-resolves the token to a profile; a token never grants access to
another student's rows, and documents are only ever served through short-lived signed URLs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import serializers as S
from app.core import storage
from app.core.database import get_db
from app.models.base import APPLICATION_STATUSES
from app.models.crm import (Application, ApplicationStatusHistory, Brochure, Document,
                            Notification, ShortlistItem, StudentProfile)
from app.models.education import Program, Scholarship, University
from app.services import notifications

router = APIRouter(tags=["student"])

# Canonical pipeline from app.models.base (§53). The last three are terminal/off-track.
APPLICATION_STAGES = list(APPLICATION_STATUSES)
ACTIVE_STAGES = [s for s in APPLICATION_STAGES if s not in {"WITHDRAWN", "REJECTED"}]

DOCUMENT_TYPES = [
    "PASSPORT", "SSC_CERTIFICATE", "SSC_TRANSCRIPT", "HSC_CERTIFICATE", "HSC_TRANSCRIPT",
    "BACHELOR_CERTIFICATE", "BACHELOR_TRANSCRIPT", "MASTER_CERTIFICATE", "MASTER_TRANSCRIPT",
    "IELTS_TRMC", "TOEFL_REPORT", "PTE_REPORT", "MOI_CERTIFICATE", "GRE_REPORT", "GMAT_REPORT",
    "CV", "SOP", "RECOMMENDATION_LETTER", "EXPERIENCE_LETTER", "BANK_STATEMENT",
    "AFFIDAVIT_OF_SUPPORT", "RESEARCH_PROPOSAL", "PUBLICATION", "PHOTO", "OTHER",
]


# ------------------------------------------------------------------ helpers
def _profile(db: Session, token: str) -> StudentProfile:
    row = db.execute(
        select(StudentProfile).where(StudentProfile.public_token == token)
    ).scalars().first()
    if not row:
        raise HTTPException(404, "We could not find that profile. Check the link or run the assessment again.")
    return row


def _application_out(db: Session, app_row: Application) -> Dict[str, Any]:
    program = db.get(Program, app_row.program_id)
    university = db.get(University, program.university_id) if program else None
    history = db.execute(
        select(ApplicationStatusHistory)
        .where(ApplicationStatusHistory.application_id == app_row.id)
        .order_by(ApplicationStatusHistory.created_at.asc())
    ).scalars().all()
    index = ACTIVE_STAGES.index(app_row.status) if app_row.status in ACTIVE_STAGES else 0
    return {
        "id": app_row.id,
        "reference": app_row.reference,
        "status": app_row.status,
        "status_index": index,
        "progress_percent": 0 if app_row.status in {"WITHDRAWN", "REJECTED"}
        else round(((index + 1) / len(ACTIVE_STAGES)) * 100),
        "intake_label": app_row.intake_label,
        "notes": app_row.notes,
        "submitted_at": S._dt(app_row.submitted_at),
        "decision_at": S._dt(app_row.decision_at),
        "created_at": S._dt(app_row.created_at),
        "program": {
            "id": program.id, "name": program.name, "slug": program.slug,
            "degree_level": program.degree_level, "tuition_annual": program.tuition_annual,
            "currency": program.currency,
        } if program else None,
        "university": {
            "id": university.id, "name": university.name, "slug": university.slug, "city": university.city,
        } if university else None,
        "history": [
            {"from": h.from_status, "to": h.to_status, "note": h.note, "at": S._dt(h.created_at)}
            for h in history
        ],
    }


def _document_out(doc: Document, *, with_url: bool = True) -> Dict[str, Any]:
    return {
        "id": doc.id,
        "doc_type": doc.doc_type,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "status": doc.status,
        "review_note": doc.review_note,
        "expires_at": S._dt(doc.expires_at),
        "uploaded_at": S._dt(doc.created_at),
        # Short-lived, signed, single-key URL. Never the raw storage key.
        "download_url": storage.signed_url(doc.storage_key) if with_url else None,
    }


# ------------------------------------------------------------------- profile
@router.get("/portal/{token}")
def portal_overview(token: str, db: Session = Depends(get_db)):
    """Everything the student's dashboard needs in one request."""
    profile = _profile(db, token)

    applications = db.execute(
        select(Application).where(Application.student_profile_id == profile.id)
        .order_by(Application.created_at.desc())
    ).scalars().all()
    documents = db.execute(
        select(Document).where(Document.student_profile_id == profile.id)
        .order_by(Document.created_at.desc())
    ).scalars().all()
    shortlist = db.execute(
        select(ShortlistItem).where(ShortlistItem.student_profile_id == profile.id)
        .order_by(ShortlistItem.priority.desc(), ShortlistItem.created_at.desc())
    ).scalars().all()
    brochures = db.execute(
        select(Brochure).where(Brochure.student_profile_id == profile.id)
        .order_by(Brochure.created_at.desc())
    ).scalars().all()
    notes = db.execute(
        select(Notification).where(Notification.student_profile_id == profile.id)
        .order_by(Notification.created_at.desc()).limit(25)
    ).scalars().all()

    required = ["PASSPORT", "BACHELOR_TRANSCRIPT", "IELTS_TRMC", "CV", "SOP"]
    present = {d.doc_type for d in documents if d.status != "REJECTED"}

    return {
        "profile": {
            "token": profile.public_token, "full_name": profile.full_name, "email": profile.email,
            "phone": profile.phone, "city": profile.city, "nationality": profile.nationality,
            "highest_qualification": profile.highest_qualification, "major": profile.major,
            "cgpa": profile.cgpa, "cgpa_scale": profile.cgpa_scale,
            "ielts_overall": profile.ielts_overall, "target_degree_level": profile.target_degree_level,
            "target_discipline": profile.target_discipline, "target_intake": profile.target_intake,
            "budget_per_year": profile.budget_per_year, "budget_currency": profile.budget_currency,
            "preferred_countries": profile.preferred_countries,
            "research_interests": profile.research_interests,
            "updated_at": S._dt(profile.updated_at),
        },
        "applications": [_application_out(db, a) for a in applications],
        "documents": [_document_out(d) for d in documents],
        "document_checklist": [
            {"doc_type": t, "provided": t in present, "required": True} for t in required
        ] + [
            {"doc_type": d.doc_type, "provided": True, "required": False}
            for d in documents if d.doc_type not in required
        ],
        "shortlist": [
            {"id": s.id, "entity_type": s.entity_type, "entity_id": s.entity_id, "slug": s.slug,
             "label": s.label, "meta": s.meta, "note": s.note, "priority": s.priority}
            for s in shortlist
        ],
        "brochures": [
            {"token": b.token, "degree_level": b.degree_level, "version": b.version,
             "target_intake": b.target_intake, "created_at": S._dt(b.created_at),
             "url": f"/brochure/{b.token}"}
            for b in brochures
        ],
        "notifications": [
            {"id": n.id, "title": n.title, "body": n.body, "severity": n.severity,
             "action_url": n.action_url, "read": n.read_at is not None, "at": S._dt(n.created_at)}
            for n in notes
        ],
        "counts": {
            "applications": len(applications),
            "documents": len(documents),
            "documents_pending_review": sum(1 for d in documents if d.status == "UPLOADED"),
            "shortlist": len(shortlist),
            "unread_notifications": sum(1 for n in notes if n.read_at is None),
            "checklist_complete": sum(1 for t in required if t in present),
            "checklist_total": len(required),
        },
        "stages": APPLICATION_STAGES,
        "document_types": DOCUMENT_TYPES,
    }


# --------------------------------------------------------------- applications
class ApplicationIn(BaseModel):
    program_id: Optional[int] = None
    program_slug: Optional[str] = None
    intake_label: Optional[str] = None
    notes: Optional[str] = None


@router.post("/portal/{token}/applications")
def create_application(token: str, body: ApplicationIn, db: Session = Depends(get_db)):
    profile = _profile(db, token)

    program = None
    if body.program_id:
        program = db.get(Program, body.program_id)
    elif body.program_slug:
        program = db.execute(select(Program).where(Program.slug == body.program_slug)).scalars().first()
    if not program:
        raise HTTPException(404, "That programme is not in our database.")

    existing = db.execute(
        select(Application).where(
            Application.student_profile_id == profile.id, Application.program_id == program.id
        )
    ).scalars().first()
    if existing:
        return {"ok": True, "duplicate": True, "application": _application_out(db, existing)}

    count = db.execute(select(Application)).scalars().all()
    row = Application(
        reference=f"APP-{datetime.utcnow():%Y}-{len(count) + 1:06d}",
        student_profile_id=profile.id, program_id=program.id,
        intake_label=body.intake_label or profile.target_intake, status="PLANNING",
        notes=body.notes,
    )
    db.add(row)
    db.flush()
    db.add(ApplicationStatusHistory(application_id=row.id, from_status=None, to_status="PLANNING",
                                    note="Application started from the student portal."))
    db.commit()
    db.refresh(row)

    notifications.broadcast_to_role(
        db, "COUNSELOR", "APPLICATION_STATUS", reference=row.reference, status="PLANNING",
        program=program.name, previous="—", entity_type="application", entity_id=row.id,
        action_url=f"/admin/applications",
    )
    return {"ok": True, "application": _application_out(db, row)}


@router.delete("/portal/{token}/applications/{application_id}")
def withdraw_application(token: str, application_id: int, db: Session = Depends(get_db)):
    profile = _profile(db, token)
    row = db.get(Application, application_id)
    if not row or row.student_profile_id != profile.id:
        raise HTTPException(404, "Application not found")
    previous, row.status = row.status, "WITHDRAWN"
    db.add(ApplicationStatusHistory(application_id=row.id, from_status=previous,
                                    to_status="WITHDRAWN", note="Withdrawn by the student."))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ documents
@router.post("/portal/{token}/documents")
async def upload_document(
    token: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    profile = _profile(db, token)
    if doc_type not in DOCUMENT_TYPES:
        raise HTTPException(400, f"Unknown document type. Expected one of: {', '.join(DOCUMENT_TYPES)}")

    content = await file.read()
    try:
        stored = storage.save_upload(
            owner_scope=f"students/{profile.id}", filename=file.filename or "upload",
            content=content, declared_mime=file.content_type,
        )
    except storage.StorageError as exc:
        raise HTTPException(400, str(exc))

    row = Document(
        student_profile_id=profile.id, doc_type=doc_type, storage_key=stored.key,
        original_filename=stored.original_name, mime_type=stored.mime,
        size_bytes=stored.size, status="UPLOADED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    notifications.broadcast_to_role(
        db, "APPLICATION_OFFICER", "DOCUMENT_UPLOADED", doc_type=doc_type,
        student=profile.full_name, filename=stored.original_name,
        entity_type="document", entity_id=row.id, action_url="/admin/documents",
    )
    return {"ok": True, "document": _document_out(row)}


@router.delete("/portal/{token}/documents/{document_id}")
def delete_document(token: str, document_id: int, db: Session = Depends(get_db)):
    profile = _profile(db, token)
    row = db.get(Document, document_id)
    if not row or row.student_profile_id != profile.id:
        raise HTTPException(404, "Document not found")
    if row.status == "ACCEPTED":
        raise HTTPException(400, "An accepted document cannot be deleted. Ask your counselor.")
    storage.remove(row.storage_key)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/documents/file/{signed_token}")
def download_document(signed_token: str, db: Session = Depends(get_db)):
    """The ONLY way to read a stored file. The token is key-bound and expires in minutes."""
    try:
        key = storage.verify_signature(signed_token)
    except storage.StorageError as exc:
        raise HTTPException(403, str(exc))

    doc = db.execute(select(Document).where(Document.storage_key == key)).scalars().first()
    if not doc:
        raise HTTPException(404, "File not found")
    try:
        data = storage.read(key)
    except storage.StorageError as exc:
        raise HTTPException(404, str(exc))

    return Response(
        content=data,
        media_type=doc.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{doc.original_filename or "document"}"',
            "Cache-Control": "private, no-store, max-age=0",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


# ------------------------------------------------------------------ shortlist
class ShortlistIn(BaseModel):
    entity_type: str = Field(pattern="^(program|university|scholarship)$")
    slug: str
    label: Optional[str] = None
    note: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


@router.get("/portal/{token}/shortlist")
def list_shortlist(token: str, db: Session = Depends(get_db)):
    profile = _profile(db, token)
    rows = db.execute(
        select(ShortlistItem).where(ShortlistItem.student_profile_id == profile.id)
        .order_by(ShortlistItem.priority.desc(), ShortlistItem.created_at.desc())
    ).scalars().all()
    return [{"id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "slug": r.slug,
             "label": r.label, "meta": r.meta, "note": r.note, "priority": r.priority} for r in rows]


@router.post("/portal/{token}/shortlist")
def add_shortlist(token: str, body: ShortlistIn, db: Session = Depends(get_db)):
    profile = _profile(db, token)

    model = {"program": Program, "university": University, "scholarship": Scholarship}[body.entity_type]
    entity = db.execute(select(model).where(model.slug == body.slug)).scalars().first()
    if not entity:
        raise HTTPException(404, "That record is not in our database.")

    existing = db.execute(
        select(ShortlistItem).where(
            ShortlistItem.student_profile_id == profile.id,
            ShortlistItem.entity_type == body.entity_type,
            ShortlistItem.slug == body.slug,
        )
    ).scalars().first()
    if existing:
        existing.note = body.note or existing.note
        db.commit()
        return {"ok": True, "duplicate": True, "id": existing.id}

    row = ShortlistItem(
        student_profile_id=profile.id, entity_type=body.entity_type, entity_id=entity.id,
        slug=body.slug, label=body.label or getattr(entity, "name", body.slug),
        meta=body.meta, note=body.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "id": row.id}


@router.post("/portal/{token}/shortlist/sync")
def sync_shortlist(token: str, items: List[ShortlistIn], db: Session = Depends(get_db)):
    """Push a browser-local shortlist to the server when a student first gets a profile."""
    profile = _profile(db, token)
    added = 0
    for item in items[:100]:
        model = {"program": Program, "university": University, "scholarship": Scholarship}[item.entity_type]
        entity = db.execute(select(model).where(model.slug == item.slug)).scalars().first()
        if not entity:
            continue
        exists = db.execute(
            select(ShortlistItem).where(
                ShortlistItem.student_profile_id == profile.id,
                ShortlistItem.entity_type == item.entity_type,
                ShortlistItem.slug == item.slug,
            )
        ).scalars().first()
        if exists:
            continue
        db.add(ShortlistItem(
            student_profile_id=profile.id, entity_type=item.entity_type, entity_id=entity.id,
            slug=item.slug, label=item.label or getattr(entity, "name", item.slug), meta=item.meta,
        ))
        added += 1
    db.commit()
    return {"ok": True, "added": added}


@router.delete("/portal/{token}/shortlist/{item_id}")
def remove_shortlist(token: str, item_id: int, db: Session = Depends(get_db)):
    profile = _profile(db, token)
    row = db.get(ShortlistItem, item_id)
    if not row or row.student_profile_id != profile.id:
        raise HTTPException(404, "Item not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# -------------------------------------------------------------- notifications
@router.post("/portal/{token}/notifications/{note_id}/read")
def read_notification(token: str, note_id: int, db: Session = Depends(get_db)):
    profile = _profile(db, token)
    ok = notifications.mark_read(db, note_id, student_profile_id=profile.id)
    if not ok:
        raise HTTPException(404, "Notification not found")
    return {"ok": True}
