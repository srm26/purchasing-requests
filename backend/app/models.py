"""Database tables. Column names stay close to a future Ironclad intake mapping."""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATUSES = ["Pending Review", "In Review", "Info Needed", "PO/Req Created", "PO Issued", "Contract Signed", "Closed", "Rejected"]
OPEN_STATUSES = ["Pending Review", "In Review", "Info Needed"]
EMAIL_STATUSES = ["Pending", "Sent", "Failed"]
URGENT_REASONS = ["Late PO Request", "Commercial Deadline", "Past Due Payment(s)", "Project / Service Deadline",
                  "Business Critical/Continuity"]
NOT_LISTED_APPROVER = "Not in the list"
HIGH_VALUE_AMOUNT = 250_000  # USD; at or above this, legal/procurement review is asked and high-value CCs are notified


class PurchaseRequest(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # PR-2026-0001
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    # Stakeholders
    requester_email: Mapped[str] = mapped_column(String(320), index=True)
    requester_name: Mapped[str] = mapped_column(String(200))
    approver_email: Mapped[str] = mapped_column(String(320))
    approver_name: Mapped[str] = mapped_column(String(200))  # NOT_LISTED_APPROVER when typed in, not from the list
    po_recipients: Mapped[str | None] = mapped_column(Text)  # comma-separated emails

    # Project costing. Numbers are text so leading zeroes survive.
    is_po_extension: Mapped[bool] = mapped_column(Boolean)
    previous_po: Mapped[str | None] = mapped_column(String(50))
    is_capital_project: Mapped[bool] = mapped_column(Boolean)
    capital_project_number: Mapped[str | None] = mapped_column(String(50))
    project_number: Mapped[str] = mapped_column(String(9), index=True)
    cost_center: Mapped[str] = mapped_column(String(4))  # Task / Cost Center
    capital_cost_center: Mapped[str | None] = mapped_column(String(4))  # Capital Task / Cost Center

    # Supplier requisition detail
    supplier_name: Mapped[str] = mapped_column(String(200), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    agreement_signed: Mapped[bool] = mapped_column(Boolean)
    signature_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)  # USD
    legal_review: Mapped[bool | None] = mapped_column(Boolean)  # asked only at or above HIGH_VALUE_AMOUNT

    # Urgency + notes
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    urgent_reason: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(30), default="Pending Review", index=True)
    email_status: Mapped[str] = mapped_column(String(10), default="Pending")
    email_error: Mapped[str | None] = mapped_column(Text)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_notes: Mapped[str | None] = mapped_column(Text)

    # Final PO, issued by Purchasing in the portal. One per requisition; change orders are new submissions.
    po_number: Mapped[str | None] = mapped_column(String(50))
    po_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    po_issued_by: Mapped[str | None] = mapped_column(String(320))
    po_email_status: Mapped[str | None] = mapped_column(String(10))  # Pending | Sent | Failed, once a PO is issued
    po_email_error: Mapped[str | None] = mapped_column(Text)
    po_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="Attachment.id"
    )
    history: Mapped[list["StatusHistory"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="StatusHistory.id"
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    blob_path: Mapped[str] = mapped_column(String(400))
    size_bytes: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20), default="supporting")  # agreement | supporting | po
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    request: Mapped[PurchaseRequest] = relationship(back_populates="attachments")


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), index=True)
    old_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30))
    changed_by: Mapped[str] = mapped_column(String(320))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str | None] = mapped_column(Text)

    request: Mapped[PurchaseRequest] = relationship(back_populates="history")


class LookupValue(Base):
    """Lists of values loaded from the SharePoint workbook (Oracle later)."""

    __tablename__ = "lookup_values"
    __table_args__ = (UniqueConstraint("list_name", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_name: Mapped[str] = mapped_column(String(50), index=True)
    code: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(300))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class LookupRefresh(Base):
    """One row per refresh attempt, so admins can see when lists last loaded and why a load failed."""

    __tablename__ = "lookup_refreshes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(50))  # sharepoint | local
    ok: Mapped[bool] = mapped_column(Boolean)
    message: Mapped[str] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(320))


class AppSetting(Base):
    """Settings Purchasing admins change in the portal (e.g. email recipients), so no redeploy is needed."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(320))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
