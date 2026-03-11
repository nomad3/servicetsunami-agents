"""Report generation endpoints — Excel practice-performance reports.

Two entry points:
- POST /generate  — JWT-authenticated (web UI)
- POST /internal/generate — header-authenticated (ADK → API callbacks)
- GET  /download/{file_id} — serves the generated .xlsx file
"""

import logging
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.api import deps
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPORTS_DIR = Path("/tmp/servicetsunami_reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Pydantic schemas — matches ADK report_tools schema
# ---------------------------------------------------------------------------


class ProductionData(BaseModel):
    doctor: Optional[float] = None
    specialty: Optional[float] = None
    hygiene: Optional[float] = None
    total: Optional[float] = None
    net_production: Optional[float] = None
    collections: Optional[float] = None


class ProviderData(BaseModel):
    name: str
    role: str = "staff"
    visits: Optional[int] = None
    gross_production: Optional[float] = None
    collections: Optional[float] = None
    production_per_visit: Optional[float] = None
    treatment_presented: Optional[float] = None
    treatment_accepted: Optional[float] = None
    acceptance_rate: Optional[float] = None


class HygieneData(BaseModel):
    visits: Optional[int] = None
    capacity: Optional[int] = None
    capacity_pct: Optional[float] = None
    reappointment_rate: Optional[float] = None
    net_production: Optional[float] = None


class ReportRequest(BaseModel):
    practice_name: str = "Practice"
    report_period: str = ""
    production: Optional[ProductionData] = None
    providers: List[ProviderData] = Field(default_factory=list)
    hygiene: Optional[HygieneData] = None
    report_title: Optional[str] = None


class ReportResponse(BaseModel):
    file_id: str
    download_url: str
    filename: str
    expires_at: str


# ---------------------------------------------------------------------------
# Excel styling
# ---------------------------------------------------------------------------
TITLE_FONT = Font(name="Calibri", size=14, bold=True)
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
SUBHEADER_FONT = Font(name="Calibri", size=11, bold=True)
SUBHEADER_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
DATA_FONT = Font(name="Calibri", size=10)

FMT_CURRENCY = '$#,##0.00'
FMT_PERCENT = '0.0%'
FMT_NUMBER = '#,##0'


def _header_row(ws, row, values, col_start=1):
    for idx, val in enumerate(values, start=col_start):
        cell = ws.cell(row=row, column=idx, value=val)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _subheader_row(ws, row, values, col_start=1):
    for idx, val in enumerate(values, start=col_start):
        cell = ws.cell(row=row, column=idx, value=val)
        cell.font = SUBHEADER_FONT
        cell.fill = SUBHEADER_FILL


def _write_row(ws, row, label, value, fmt=None, bold=False):
    c1 = ws.cell(row=row, column=1, value=label)
    c1.font = Font(name="Calibri", size=10, bold=bold)
    c2 = ws.cell(row=row, column=2, value=value)
    c2.font = Font(name="Calibri", size=10, bold=bold)
    if fmt and value is not None:
        c2.number_format = fmt
    c2.alignment = Alignment(horizontal="right")
    return row + 1


# ---------------------------------------------------------------------------
# Excel builder
# ---------------------------------------------------------------------------

def _build_excel(data: ReportRequest) -> tuple:
    """Build a formatted Excel workbook. Returns (workbook, file_id)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Operations Report"

    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 20

    row = 1

    # ── Title ──
    title = data.report_title or f"{data.practice_name} — Monthly Operations Report"
    ws.cell(row=row, column=1, value=title).font = TITLE_FONT
    row += 1
    if data.report_period:
        ws.cell(row=row, column=1, value=f"Period: {data.report_period}").font = DATA_FONT
    row += 2

    role_order = ["doctor", "specialist", "hygienist", "staff"]
    doctors = [p for p in data.providers if p.role == "doctor"]
    specialists = [p for p in data.providers if p.role == "specialist"]
    hygienists = [p for p in data.providers if p.role == "hygienist"]

    # ── PRODUCTION & COLLECTIONS ──
    prod = data.production
    if prod:
        _header_row(ws, row, ["PRODUCTION & COLLECTIONS", ""])
        row += 1
        row = _write_row(ws, row, "Gross Production", None, bold=True)
        row = _write_row(ws, row, "  Doctor", prod.doctor, FMT_CURRENCY)
        row = _write_row(ws, row, "  Specialty", prod.specialty, FMT_CURRENCY)
        row = _write_row(ws, row, "  Hygiene", prod.hygiene, FMT_CURRENCY)
        row = _write_row(ws, row, "  Total", prod.total, FMT_CURRENCY, bold=True)
        row += 1
        row = _write_row(ws, row, "Net Production (Revenue)", prod.net_production, FMT_CURRENCY, bold=True)
        row = _write_row(ws, row, "Collections", prod.collections, FMT_CURRENCY, bold=True)
        if prod.net_production and prod.collections:
            pct = prod.collections / prod.net_production
            row = _write_row(ws, row, "  % Net Production", pct, FMT_PERCENT)
        row += 1

    # ── PATIENT VISITS ──
    if any(p.visits for p in data.providers):
        _header_row(ws, row, ["PATIENT VISITS", ""])
        row += 1

        if doctors:
            row = _write_row(ws, row, "Doctors", None, bold=True)
            total_doc_visits = 0
            for p in doctors:
                row = _write_row(ws, row, f"  {p.name}", p.visits, FMT_NUMBER)
                total_doc_visits += p.visits or 0
            row = _write_row(ws, row, "  Total Doctors", total_doc_visits, FMT_NUMBER, bold=True)

        if specialists:
            row = _write_row(ws, row, "Specialists", None, bold=True)
            for p in specialists:
                row = _write_row(ws, row, f"  {p.name}", p.visits, FMT_NUMBER)

        if hygienists:
            row = _write_row(ws, row, "Hygienists", None, bold=True)
            for p in hygienists:
                row = _write_row(ws, row, f"  {p.name}", p.visits, FMT_NUMBER)

        all_visits = sum(p.visits or 0 for p in data.providers)
        row = _write_row(ws, row, "Total", all_visits, FMT_NUMBER, bold=True)
        row += 1

    # ── PRODUCTION BY PROVIDER ──
    providers_with_prod = [p for p in data.providers if p.gross_production]
    if providers_with_prod:
        _header_row(ws, row, ["PRODUCTION BY PROVIDER", ""])
        row += 1
        for role in role_order:
            role_providers = [p for p in providers_with_prod if p.role == role]
            if role_providers:
                role_label = role.capitalize() + "s"
                _subheader_row(ws, row, [role_label, ""])
                row += 1
                for p in role_providers:
                    row = _write_row(ws, row, f"  {p.name}", p.gross_production, FMT_CURRENCY)
                role_total = sum(p.gross_production or 0 for p in role_providers)
                row = _write_row(ws, row, f"  Subtotal {role_label}", role_total, FMT_CURRENCY, bold=True)
        total_gp = sum(p.gross_production or 0 for p in providers_with_prod)
        row = _write_row(ws, row, "Total Production", total_gp, FMT_CURRENCY, bold=True)
        row += 1

    # ── COLLECTIONS BY PROVIDER ──
    providers_with_coll = [p for p in data.providers if p.collections]
    if providers_with_coll:
        _header_row(ws, row, ["COLLECTIONS BY PROVIDER", ""])
        row += 1
        for role in role_order:
            role_providers = [p for p in providers_with_coll if p.role == role]
            if role_providers:
                role_label = role.capitalize() + "s"
                _subheader_row(ws, row, [role_label, ""])
                row += 1
                for p in role_providers:
                    row = _write_row(ws, row, f"  {p.name}", p.collections, FMT_CURRENCY)
                role_total = sum(p.collections or 0 for p in role_providers)
                row = _write_row(ws, row, f"  Subtotal {role_label}", role_total, FMT_CURRENCY, bold=True)
        total_coll = sum(p.collections or 0 for p in providers_with_coll)
        row = _write_row(ws, row, "Total Collections", total_coll, FMT_CURRENCY, bold=True)
        row += 1

    # ── PRODUCTION PER VISIT ──
    if any(p.production_per_visit for p in data.providers):
        _header_row(ws, row, ["PRODUCTION PER VISIT", ""])
        row += 1
        for p in data.providers:
            if p.production_per_visit:
                row = _write_row(ws, row, f"  {p.name}", p.production_per_visit, FMT_CURRENCY)
        row += 1

    # ── CASE ACCEPTANCE ──
    providers_with_tx = [p for p in data.providers if p.treatment_presented]
    if providers_with_tx:
        _header_row(ws, row, ["CASE ACCEPTANCE", ""])
        row += 1
        total_presented = 0
        total_accepted = 0
        for p in providers_with_tx:
            row = _write_row(ws, row, p.name, None, bold=True)
            row = _write_row(ws, row, "  Treatment Presented", p.treatment_presented, FMT_CURRENCY)
            row = _write_row(ws, row, "  Treatment Accepted", p.treatment_accepted, FMT_CURRENCY)
            row = _write_row(ws, row, "  Acceptance Rate", p.acceptance_rate, FMT_PERCENT)
            total_presented += p.treatment_presented or 0
            total_accepted += p.treatment_accepted or 0
        row += 1
        row = _write_row(ws, row, "Total Presented", total_presented, FMT_CURRENCY, bold=True)
        row = _write_row(ws, row, "Total Accepted", total_accepted, FMT_CURRENCY, bold=True)
        if total_presented > 0:
            row = _write_row(ws, row, "Overall Acceptance Rate", total_accepted / total_presented, FMT_PERCENT, bold=True)
        row += 1

    # ── RECARE ──
    hyg = data.hygiene
    if hyg:
        _header_row(ws, row, ["RECARE", ""])
        row += 1
        row = _write_row(ws, row, "Capacity Utilization", None, bold=True)
        row = _write_row(ws, row, "  Hygiene Visits", hyg.visits, FMT_NUMBER)
        row = _write_row(ws, row, "  Hygiene Capacity", hyg.capacity, FMT_NUMBER)
        row = _write_row(ws, row, "  % Capacity", hyg.capacity_pct, FMT_PERCENT)
        row += 1
        row = _write_row(ws, row, "Reappointment Rate", hyg.reappointment_rate, FMT_PERCENT)
        row = _write_row(ws, row, "Hygiene Net Production", hyg.net_production, FMT_CURRENCY)

    file_id = str(uuid.uuid4())
    return wb, file_id


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup_expired_reports():
    """Remove report files older than REPORT_TTL_HOURS."""
    if not REPORTS_DIR.exists():
        return
    cutoff = time.time() - (REPORT_TTL_HOURS * 3600)
    for fpath in REPORTS_DIR.iterdir():
        if fpath.is_file() and fpath.stat().st_mtime < cutoff:
            try:
                fpath.unlink()
                logger.info("Removed expired report: %s", fpath.name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=ReportResponse)
def generate_report(
    payload: ReportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Generate an Excel practice-performance report (JWT-authenticated)."""
    tenant_id = str(current_user.tenant_id)
    return _do_generate(payload, tenant_id)


@router.post("/internal/generate", response_model=ReportResponse)
def generate_report_internal(
    payload: ReportRequest,
    x_tenant_id: str = Header(...),
):
    """Generate an Excel report — internal endpoint for ADK callbacks (no JWT)."""
    return _do_generate(payload, x_tenant_id)


@router.get("/download/{file_id}")
def download_report(
    file_id: str,
    tenant_id: str = Query(...),
):
    """Serve a previously generated Excel report for download."""
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="Invalid file_id")
    if ".." in tenant_id or "/" in tenant_id or "\\" in tenant_id:
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    file_path = REPORTS_DIR / f"{tenant_id}_{file_id}.xlsx"

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found or expired")

    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"report_{file_id}.xlsx",
    )


# ---------------------------------------------------------------------------
# Shared logic
# ---------------------------------------------------------------------------

def _do_generate(payload: ReportRequest, tenant_id: str) -> dict:
    """Build the Excel file, persist it, return metadata."""
    _cleanup_expired_reports()

    wb, file_id = _build_excel(payload)

    file_path = REPORTS_DIR / f"{tenant_id}_{file_id}.xlsx"
    wb.save(str(file_path))
    logger.info("Generated report %s for tenant %s", file_id, tenant_id)

    expires_at = (datetime.utcnow() + timedelta(hours=REPORT_TTL_HOURS)).isoformat()
    safe_name = payload.practice_name.replace(" ", "_")
    filename = f"{safe_name}_Operations_Report_{payload.report_period.replace(' ', '_')}.xlsx"
    download_url = f"/api/v1/reports/download/{file_id}?tenant_id={tenant_id}"

    return {
        "file_id": file_id,
        "download_url": download_url,
        "filename": filename,
        "expires_at": expires_at,
    }
