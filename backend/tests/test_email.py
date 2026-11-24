"""Email routing: small attachments use sendMail (Mail.Send only); large ones use the draft + upload flow."""
from types import SimpleNamespace

import pytest

from app.services import email


class FakeResp:
    def __init__(self, body=None):
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def graph_calls(monkeypatch):
    calls = []

    def fake_call(method, path, **kw):
        calls.append((method, path, kw))
        if path.endswith("/createUploadSession"):
            return FakeResp({"uploadUrl": "https://upload.example/x"})
        return FakeResp({"id": "draft1"})

    s = email.get_settings()
    for k, v in {"graph_app_id": "a", "graph_tenant_id": "t", "graph_client_secret": "s", "graph_shared_mailbox": "po@ges.com"}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(email.graph, "call", fake_call)
    monkeypatch.setattr(email.requests, "put", lambda *a, **k: SimpleNamespace(status_code=200, text=""))
    return calls


def make_request(size: int):
    att = SimpleNamespace(file_name="sow.pdf", blob_path="x/y.pdf", category="agreement")
    return SimpleNamespace(
        request_number="PR-2026-0001", supplier_name="Acme", project_number="012345678", cost_center="0410",
        amount=1000.0, requester_email="a@ges.com", requester_name="A", approver_email="ap@ges.com",
        approver_name="Approver", po_recipients=None, is_po_extension=False, previous_po=None,
        is_capital_project=False, capital_project_number=None, capital_cost_center=None, legal_review=None, start_date="2026-01-01", end_date="2026-12-31",
        agreement_signed=False, signature_date=None, is_urgent=False, urgent_reason=None,
        description="<b>x</b>", attachments=[att],
    ), b"%PDF-" + b"0" * size


def test_small_attachments_use_sendmail(graph_calls, monkeypatch):
    r, data = make_request(1000)
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    email.send_submission_email(r)
    assert [c[1] for c in graph_calls] == ["/users/po@ges.com/sendMail"]
    msg = graph_calls[0][2]["json"]["message"]
    assert msg["ccRecipients"][0]["emailAddress"]["address"] == "a@ges.com"
    assert msg["replyTo"][0]["emailAddress"]["address"] == "a@ges.com"
    assert "&lt;b&gt;x&lt;/b&gt;" in msg["body"]["content"]  # user text is escaped
    assert msg["subject"] == "[TEST – LOCAL] [PR-2026-0001] – Acme – Project 012345678 – CC 0410 – $1,000.00"
    assert msg["importance"] == "normal" and "TEST ENVIRONMENT" in msg["body"]["content"]


def test_separate_recipient_group(graph_calls, monkeypatch):
    r, data = make_request(10)
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    monkeypatch.setattr(email.get_settings(), "email_to", "po-team@ges.com, backup@ges.com")
    email.send_submission_email(r)
    msg = graph_calls[0][2]["json"]["message"]
    assert graph_calls[0][1] == "/users/po@ges.com/sendMail"  # sent from the mailbox
    assert [t["emailAddress"]["address"] for t in msg["toRecipients"]] == ["po-team@ges.com", "backup@ges.com"]


def test_large_attachments_use_draft_and_upload_session(graph_calls, monkeypatch):
    r, data = make_request(5 * 1024 * 1024)
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    email.send_submission_email(r)
    assert [c[1] for c in graph_calls] == [
        "/users/po@ges.com/messages",
        "/users/po@ges.com/messages/draft1/attachments/createUploadSession",
        "/users/po@ges.com/messages/draft1/send",
    ]


def test_urgent_is_high_importance_and_prod_has_no_test_label(graph_calls, monkeypatch):
    r, data = make_request(10)
    r.is_urgent, r.urgent_reason = True, "Commercial Deadline"
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    monkeypatch.setattr(email.get_settings(), "app_env", "prod")
    email.send_submission_email(r)
    msg = graph_calls[0][2]["json"]["message"]
    assert msg["importance"] == "high"
    assert msg["subject"].startswith("[PR-2026-0001] URGENT")
    assert "TEST ENVIRONMENT" not in msg["body"]["content"]


@pytest.mark.parametrize("env", ["", "  ", "PROD", "production"])
def test_blank_or_prod_env_still_sends_without_test_label(graph_calls, monkeypatch, env):
    r, data = make_request(10)
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    monkeypatch.setattr(email.get_settings(), "app_env", env)
    email.send_submission_email(r)
    msg = graph_calls[0][2]["json"]["message"]
    assert graph_calls[0][1] == "/users/po@ges.com/sendMail"
    assert msg["subject"].startswith("[PR-2026-0001]") and "TEST ENVIRONMENT" not in msg["body"]["content"]


def test_admin_cc_added_after_requester_without_duplicates(graph_calls, monkeypatch):
    r, data = make_request(10)
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    email.send_submission_email(r, to=["po@ges.com"], cc=["boss@ges.com", "A@ges.com"])
    msg = graph_calls[0][2]["json"]["message"]
    assert [t["emailAddress"]["address"] for t in msg["toRecipients"]] == ["po@ges.com"]
    assert [c["emailAddress"]["address"] for c in msg["ccRecipients"]] == ["a@ges.com", "boss@ges.com"]


def test_po_email_goes_to_requester_and_po_recipients_only(graph_calls, monkeypatch):
    r, data = make_request(10)
    r.attachments.append(SimpleNamespace(file_name="PO-4500123.pdf", blob_path="x/po.pdf", category="po"))
    r.po_number, r.po_recipients = "4500123", "ap@ges.com, A@ges.com, ap@ges.com"
    monkeypatch.setattr(email.storage, "get", lambda c, p: data)
    email.send_po_email(r, note="Thanks")
    msg = graph_calls[0][2]["json"]["message"]
    assert [t["emailAddress"]["address"] for t in msg["toRecipients"]] == ["a@ges.com"]
    assert [c["emailAddress"]["address"] for c in msg["ccRecipients"]] == ["ap@ges.com"]  # not the approver
    assert [a["name"] for a in msg["attachments"]] == ["PO-4500123.pdf"]  # PO only
    assert "PO 4500123 issued" in msg["subject"] and "Thanks" in msg["body"]["content"]
    # The submission email (e.g. a resend) never carries the PO
    graph_calls.clear()
    email.send_submission_email(r, to=["po@ges.com"])
    assert [a["name"] for a in graph_calls[0][2]["json"]["message"]["attachments"]] == ["sow.pdf"]
