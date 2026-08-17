"""Deterministic PDF rendering for a shared build.

Plain fpdf2 layout code over the stored snapshot — no LLM anywhere near it. The
same shared_builds row always renders the same document: content comes from the
frozen snapshot, and the embedded creation date is pinned to the row's
created_at rather than "now", so re-downloading a link does not produce a file
that merely looks identical.

Core-font limitation, embraced: helvetica covers latin-1 only, and part names
are ASCII in practice. `_latin1` replaces anything outside that rather than
crashing the export over an exotic dash.
"""

from __future__ import annotations

from datetime import datetime

from fpdf import FPDF

_MARGIN = 15
_PAGE_WIDTH = 210  # A4 portrait, mm
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN

# Column widths for the parts table, summing to the content width.
_COL_COMPONENT = 32
_COL_PART = 88
_COL_QTY = 12
_COL_UNIT = 24
_COL_TOTAL = 24

_ROW_H = 7


def _latin1(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def _usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render_build_pdf(build: dict, share_url: str, created_at: datetime) -> bytes:
    """Render one shared build snapshot as PDF bytes.

    `build` is the shared_builds.build payload: label, description,
    total_approx (cents) and parts rows shaped like RecommendedPart
    (component, brand, model, approx_price per unit in cents, quantity).
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_creation_date(created_at)
    pdf.set_title(_latin1(build.get("label") or "PC Build"))
    pdf.set_creator("Palladium")
    pdf.set_producer("Palladium")
    pdf.set_margins(_MARGIN, _MARGIN)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN)
    pdf.add_page()

    # Header
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(_CONTENT_WIDTH, 10, _latin1(build.get("label") or "PC Build"))
    pdf.ln(10)
    if description := build.get("description"):
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(_CONTENT_WIDTH, 5, _latin1(description))
        pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # Table header
    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(_COL_COMPONENT, _ROW_H, "Component", border=1, fill=True)
    pdf.cell(_COL_PART, _ROW_H, "Part", border=1, fill=True)
    pdf.cell(_COL_QTY, _ROW_H, "Qty", border=1, fill=True, align="C")
    pdf.cell(_COL_UNIT, _ROW_H, "Unit", border=1, fill=True, align="R")
    pdf.cell(_COL_TOTAL, _ROW_H, "Total", border=1, fill=True, align="R")
    pdf.ln(_ROW_H)

    pdf.set_font("helvetica", "", 9)
    for part in build.get("parts") or []:
        quantity = int(part.get("quantity") or 1)
        unit_cents = part.get("approx_price")
        name = " ".join(
            s for s in [part.get("brand") or "", part.get("model") or ""] if s
        )
        component = (part.get("component") or "").upper()
        pdf.cell(_COL_COMPONENT, _ROW_H, _latin1(component), border=1)
        pdf.cell(_COL_PART, _ROW_H, _latin1(name), border=1)
        pdf.cell(_COL_QTY, _ROW_H, str(quantity), border=1, align="C")
        pdf.cell(
            _COL_UNIT,
            _ROW_H,
            _usd(unit_cents) if unit_cents is not None else "-",
            border=1,
            align="R",
        )
        pdf.cell(
            _COL_TOTAL,
            _ROW_H,
            _usd(unit_cents * quantity) if unit_cents is not None else "-",
            border=1,
            align="R",
        )
        pdf.ln(_ROW_H)

    # Total row
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(
        _COL_COMPONENT + _COL_PART + _COL_QTY + _COL_UNIT,
        _ROW_H + 1,
        "Approximate total*",
        border=1,
        align="R",
    )
    pdf.cell(
        _COL_TOTAL,
        _ROW_H + 1,
        _usd(int(build.get("total_approx") or 0)),
        border=1,
        align="R",
    )
    pdf.ln(_ROW_H + 6)

    # Footnotes
    pdf.set_font("helvetica", "", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(
        _CONTENT_WIDTH,
        4,
        "*Based on approximate street prices derived from Google Shopping at the "
        "time this build was generated. Current prices may differ.",
    )
    pdf.ln(2)
    pdf.multi_cell(
        _CONTENT_WIDTH,
        4,
        _latin1(
            f"View this build online: {share_url}\n"
            f"Generated {created_at.strftime('%Y-%m-%d')} by Palladium."
        ),
    )

    return bytes(pdf.output())
