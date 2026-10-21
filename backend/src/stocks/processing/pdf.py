"""Extract located page text from a stored PDF original.

A PDF is evidence like any other Document: this module only reads the text the
file actually contains and keeps the page it came from, so a Chunk can point
back at ``page N``. Nothing is inferred from images or layout: a file without a
usable text layer fails its parse attempt instead of becoming an empty body, and
table structure is not claimed because the extractor does not reconstruct it.

Errors are raised as :class:`PdfError` with a stable code; the parse stage turns
them into the persisted batch failure.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

MAX_PDF_PAGES = 2_000
MAX_PDF_CHARACTERS = 8_000_000


class PdfError(ValueError):
    """One stored PDF could not be turned into located page text."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


def pdf_pages(payload: bytes) -> list[tuple[str, dict]]:
    """One ``(page_text, locator)`` entry per page that contains text."""

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(payload), strict=False)
    except PdfReadError as exc:
        raise PdfError(
            "PDF_DAMAGED",
            "Stored PDF could not be read",
        ) from exc
    if reader.is_encrypted:
        # An empty password decrypts documents that only restrict editing; a
        # document that still needs a real password cannot be read as evidence.
        try:
            opened = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001 - any decryption defect
            raise PdfError(
                "PDF_ENCRYPTED",
                "Stored PDF is encrypted and cannot be read",
            ) from exc
        if not opened:
            raise PdfError(
                "PDF_ENCRYPTED",
                "Stored PDF is encrypted and cannot be read",
            )
    try:
        pages = list(reader.pages)
    except PdfReadError as exc:
        raise PdfError("PDF_DAMAGED", "Stored PDF could not be read") from exc
    page_count = len(pages)
    if page_count > MAX_PDF_PAGES:
        raise PdfError(
            "PDF_PAGE_LIMIT",
            "Stored PDF has more pages than the parser allows",
            details={"limit_pages": MAX_PDF_PAGES, "page_count": page_count},
        )
    output: list[tuple[str, dict]] = []
    characters = 0
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - extractor defect on one page
            raise PdfError(
                "PDF_DAMAGED",
                "Stored PDF page could not be read",
                details={"page": number},
            ) from exc
        text = text.replace("\x00", "")
        if not text.strip():
            continue
        characters += len(text)
        if characters > MAX_PDF_CHARACTERS:
            raise PdfError(
                "PDF_TEXT_TOO_LARGE",
                "Stored PDF has more text than the parser allows",
                details={"limit_characters": MAX_PDF_CHARACTERS},
            )
        output.append(
            (
                text,
                {
                    "kind": "pdf_page",
                    "page": number,
                    "page_count": page_count,
                    "coordinate_unit": "pdf_extracted_text_chars",
                },
            )
        )
    if not output:
        raise PdfError(
            "PDF_TEXT_UNAVAILABLE",
            "Stored PDF has no extractable text layer",
            details={"page_count": page_count},
        )
    return output
