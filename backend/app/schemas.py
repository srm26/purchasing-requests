"""API request/response shapes."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from .models import HIGH_VALUE_AMOUNT, URGENT_REASONS

UrgentReason = Literal[tuple(URGENT_REASONS)]  # type: ignore[valid-type]


class RequestIn(BaseModel):
    """Form fields. Sent as a JSON string in the multipart 'payload' part next to the PDF files."""

    approver_email: EmailStr
    approver_not_listed: bool = False  # True: a typed-in approver who isn't in the list, not checked against the list
    po_recipients: list[EmailStr] = Field(default=[], max_length=10)

    is_po_extension: bool
    previous_po: str | None = Field(default=None, max_length=50)
    is_capital_project: bool
    capital_project_number: str | None = Field(default=None, pattern=r"^\d{9}$")
    project_number: str = Field(pattern=r"^\d{9}$")
    cost_center: str = Field(pattern=r"^\d{4}$")
    capital_cost_center: str | None = Field(default=None, pattern=r"^\d{4}$")

    supplier_name: str = Field(min_length=2, max_length=200)
    start_date: date
    end_date: date
    agreement_signed: bool
    signature_date: date | None = None
    amount: float = Field(gt=0, lt=1_000_000_000)
    legal_review: bool | None = None

    is_urgent: bool = False
    urgent_reason: UrgentReason | None = None
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _rules(self):
        # Clear answers that don't apply, so stored data matches what the form showed.
        if not self.is_po_extension:
            self.previous_po = None
        elif not (self.previous_po or "").strip():
            raise ValueError("Previous PO is required for a PO extension / add-on")
        if not self.is_capital_project:
            self.capital_project_number = self.capital_cost_center = None
        elif not (self.capital_project_number or "").strip():
            raise ValueError("Capital project number is required for a capital project")
        elif not self.capital_cost_center:
            raise ValueError("Capital task / cost center is required for a capital project")
        if self.end_date < self.start_date:
            raise ValueError("End date is before start date")
        if not self.agreement_signed:
            self.signature_date = None
        elif not self.signature_date:
            raise ValueError("Date of signature is required when the agreement is signed")
        elif self.signature_date > date.today():
            raise ValueError("Date of signature can't be in the future")
        if self.amount < HIGH_VALUE_AMOUNT:
            self.legal_review = None
        elif self.legal_review is None:
            raise ValueError("Legal / procurement review is required for $250,000 or more")
        if not self.is_urgent:
            self.urgent_reason = None
        elif not self.urgent_reason:
            raise ValueError("Choose a reason for the urgent request")
        return self


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_name: str
    size_bytes: int
    category: str
    uploaded_at: datetime


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    old_status: str | None
    new_status: str
    changed_by: str
    changed_at: datetime
    note: str | None


class RequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    request_number: str
    submitted_at: datetime
    requester_name: str
    requester_email: str
    supplier_name: str
    project_number: str
    cost_center: str
    is_capital_project: bool
    amount: float
    start_date: date
    is_urgent: bool
    urgent_reason: str | None
    status: str
    email_status: str
    po_number: str | None


class RequestDetail(RequestSummary):
    approver_email: str
    approver_name: str
    po_recipients: str | None
    is_po_extension: bool
    previous_po: str | None
    capital_project_number: str | None
    capital_cost_center: str | None
    legal_review: bool | None
    end_date: date
    agreement_signed: bool
    signature_date: date | None
    description: str | None
    email_error: str | None
    email_sent_at: datetime | None
    admin_notes: str | None
    po_issued_at: datetime | None
    po_issued_by: str | None
    po_email_status: str | None
    po_email_error: str | None
    po_email_sent_at: datetime | None
    updated_at: datetime
    attachments: list[AttachmentOut]
    history: list[HistoryOut]


class StatusUpdate(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


class RecipientsUpdate(BaseModel):
    """Comma-separated email lists for the submission email. high_value_cc is added to CC at or above HIGH_VALUE_AMOUNT."""
    to: list[EmailStr] = Field(min_length=1, max_length=20)
    cc: list[EmailStr] = Field(default=[], max_length=20)
    high_value_cc: list[EmailStr] = Field(default=[], max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _split(cls, data):
        from .services.recipients import split
        if isinstance(data, dict):
            data = {k: split(v) if isinstance(v, str) else v for k, v in data.items()}
        return data


class NotesUpdate(BaseModel):
    admin_notes: str = Field(max_length=5000)
