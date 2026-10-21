"""JSON, XML and PDF parsing: located values, preserved numbers, honest gaps."""
from __future__ import annotations

import json

import pytest

from stocks.processing.parser import ParseFailure, parse_content


def parse(payload: bytes, content_type: str):
    return parse_content(payload, content_type, chunk_size=400, chunk_overlap=40)


def _segments(chunk) -> list[dict]:
    return list(chunk.locator["segments"])


def test_json_response_keeps_pointer_locators_numbers_and_units():
    payload = json.dumps(
        {
            "series": {"id": "CPIAUCSL", "title": "CPI, 12-month change"},
            "observations": [
                {"period": "2026-08", "value": "2.4%", "a / b~c": 1.50},
                {"period": "2026-09", "value": "2.3%", "note": None},
            ],
            "flags": [True, False],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    _, chunks = parse(payload, "application/json")

    text = "\n".join(chunk.text for chunk in chunks)
    assert "series.id = CPIAUCSL" in text
    assert "observations[0].value = 2.4%" in text
    # A key that is not a plain path component is quoted for reading; the
    # pointer below stays the locator of record.
    assert 'observations[0]."a / b~c" = 1.5' in text
    assert "flags[1] = false" in text
    # A null value carries no evidence and is not turned into a line.
    assert "note" not in text
    locators = [
        segment["locator"] for chunk in chunks for segment in _segments(chunk)
    ]
    pointers = {locator["pointer"] for locator in locators}
    assert "/observations/0/value" in pointers
    # `/` and `~` are escaped the JSON Pointer way, never emitted raw.
    assert "/observations/0/a ~1 b~0c" in pointers
    assert all(locator["kind"] == "json_pointer" for locator in locators)


def test_json_number_text_keeps_the_source_scale():
    payload = b'{"amount": 1200.500, "count": 3, "currency": "USD"}'

    _, chunks = parse(payload, "application/json")

    text = "\n".join(chunk.text for chunk in chunks)
    assert "amount = 1200.500" in text
    assert "count = 3" in text
    assert "currency = USD" in text


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"a": 1,', "INVALID_JSON"),
        (b'{"a": 1, "a": 2}', "DUPLICATE_JSON_KEY"),
        (b'{"a": NaN}', "INVALID_JSON"),
    ],
)
def test_invalid_json_is_an_explicit_failure(payload, code):
    with pytest.raises(ParseFailure) as captured:
        parse(payload, "application/json")

    assert captured.value.code == code
    assert captured.value.status == "failed"


def test_json_media_type_suffix_is_parsed_as_json():
    payload = b'{"item": {"name": "Quarterly release"}}'

    _, chunks = parse(payload, "application/vnd.api+json")

    assert "item.name = Quarterly release" in chunks[0].text


def test_oversized_json_is_refused_instead_of_truncated():
    payload = json.dumps({f"key{index}": index for index in range(5_001)}).encode(
        "utf-8"
    )

    with pytest.raises(ParseFailure) as captured:
        parse(payload, "application/json")

    assert captured.value.code == "STRUCTURED_CONTENT_TOO_LARGE"
    assert captured.value.status == "failed"


def test_xml_response_keeps_element_paths_attributes_and_repeated_siblings():
    payload = b"""\
<?xml version="1.0" encoding="utf-8"?>
<report generated="2026-10-21">
  <title>Quarterly filing summary</title>
  <item form="10-Q">
    <value unit="USD">1200.50</value>
  </item>
  <item form="8-K">
    <value unit="USD">980.25</value>
  </item>
</report>
"""

    _, chunks = parse(payload, "application/xml")

    text = "\n".join(chunk.text for chunk in chunks)
    assert "report@generated = 2026-10-21" in text
    assert "/report/title = Quarterly filing summary" in text
    assert "/report/item[1]/value = 1200.50" in text
    assert "/report/item[2]@form = 8-K" in text
    locators = [
        segment["locator"] for chunk in chunks for segment in _segments(chunk)
    ]
    assert all(locator["kind"] == "xml_path" for locator in locators)
    assert {locator["path"] for locator in locators} >= {
        "/report/item[1]/value",
        "/report/item[2]/value",
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"<report><item></report>",
        (
            b'<!DOCTYPE report [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<report>&xxe;</report>"
        ),
    ],
)
def test_malformed_or_unsafe_xml_is_an_explicit_failure(payload):
    with pytest.raises(ParseFailure) as captured:
        parse(payload, "text/xml")

    assert captured.value.code == "INVALID_XML"
    assert captured.value.status == "failed"


def test_pdf_pages_keep_their_page_and_character_range():
    payload = minimal_pdf(
        [
            "Fiscal year 2026 revenue was 1234.5 million USD.",
            "Segment margin improved to 12.5 percent.",
        ]
    )

    _, chunks = parse(payload, "application/pdf")

    segments = [segment for chunk in chunks for segment in _segments(chunk)]
    assert [segment["locator"]["page"] for segment in segments] == [1, 2]
    assert all(segment["locator"]["page_count"] == 2 for segment in segments)
    for segment in segments:
        # Whitespace normalization keeps every visible character mapped back to
        # the page text it was extracted from.
        assert len(segment["locator"]["source_ranges"]) == len(segment["text"])
    text = "\n".join(chunk.text for chunk in chunks)
    assert "1234.5 million USD" in text
    assert "12.5 percent" in text


def test_pdf_without_a_text_layer_is_a_gap_not_an_empty_body():
    payload = minimal_pdf([None])

    with pytest.raises(ParseFailure) as captured:
        parse(payload, "application/pdf")

    assert captured.value.code == "PDF_TEXT_UNAVAILABLE"
    assert captured.value.status == "failed"


def test_damaged_pdf_bytes_fail_explicitly():
    with pytest.raises(ParseFailure) as captured:
        parse(b"%PDF-1.7 truncated", "application/pdf")

    assert captured.value.code == "PDF_DAMAGED"
    assert captured.value.status == "failed"


def test_unknown_content_type_stays_unsupported():
    with pytest.raises(ParseFailure) as captured:
        parse(b"\x89PNG\r\n\x1a\n", "image/png")

    assert captured.value.code == "UNSUPPORTED_CONTENT_TYPE"
    assert captured.value.status == "unsupported"


def minimal_pdf(pages: list[str | None]) -> bytes:
    """A real one-font PDF with one content stream per page, no dependencies."""

    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(pages)))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(pages):
        stream = (
            b""
            if text is None
            else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
        )
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {5 + 2 * index} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)
