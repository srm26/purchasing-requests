"""Lists of values: load the SharePoint workbook into lookup_values (Oracle later replaces only the reader).

A refresh validates the whole workbook first and replaces the tables in one transaction,
so a broken workbook never empties the form: the last good lists stay in place.
"""
import io
import logging
from pathlib import Path
from urllib.parse import quote, urlparse

from openpyxl import load_workbook
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LookupRefresh, LookupValue
from . import graph

log = logging.getLogger(__name__)

# list name (API) -> sheet name (workbook)
# Approvers: column A = email (the code), column B = display name.
SHEETS = {
    "approvers": "Approvers",
}


class WorkbookError(ValueError):
    pass


def _read_sharepoint() -> bytes:
    s = get_settings()
    u = urlparse(s.sp_site_url)
    site = graph.call("GET", f"/sites/{u.hostname}:{u.path.rstrip('/')}").json()
    path = quote(s.sp_lists_path.strip("/"))
    return graph.call("GET", f"/sites/{site['id']}/drive/root:/{path}:/content").content


def parse_workbook(data: bytes) -> dict[str, list[tuple[str, str]]]:
    """Return {list_name: [(code, label), ...]} for active rows. Raises WorkbookError on any problem."""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: dict[str, list[tuple[str, str]]] = {}
    for list_name, sheet in SHEETS.items():
        if sheet not in wb.sheetnames:
            raise WorkbookError(f"Missing sheet '{sheet}'")
        rows = list(wb[sheet].iter_rows(values_only=True))
        if not rows:
            raise WorkbookError(f"Sheet '{sheet}' is empty")
        header = [str(h or "").strip().lower() for h in rows[0]]
        active_col = header.index("active") if "active" in header else None
        values, seen = [], set()
        for n, row in enumerate(rows[1:], start=2):
            code = str(row[0]).strip() if row and row[0] is not None else ""
            if not code:
                continue
            if active_col is not None and str(row[active_col]).strip().lower() in ("n", "no", "false", "0", "inactive"):
                continue
            if code in seen:
                raise WorkbookError(f"Duplicate value '{code}' in sheet '{sheet}' (row {n})")
            seen.add(code)
            label = str(row[1]).strip() if len(row) > 1 and row[1] is not None and 1 != active_col else code
            values.append((code, label))
        if not values:
            raise WorkbookError(f"Sheet '{sheet}' has no active values")
        out[list_name] = values
    return out


def refresh(db: Session, triggered_by: str) -> LookupRefresh:
    s = get_settings()
    source = "sharepoint" if s.sharepoint_configured and not s.demo_mode else "local"
    try:
        if source == "local" and not s.use_local_services:
            raise WorkbookError("SharePoint is not configured (SP_SITE_URL / SP_LISTS_PATH app settings)")
        data = _read_sharepoint() if source == "sharepoint" else Path(s.local_lists_file).read_bytes()
        lists = parse_workbook(data)
    except Exception as exc:  # keep last good lists, record why
        log.exception("Lookup refresh failed")
        entry = LookupRefresh(source=source, ok=False, message=str(exc)[:1000], triggered_by=triggered_by)
        db.add(entry)
        db.commit()
        return entry

    db.execute(delete(LookupValue))
    for list_name, values in lists.items():
        db.add_all(LookupValue(list_name=list_name, code=c, label=l, sort_order=i) for i, (c, l) in enumerate(values))
    counts = ", ".join(f"{SHEETS[k]}: {len(v)}" for k, v in lists.items())
    entry = LookupRefresh(source=source, ok=True, message=f"Loaded {counts}", triggered_by=triggered_by)
    db.add(entry)
    db.commit()
    log.info("Lookup refresh ok: %s", counts)
    return entry


def get_lists(db: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in SHEETS}
    for v in db.scalars(select(LookupValue).order_by(LookupValue.list_name, LookupValue.sort_order)):
        out.setdefault(v.list_name, []).append({"code": v.code, "label": v.label})
    return out


def find(db: Session, list_name: str, code: str) -> LookupValue | None:
    # Case-insensitive: approver codes are emails, and EmailStr lowercases the domain (KMAGANTI@VIAD.COM -> KMAGANTI@viad.com).
    return db.scalar(select(LookupValue).where(LookupValue.list_name == list_name,
                                               func.lower(LookupValue.code) == code.lower()))
