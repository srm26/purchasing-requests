from conftest import ADMIN, ALICE, BOB, PDF, submit


def test_health_and_auth(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-MS-CLIENT-PRINCIPAL": "not-base64!"}).status_code == 401
    me = client.get("/api/me", headers=ADMIN).json()
    assert me == {"email": "po.admin@ges.com", "name": "Pat Admin", "is_admin": True, "demo_mode": False, "email_live": False}
    assert client.get("/api/me", headers=ALICE).json()["is_admin"] is False


def test_lookups_loaded_at_startup(client):
    lists = client.get("/api/lookups", headers=ALICE).json()
    assert [a["code"] for a in lists["approvers"]] == [
        "approver.one@example.com", "approver.two@example.com", "approver.three@example.com"]  # inactive row skipped
    assert lists["approvers"][0]["label"] == "SAMPLE – Approver One"
    assert "Late PO Request" in lists["urgent_reasons"]


def test_submit_saves_request_and_sends_email(client):
    r = submit(client, ALICE)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["request_number"].startswith("PR-") and body["status"] == "Pending Review"
    assert body["email_status"] == "Sent"  # local mode writes to the outbox folder
    assert body["approver_name"] == "SAMPLE – Approver One"
    assert body["project_number"] == "012345678" and body["cost_center"] == "0410"  # leading zeroes kept
    assert body["po_recipients"] == "ap@ges.com"
    assert body["attachments"][0]["file_name"] == "sow.pdf"
    pdf = client.get(f"/api/requests/{body['request_number']}/attachments/{body['attachments'][0]['id']}", headers=ALICE)
    assert pdf.content.startswith(b"%PDF-")


def test_validation(client):
    from datetime import date, timedelta
    ok = lambda **kw: submit(client, ALICE, **kw).status_code  # noqa: E731
    assert submit(client, ALICE, files=[]).status_code == 422
    assert submit(client, ALICE, files=[("supporting_files", ("x.pdf", b"MZ not a pdf", "application/pdf"))]).status_code == 422
    assert submit(client, ALICE, files=[("supporting_files", ("x.exe", b"MZ", "application/octet-stream"))]).status_code == 422
    assert submit(client, ALICE, files=[("supporting_files", ("x.doc", b"%PDF-1.4", "application/msword"))]).status_code == 422
    # Project number: exactly 9 digits; cost center: exactly 4 digits
    for bad in ["12345678", "1234567890", "12345678a", ""]:
        assert ok(project_number=bad) == 422, bad
    for bad in ["123", "12345", "12a4"]:
        assert ok(cost_center=bad) == 422, bad
    # Conditional fields
    assert ok(is_po_extension=True) == 422
    assert ok(is_po_extension=True, previous_po="PO12345") == 201
    assert ok(is_capital_project=True) == 422
    for bad in ["CP-1", "12345678", "1234567890"]:  # capital project number: exactly 9 digits
        assert ok(is_capital_project=True, capital_project_number=bad, capital_cost_center="0520") == 422, bad
    assert ok(is_capital_project=True, capital_project_number="098765432") == 422  # needs capital task / cost center
    assert ok(is_capital_project=True, capital_project_number="098765432", capital_cost_center="12a4") == 422
    assert ok(is_capital_project=True, capital_project_number="098765432", capital_cost_center="0520") == 201
    # Legal / procurement review: required at $250,000 and above
    assert ok(amount=250000) == 422
    assert ok(amount=250000, legal_review=False) == 201
    assert ok(amount=249999.99) == 201
    signed = [("agreement_files", ("signed.pdf", PDF, "application/pdf"))]
    assert ok(agreement_signed=True, files=signed) == 422  # needs signature date
    assert ok(agreement_signed=True, signature_date=str(date.today() + timedelta(days=1)), files=signed) == 422
    assert ok(agreement_signed=True, signature_date=str(date.today())) == 422  # signed but no agreement file
    assert ok(agreement_signed=True, signature_date=str(date.today()), files=signed) == 201
    assert ok(is_urgent=True) == 422
    assert ok(is_urgent=True, urgent_reason="Not a reason") == 422
    assert ok(is_urgent=True, urgent_reason="Commercial Deadline") == 201
    # Dates, amount, approver, recipients
    assert ok(end_date="2025-12-31") == 422
    assert ok(start_date=None) == 422
    assert ok(amount=0) == 422
    assert ok(approver_email="someone.else@ges.com") == 422
    assert ok(po_recipients=["not-an-email"]) == 422
    # Non-Oracle approver: typed in, must be a valid email, not checked against the list
    assert ok(approver_not_listed=True, approver_email="not-an-email") == 422
    other = submit(client, ALICE, approver_not_listed=True, approver_email="finance.lead@ges.com").json()
    assert other["approver_email"] == "finance.lead@ges.com" and other["approver_name"] == "Non-Oracle approver"
    assert ok(supplier_name="") == 422


def test_hidden_answers_are_cleared(client):
    body = submit(client, ALICE, is_po_extension=False, previous_po="PO999", agreement_signed=False,
                  signature_date="2026-01-01", is_urgent=False, urgent_reason="Commercial Deadline",
                  is_capital_project=False, capital_cost_center="0520", amount=1000, legal_review=True).json()
    assert body["previous_po"] is None and body["signature_date"] is None and body["urgent_reason"] is None
    assert body["capital_cost_center"] is None and body["legal_review"] is None


def test_requesters_only_see_their_own(client):
    number = submit(client, BOB).json()["request_number"]
    assert client.get(f"/api/requests/{number}", headers=ALICE).status_code == 404
    assert client.get(f"/api/requests/{number}", headers=BOB).status_code == 200
    assert client.get(f"/api/requests/{number}", headers=ADMIN).status_code == 200
    alice_all = client.get("/api/requests?scope=all", headers=ALICE).json()  # non-admin "all" = mine
    assert alice_all and all(r["requester_email"] == "alice@ges.com" for r in alice_all)
    admin_all = {r["requester_email"] for r in client.get("/api/requests?scope=all", headers=ADMIN).json()}
    assert {"alice@ges.com", "bob@ges.com"} <= admin_all


def test_admin_actions(client):
    number = submit(client, ALICE).json()["request_number"]
    url = f"/api/requests/{number}"
    assert client.patch(f"{url}/status", headers=ALICE, json={"status": "Closed"}).status_code == 403
    assert client.patch(f"{url}/status", headers=ADMIN, json={"status": "Bogus"}).status_code == 422
    r = client.patch(f"{url}/status", headers=ADMIN, json={"status": "Info Needed", "note": "Need W-9"}).json()
    assert r["status"] == "Info Needed" and r["history"][-1]["note"] == "Need W-9"
    notes = client.put(f"{url}/notes", headers=ADMIN, json={"admin_notes": "Called vendor"}).json()
    assert notes["admin_notes"] == "Called vendor"
    assert client.post(f"{url}/resend-email", headers=ADMIN).json()["email_status"] == "Sent"
    assert client.get("/api/requests/export.csv", headers=ALICE).status_code == 403
    assert number in client.get("/api/requests/export.csv", headers=ADMIN).text


def test_spa_routing(client):
    assert client.get("/requests/PR-2026-0001").text == "<html>spa</html>"
    missing_api = client.get("/api/does-not-exist", headers=ALICE)
    assert missing_api.status_code == 404 and missing_api.headers["content-type"].startswith("application/json")
    assert client.get("/..%2F..%2Fapp%2Fconfig.py").text == "<html>spa</html>"


def test_admin_by_group_claim(client, monkeypatch):
    from app.config import get_settings
    from conftest import principal
    import base64, json
    monkeypatch.setattr(get_settings(), "azure_ad_admin_group_id", "grp-123")
    claims = [{"typ": "preferred_username", "val": "g@ges.com"}, {"typ": "groups", "val": "grp-123"}]
    hdr = {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps({"claims": claims}).encode()).decode()}
    assert client.get("/api/me", headers=hdr).json()["is_admin"] is True
    assert client.get("/api/me", headers=principal("x@ges.com")).json()["is_admin"] is False



def test_word_and_excel_attachments(client):
    ole, zipped = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", b"PK\x03\x04rest"
    files = [("supporting_files", ("quote.xls", ole, "application/vnd.ms-excel")), ("supporting_files", ("sow.doc", ole, "application/msword")),
             ("supporting_files", ("order.xlsx", zipped, "application/octet-stream")), ("supporting_files", ("sow.docx", zipped, "application/octet-stream"))]
    body = submit(client, ALICE, files=files).json()
    assert [a["file_name"] for a in body["attachments"]] == ["quote.xls", "sow.doc", "order.xlsx", "sow.docx"]
    dl = client.get(f"/api/requests/{body['request_number']}/attachments/{body['attachments'][0]['id']}", headers=ALICE)
    assert dl.content == ole and dl.headers["content-type"] == "application/vnd.ms-excel"
    assert dl.headers["content-disposition"].startswith("attachment;")


def test_admin_manages_email_recipients(client, monkeypatch):
    from app.services import email
    sent = {}
    monkeypatch.setattr(email, "send_submission_email", lambda r, to=None, cc=None: sent.update(to=to, cc=cc))
    assert client.get("/api/admin/recipients", headers=ALICE).status_code == 403
    assert client.get("/api/admin/recipients", headers=ADMIN).json()["source"] == "app setting"
    url = "/api/admin/recipients"
    assert client.put(url, headers=ADMIN, json={"to": "", "cc": ""}).status_code == 422  # To is required
    assert client.put(url, headers=ADMIN, json={"to": "a@ges.com, not-an-email", "cc": ""}).status_code == 422
    saved = client.put(url, headers=ADMIN, json={"to": "po@ges.com, mary@ges.com; heather@ges.com", "cc": "boss@ges.com",
                                                 "high_value_cc": "cio@ges.com, Boss@ges.com"}).json()
    assert saved["to"] == "po@ges.com, mary@ges.com, heather@ges.com" and saved["source"] == "admin"
    assert saved["high_value_cc"] == "cio@ges.com, Boss@ges.com"
    assert saved["updated_by"] == "po.admin@ges.com"
    submit(client, ALICE)
    assert sent == {"to": ["po@ges.com", "mary@ges.com", "heather@ges.com"], "cc": ["boss@ges.com"]}
    submit(client, ALICE, amount=250000, legal_review=True)  # high value: CC added, no duplicates
    assert sent["cc"] == ["boss@ges.com", "cio@ges.com"]


def test_agreement_and_supporting_sections(client):
    f = lambda field, name: (field, (name, PDF, "application/pdf"))  # noqa: E731
    body = submit(client, ALICE, files=[f("agreement_files", "signed.pdf"), f("supporting_files", "quote.pdf")],
                  agreement_signed=True, signature_date="2026-01-15").json()
    assert [(a["file_name"], a["category"]) for a in body["attachments"]] == [("signed.pdf", "agreement"), ("quote.pdf", "supporting")]
    # Not signed: the agreement section is optional, but at least one document is still required
    assert submit(client, ALICE, files=[f("agreement_files", "draft.pdf")]).status_code == 201
    too_many = [f("agreement_files", f"a{i}.pdf") for i in range(4)]
    assert submit(client, ALICE, files=too_many).status_code == 422


def test_purchasing_issues_final_po(client, monkeypatch):
    from app.services import email
    sent = []
    monkeypatch.setattr(email, "send_po_email", lambda r, note=None: sent.append((r.po_number, note)))
    n = submit(client, ALICE, po_recipients=["ap@ges.com"]).json()["request_number"]
    url = f"/api/requests/{n}/po"
    po = lambda **kw: {"file": kw.pop("file", ("PO-4500123.pdf", PDF, "application/pdf"))}  # noqa: E731
    data = {"po_number": "4500123", "note": "Send invoices to AP."}
    # Status can't be set to PO Issued by hand, before a PO exists
    assert client.patch(f"/api/requests/{n}/status", headers=ADMIN, json={"status": "PO Issued"}).status_code == 422
    assert client.post(url, headers=ALICE, data=data, files=po()).status_code == 403
    assert client.post(url, headers=ADMIN, data=data, files=po(file=("po.docx", b"PK\x03\x04", "application/msword"))).status_code == 422
    assert client.post(url, headers=ADMIN, data={"po_number": ""}, files=po()).status_code == 422
    r = client.post(url, headers=ADMIN, data=data, files=po())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PO Issued" and body["po_number"] == "4500123" and body["po_email_status"] == "Sent"
    assert body["po_issued_by"] == "po.admin@ges.com" and body["history"][-1]["new_status"] == "PO Issued"
    assert sent == [("4500123", "Send invoices to AP.")]
    # The requester sees and can open the PO
    mine = client.get(f"/api/requests/{n}", headers=ALICE).json()
    att = next(a for a in mine["attachments"] if a["category"] == "po")
    assert client.get(f"/api/requests/{n}/attachments/{att['id']}", headers=ALICE).content.startswith(b"%PDF-")
    # Only one PO per requisition; resend works
    assert client.post(url, headers=ADMIN, data=data, files=po()).status_code == 409
    assert client.post(f"{url}/resend-email", headers=ADMIN).json()["po_email_status"] == "Sent"
    assert len(sent) == 2


def test_local_files_copied_to_blob_once(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.services import storage
    s = storage.get_settings()
    monkeypatch.setattr(s, "local_files_dir", str(tmp_path))
    monkeypatch.setattr(s, "blob_account_url", "https://example.blob.core.windows.net/")
    (tmp_path / "attachments" / "PR-1").mkdir(parents=True)
    (tmp_path / "attachments" / "PR-1" / "a.pdf").write_bytes(PDF)
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "db.db").write_bytes(b"x")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "ignored.txt").write_bytes(b"x")
    blobs = {}

    def client(container, name):
        return SimpleNamespace(exists=lambda: (container, name) in blobs,
                               upload_blob=lambda data, **kw: blobs.__setitem__((container, name), data))
    monkeypatch.setattr(storage, "_blob_service", lambda: SimpleNamespace(get_blob_client=client))
    assert storage.copy_local_to_blob() == 2
    assert blobs == {("attachments", "PR-1/a.pdf"): PDF, ("backups", "db.db"): b"x"}
    assert storage.copy_local_to_blob() == 0  # already there: nothing uploaded again
