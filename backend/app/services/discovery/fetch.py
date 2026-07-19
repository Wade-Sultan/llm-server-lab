from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Spec pages fit comfortably; the cap only guards against pathological pages.
_MAX_MARKDOWN_CHARS = 30_000
_MAX_PDF_PAGES = 4
_PDF_RENDER_SCALE = 2.0


@dataclass
class FetchedDoc:
    url: str
    kind: Literal["markdown", "pdf_images"]
    text: str | None = None
    images: list[str] | None = None  # data:image/png;base64,... URLs


def _extract_markdown(html: str) -> str | None:
    import trafilatura  # lazy: discovery job only

    # include_tables is load-bearing — TechPowerUp and manufacturer spec pages
    # keep the numbers we want in tables.
    return trafilatura.extract(html, output_format="markdown", include_tables=True)


def _rasterize_pdf(data: bytes) -> list[str]:
    import pypdfium2 as pdfium  # lazy: discovery job only

    pdf = pdfium.PdfDocument(data)
    images: list[str] = []
    try:
        for i in range(min(len(pdf), _MAX_PDF_PAGES)):
            bitmap = pdf[i].render(scale=_PDF_RENDER_SCALE)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            images.append(f"data:image/png;base64,{b64}")
    finally:
        pdf.close()
    return images


async def fetch_document(url: str) -> FetchedDoc | None:
    """Fetch a source page as extraction input: markdown for HTML, rasterized
    page images for PDF spec sheets. Returns None on any failure — the caller
    skips the source rather than failing the run."""
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "").lower()
        is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf")

        if is_pdf:
            images = await asyncio.to_thread(_rasterize_pdf, resp.content)
            if not images:
                return None
            return FetchedDoc(url=url, kind="pdf_images", images=images)

        markdown = await asyncio.to_thread(_extract_markdown, resp.text)
        if not markdown:
            return None
        return FetchedDoc(url=url, kind="markdown", text=markdown[:_MAX_MARKDOWN_CHARS])
    except Exception:
        logger.warning("discovery: failed to fetch %s", url, exc_info=True)
        return None
