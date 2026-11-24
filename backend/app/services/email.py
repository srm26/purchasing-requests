"""Emails via Graph from the PO shared mailbox:
- submission: to EMAIL_TO / Admin-page recipients, CC + Reply-To the requester
- final PO: to the requester, CC the Additional PO recipients, with the PO attached

Graph's sendMail takes a ~4 MB request, so:
- attachments under 3 MB in total: one sendMail call (needs only the Mail.Send permission)
- larger: create a draft, attach big files through an upload session, then send it
  (needs Mail.ReadWrite as well, scoped to the PO mailbox by the same Exchange access policy)
"""
import base64
import html
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..config import get_settings
from ..models import PurchaseRequest
from . import graph, storage

log = logging.getLogger(__name__)
INLINE_LIMIT = 3 * 1024 * 1024
CHUNK = 4 * 1024 * 1024  # Outlook upload sessions accept up to 4 MB per PUT


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def subject_for(r: PurchaseRequest) -> str:
    parts = [f"[{r.request_number}]" + (" URGENT" if r.is_urgent else ""), r.supplier_name,
             f"Project {r.project_number}", f"CC {r.cost_center}", money(r.amount)]
    return _test_prefix() + " – ".join(parts)


def _is_test() -> bool:
    """Non-production environments send clearly marked test emails. Only the label changes; sending never
    depends on APP_ENV. A blank APP_ENV counts as production."""
    return get_settings().app_env.strip().lower() not in ("", "prod", "production")


def _test_prefix() -> str:
    return f"[TEST – {get_settings().app_env.upper()}] " if _is_test() else ""


def body_for(r: PurchaseRequest) -> str:
    s = get_settings()
    link = f"{s.app_base_url.rstrip('/')}/requests/{r.request_number}"
    yes_no = lambda b: "Yes" if b else "No"  # noqa: E731
    rows = [
        ("Urgent", f"Yes – {r.urgent_reason}" if r.is_urgent else None),
        ("Requester", f"{r.requester_name} <{r.requester_email}>"),
        ("Approver", f"{r.approver_name} <{r.approver_email}>"),
        ("Additional PO recipients", r.po_recipients),
        ("Supplier", r.supplier_name),
        ("Service dates", f"{r.start_date} to {r.end_date}"),
        ("Agreement signed", yes_no(r.agreement_signed) + (f" – {r.signature_date}" if r.signature_date else "")),
        ("Total order form / SOW value", money(r.amount)),
        ("Legal / procurement review", None if r.legal_review is None else yes_no(r.legal_review)),
        ("PO extension / add-on", yes_no(r.is_po_extension) + (f" – Previous PO {r.previous_po}" if r.previous_po else "")),
        ("Capital project", yes_no(r.is_capital_project) + (f" – {r.capital_project_number}" if r.capital_project_number else "")),
        ("Capital task / cost center", r.capital_cost_center),
        ("Project number", r.project_number),
        ("Task / cost center", r.cost_center),
    ]

    trs = "".join(
        f'<tr><td style="padding:6px 12px;background:#f4f6f9;font-weight:600;width:220px;border-bottom:1px solid #e3e7ee">'
        f'{html.escape(k)}</td><td style="padding:6px 12px;border-bottom:1px solid #e3e7ee">{html.escape(str(v))}</td></tr>'
        for k, v in rows if v not in (None, "")
    )
    def file_list(category: str) -> str:
        items = "".join(f"<li>{html.escape(a.file_name)}</li>" for a in r.attachments if a.category == category)
        return f"<ul>{items}</ul>" if items else "<p>None</p>"
    files = (f'<h3 style="margin:18px 0 6px">Fully executed agreement</h3>{file_list("agreement")}'
             f'<h3 style="margin:18px 0 6px">Supporting documents</h3>{file_list("supporting")}')
    notes = (f'<h3 style="margin:18px 0 6px">Description / notes</h3>'
             f'<div style="white-space:pre-wrap;max-width:760px">{html.escape(r.description)}</div>') if r.description else ""
    test_banner = ('<div style="background:#fff4ce;color:#5c4400;border:1px solid #e0c060;padding:10px 18px;font-weight:600">'
                   'TEST ENVIRONMENT – this is a test requisition. Do not raise a PO.</div>') if _is_test() else ""
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1b2430">
{test_banner}<div style="background:#0b2341;color:#fff;padding:14px 18px;font-size:18px;font-weight:600">
New IT procurement requisition {html.escape(r.request_number)}</div>
<table style="border-collapse:collapse;width:100%;max-width:760px;margin-top:12px">{trs}</table>
{notes}{files}
<p><a href="{html.escape(link)}" style="background:#0067b1;color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px">
Open request in the Purchasing Request portal</a></p>
<p style="color:#6b7686;font-size:12px">Reply to this email to reach the requester directly.</p></div>"""


def _attach(mailbox: str, message_id: str, name: str, data: bytes) -> None:
    base = f"/users/{mailbox}/messages/{message_id}/attachments"
    if len(data) < INLINE_LIMIT:
        graph.call("POST", base, json={
            "@odata.type": "#microsoft.graph.fileAttachment", "name": name,
            "contentType": storage.content_type(name), "contentBytes": base64.b64encode(data).decode(),
        })
        return
    session = graph.call("POST", f"{base}/createUploadSession", json={
        "AttachmentItem": {"attachmentType": "file", "name": name, "size": len(data), "contentType": storage.content_type(name)}
    }).json()
    for start in range(0, len(data), CHUNK):
        chunk = data[start:start + CHUNK]
        # The upload URL carries its own token; sending an Authorization header makes it fail.
        resp = requests.put(session["uploadUrl"], data=chunk, timeout=graph.TIMEOUT, headers={
            "Content-Type": "application/octet-stream", "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
        })
        if resp.status_code >= 400:
            raise graph.GraphError(f"Attachment upload failed for {name}: {resp.status_code} {resp.text[:300]}")


def send_submission_email(r: PurchaseRequest, to: list[str] | None = None, cc: list[str] | None = None) -> None:
    """Send the email or raise. The caller records Sent/Failed on the request.
    to/cc come from the Admin page; without them, To falls back to EMAIL_TO (then the sender mailbox)."""
    s = get_settings()
    subject, body = subject_for(r), body_for(r)
    if not s.email_configured:
        return _write_outbox(r.request_number, subject, body)

    mb = s.graph_shared_mailbox
    to = to or [a.strip() for a in (s.email_to or mb).split(",") if a.strip()]
    # Requester first; skip duplicates so nobody gets the email twice.
    cc_all = [{"emailAddress": {"address": r.requester_email, "name": r.requester_name}}] + [
        {"emailAddress": {"address": a}} for a in (cc or []) if a.lower() != r.requester_email.lower()]
    message = {
        "subject": subject,
        "importance": "high" if r.is_urgent else "normal",
        "body": {"contentType": "HTML", "content": body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": cc_all,
        "replyTo": [{"emailAddress": {"address": r.requester_email, "name": r.requester_name}}],
    }
    _deliver(mb, message, [a for a in r.attachments if a.category != "po"])
    log.info("Email sent for %s at %s", r.request_number, datetime.now(timezone.utc).isoformat())


def po_subject_for(r: PurchaseRequest) -> str:
    return _test_prefix() + f"[{r.request_number}] PO {r.po_number} issued – {r.supplier_name}"


def po_body_for(r: PurchaseRequest, note: str | None = None) -> str:
    s = get_settings()
    link = f"{s.app_base_url.rstrip('/')}/requests/{r.request_number}"
    rows = [("PO number", r.po_number), ("Requisition", r.request_number), ("Supplier", r.supplier_name),
            ("Total order form / SOW value", money(r.amount)), ("Project number", r.project_number),
            ("Task / cost center", r.cost_center), ("Requester", f"{r.requester_name} <{r.requester_email}>")]
    trs = "".join(
        f'<tr><td style="padding:6px 12px;background:#f4f6f9;font-weight:600;width:220px;border-bottom:1px solid #e3e7ee">'
        f'{html.escape(k)}</td><td style="padding:6px 12px;border-bottom:1px solid #e3e7ee">{html.escape(str(v))}</td></tr>'
        for k, v in rows)
    note_html = (f'<h3 style="margin:18px 0 6px">Note from Purchasing</h3>'
                 f'<div style="white-space:pre-wrap;max-width:760px">{html.escape(note)}</div>') if note else ""
    test_banner = ('<div style="background:#fff4ce;color:#5c4400;border:1px solid #e0c060;padding:10px 18px;font-weight:600">'
                   'TEST ENVIRONMENT – this is a test PO. Do not send it to the supplier.</div>') if _is_test() else ""
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1b2430">
{test_banner}<div style="background:#0b2341;color:#fff;padding:14px 18px;font-size:18px;font-weight:600">
Purchase order {html.escape(r.po_number or "")} issued for {html.escape(r.request_number)}</div>
<p>The final purchase order is attached.</p>
<table style="border-collapse:collapse;width:100%;max-width:760px;margin-top:12px">{trs}</table>
{note_html}
<p><a href="{html.escape(link)}" style="background:#0067b1;color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px">
Open request in the Purchasing Request portal</a></p></div>"""


def send_po_email(r: PurchaseRequest, note: str | None = None) -> None:
    """Final PO to the requester, CC the Additional PO recipients (not the approver, for now). Raises on failure."""
    s = get_settings()
    subject, body = po_subject_for(r), po_body_for(r, note)
    if not s.email_configured:
        return _write_outbox(f"{r.request_number}-PO", subject, body)

    seen = {r.requester_email.lower()}
    cc = [a for a in (x.strip() for x in (r.po_recipients or "").split(","))
          if a and not (a.lower() in seen or seen.add(a.lower()))]
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body},
        "toRecipients": [{"emailAddress": {"address": r.requester_email, "name": r.requester_name}}],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
    }
    _deliver(s.graph_shared_mailbox, message, [a for a in r.attachments if a.category == "po"])
    log.info("PO email sent for %s at %s", r.request_number, datetime.now(timezone.utc).isoformat())


def _write_outbox(name: str, subject: str, body: str) -> None:
    """Local/demo mode without Graph: save the email as an HTML file instead of sending it."""
    s = get_settings()
    if not s.use_local_services:
        raise RuntimeError("Email is not configured (GRAPH_* / GRAPH_SHARED_MAILBOX app settings missing)")
    outbox = Path(s.db_path).parent / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"{name}.html").write_text(f"<h2>{html.escape(subject)}</h2>{body}", encoding="utf-8")
    log.info("Local/demo mode: email written to %s", outbox / f"{name}.html")


def _deliver(mb: str, message: dict, attachments: list) -> None:
    """Send through Graph: one sendMail call under 3 MB of attachments, otherwise draft + upload sessions."""
    s = get_settings()
    files = [(a.file_name, storage.get(s.blob_attachments_container, a.blob_path)) for a in attachments]
    if sum(len(d) for _, d in files) < INLINE_LIMIT:
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment", "name": name,
            "contentType": storage.content_type(name), "contentBytes": base64.b64encode(data).decode(),
        } for name, data in files]
        graph.call("POST", f"/users/{mb}/sendMail", json={"message": message, "saveToSentItems": True})
    else:
        draft = graph.call("POST", f"/users/{mb}/messages", json=message).json()
        for name, data in files:
            _attach(mb, draft["id"], name, data)
        graph.call("POST", f"/users/{mb}/messages/{draft['id']}/send")
