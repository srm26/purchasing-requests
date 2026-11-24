"""Purchasing requests: submit, list, detail, attachments, and admin actions."""
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth import User, get_current_user, require_admin
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import NOT_LISTED_APPROVER, STATUSES, Attachment, PurchaseRequest, StatusHistory, utcnow
from ..schemas import NotesUpdate, RequestDetail, RequestIn, RequestSummary, StatusUpdate
from ..services import email, lookups, recipients, storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/requests", tags=["requests"])


def _next_number(db: Session) -> str:
    prefix = f"PR-{datetime.now(timezone.utc).year}-"
    last = db.scalar(select(func.max(PurchaseRequest.request_number)).where(PurchaseRequest.request_number.like(f"{prefix}%")))
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def _get_visible(db: Session, number: str, user: User) -> PurchaseRequest:
    r = db.scalar(
        select(PurchaseRequest)
        .where(PurchaseRequest.request_number == number)
        .options(selectinload(PurchaseRequest.attachments), selectinload(PurchaseRequest.history))
    )
    # Same 404 for "missing" and "not yours", so request numbers can't be probed.
    if not r or (not user.is_admin and r.requester_email != user.email):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    return r


def _send_email(db: Session, r: PurchaseRequest) -> None:
    try:
        email.send_submission_email(r, *recipients.resolve(db, r.amount))
        r.email_status, r.email_error, r.email_sent_at = "Sent", None, utcnow()
    except Exception as exc:
        log.exception("Email failed for %s", r.request_number)
        r.email_status, r.email_error = "Failed", str(exc)[:1000]
    db.commit()


def _send_po_email(db: Session, r: PurchaseRequest, note: str | None = None) -> None:
    try:
        email.send_po_email(r, note)
        r.po_email_status, r.po_email_error, r.po_email_sent_at = "Sent", None, utcnow()
    except Exception as exc:
        log.exception("PO email failed for %s", r.request_number)
        r.po_email_status, r.po_email_error = "Failed", str(exc)[:1000]
    db.commit()


def _email_in_background(request_id: int, po_note: str | None = None, po: bool = False) -> None:
    """Runs after the response is sent, so the requester isn't kept waiting on Graph (often 5-15 s).
    Status stays "Pending" until this finishes; a failure is recorded for admin resend as before."""
    with SessionLocal() as db:
        r = db.get(PurchaseRequest, request_id)
        if r:
            _send_po_email(db, r, po_note) if po else _send_email(db, r)


async def _read_files(files: list[UploadFile], label: str, limit: int) -> list[tuple[str, bytes]]:
    s = get_settings()
    if len(files) > limit:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{label}: attach at most {limit} files")
    out = []
    for f in files:
        data = await f.read()
        name = (f.filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1][:255]
        kind = storage.FILE_TYPES.get(storage.extension(name))
        if not kind:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{name}': only PDF, Word or Excel files are allowed")
        if not data.startswith(kind[1]):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{name}' doesn't match its file type")
        if len(data) > s.max_file_mb * 1024 * 1024:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{name}' is larger than {s.max_file_mb} MB")
        out.append((name, data))
    return out


@router.post("", response_model=RequestDetail, status_code=status.HTTP_201_CREATED)
async def submit_request(
    payload: str = Form(...),
    agreement_files: list[UploadFile] = File(default=[]),
    supporting_files: list[UploadFile] = File(default=[]),
    background: BackgroundTasks = BackgroundTasks(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        data = RequestIn.model_validate(json.loads(payload))
    except ValidationError as exc:
        msgs = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'form'}: {e['msg']}" for e in exc.errors())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, msgs) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid form data") from exc
    s = get_settings()
    agreement = await _read_files(agreement_files, "Fully Executed Agreement", s.max_agreement_files)
    supporting = await _read_files(supporting_files, "Supporting Documents", s.max_supporting_files)
    if data.agreement_signed and not agreement:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Attach the fully executed agreement")
    if not agreement and not supporting:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Attach at least one document")

    if data.approver_not_listed:
        approver_name = NOT_LISTED_APPROVER
    else:
        approver = lookups.find(db, "approvers", data.approver_email)
        if not approver:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Approver is not on the approvers list")
        approver_name = approver.label

    r = PurchaseRequest(
        **data.model_dump(exclude={"po_recipients", "approver_not_listed"}),
        po_recipients=", ".join(data.po_recipients) or None,
        approver_name=approver_name,
        request_number=_next_number(db),
        requester_email=user.email,
        requester_name=user.name,
    )
    for category, uploads in (("agreement", agreement), ("supporting", supporting)):
        for name, content in uploads:
            path = f"{r.request_number}/{uuid.uuid4().hex}{storage.extension(name)}"
            storage.put(s.blob_attachments_container, path, content, storage.content_type(name))
            r.attachments.append(Attachment(file_name=name, blob_path=path, size_bytes=len(content), category=category))
    r.history.append(StatusHistory(old_status=None, new_status="Pending Review", changed_by=user.email))
    db.add(r)
    db.commit()
    log.info("Request %s submitted by %s", r.request_number, user.email)

    background.add_task(_email_in_background, r.id)  # saved first; email_status stays "Pending" until sent
    return r


@router.get("", response_model=list[RequestSummary])
def list_requests(
    scope: str = Query("mine", pattern="^(mine|all)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(PurchaseRequest).order_by(PurchaseRequest.submitted_at.desc())
    if scope == "mine" or not user.is_admin:
        q = q.where(PurchaseRequest.requester_email == user.email)
    return db.scalars(q).all()


@router.get("/export.csv")
def export_csv(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    cols = [c.name for c in PurchaseRequest.__table__.columns if c.name != "id"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in db.scalars(select(PurchaseRequest).order_by(PurchaseRequest.submitted_at)):
        w.writerow([getattr(r, c) for c in cols])
    name = f"purchasing_requests_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{number}", response_model=RequestDetail)
def get_request(number: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_visible(db, number, user)


@router.get("/{number}/attachments/{attachment_id}")
def download_attachment(number: str, attachment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _get_visible(db, number, user)
    a = next((a for a in r.attachments if a.id == attachment_id), None)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    data = storage.get(get_settings().blob_attachments_container, a.blob_path)
    safe = a.file_name.replace('"', "")
    # PDFs open in the browser; Word/Excel files download.
    disposition = "inline" if storage.extension(a.file_name) == ".pdf" else "attachment"
    return StreamingResponse(io.BytesIO(data), media_type=storage.content_type(a.file_name),
                             headers={"Content-Disposition": f'{disposition}; filename="{safe}"'})


@router.patch("/{number}/status", response_model=RequestDetail)
def update_status(number: str, body: StatusUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.status not in STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Status must be one of {STATUSES}")
    r = _get_visible(db, number, admin)
    if body.status == "PO Issued" and not r.po_number:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use 'Issue final PO' to upload the PO first")
    if body.status != r.status or body.note:
        r.history.append(StatusHistory(old_status=r.status, new_status=body.status, changed_by=admin.email, note=body.note))
        r.status = body.status
        db.commit()
    return r


@router.put("/{number}/notes", response_model=RequestDetail)
def update_notes(number: str, body: NotesUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = _get_visible(db, number, admin)
    r.admin_notes = body.admin_notes
    db.commit()
    return r


@router.post("/{number}/resend-email", response_model=RequestDetail)
def resend_email(number: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = _get_visible(db, number, admin)
    log.info("Email resend for %s by %s", number, admin.email)
    _send_email(db, r)
    return r


@router.post("/{number}/po", response_model=RequestDetail)
async def issue_po(
    number: str,
    po_number: str = Form(..., min_length=1, max_length=50),
    note: str | None = Form(default=None, max_length=2000),
    file: UploadFile = File(...),
    background: BackgroundTasks = BackgroundTasks(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Purchasing uploads the final PO: stored, status -> PO Issued, emailed to the requester + PO recipients."""
    r = _get_visible(db, number, admin)
    if r.po_number:
        raise HTTPException(status.HTTP_409_CONFLICT, f"PO {r.po_number} was already issued for this requisition")
    (name, content), = await _read_files([file], "Final PO", 1)
    if storage.extension(name) != ".pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The final PO must be a PDF")
    path = f"{r.request_number}/{uuid.uuid4().hex}.pdf"
    storage.put(get_settings().blob_attachments_container, path, content, storage.content_type(name))
    r.attachments.append(Attachment(file_name=name, blob_path=path, size_bytes=len(content), category="po"))
    note = (note or "").strip() or None
    r.po_number, r.po_issued_at, r.po_issued_by = po_number.strip(), utcnow(), admin.email
    r.history.append(StatusHistory(old_status=r.status, new_status="PO Issued", changed_by=admin.email,
                                   note=f"PO {r.po_number} issued" + (f"\n{note}" if note else "")))
    r.status, r.po_email_status = "PO Issued", "Pending"
    db.commit()
    log.info("PO %s issued for %s by %s", r.po_number, number, admin.email)
    background.add_task(_email_in_background, r.id, note, po=True)  # PO is saved; a failed email can be resent
    return r


@router.post("/{number}/po/resend-email", response_model=RequestDetail)
def resend_po_email(number: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = _get_visible(db, number, admin)
    if not r.po_number:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No PO has been issued for this requisition")
    log.info("PO email resend for %s by %s", number, admin.email)
    _send_po_email(db, r)
    return r
