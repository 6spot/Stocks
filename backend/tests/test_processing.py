import hashlib
import html
import os
from datetime import UTC, datetime
from itertools import pairwise
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from stocks.models import (
    Base,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    DocumentParseState,
    Source,
)
from stocks.processing.parser import (
    ParseFailure,
    _ChunkPiece,
    parse_content,
)
from stocks.processing.service import process_document
from stocks.repository import (
    SearchFilters,
    list_parse_batches,
    list_parse_chunks,
    persist_parse_result,
)
from stocks.search.service import exact_search
from stocks.settings import Settings

ARTICLE_HTML = b"""\
<html><head><meta charset="utf-8" /></head><body>
<nav>Site navigation must not become evidence.</nav>
<article>
  <h1>Quarterly inflation update</h1>
  <p>The annual rate was <b>3.1%</b> and the prior value was 2.8%.</p>
  <ul><li>Core inflation: 2.9%</li><li>Policy rate: 4.25%</li></ul>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>CPI</td><td>3.1%</td></tr>
  </table>
  <script>window.secret = "not evidence";</script>
</article>
</body></html>
"""


def make_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://unused/unused",
        "s3_endpoint_url": "https://storage.example",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "parse_chunk_size": 1200,
        "parse_chunk_overlap": 150,
    }
    values.update(overrides)
    return Settings(**values)


def assert_visible_source_ranges(text: str, locator: dict) -> None:
    ranges = locator["source_ranges"]
    assert len(ranges) == len(locator["text"])
    synthetic_indexes = {
        item["char_index"]
        for item in locator.get("separators", [])
    }
    for index, (source_range, character) in enumerate(
        zip(ranges, locator["text"])
    ):
        if not source_range:
            assert index in synthetic_indexes
            assert locator["coordinate_unit"] in {
                "source_html_with_synthetic_separators",
                "decoded_unicode_chars",
            } or (
                index in synthetic_indexes
                and character.isprintable()
            )
            continue
        start, end = source_range
        if start == end:
            assert character.isspace() or character == "|"
            continue
        assert 0 <= start < end <= len(text)
        assert html.unescape(text[start:end]) == character


def make_document(payload: bytes, **overrides) -> Document:
    document = Document(
        id=uuid4(),
        source_id="sec",
        dedupe_key="release-1",
        version=1,
        title="SEC release",
        collected_at=datetime(2026, 10, 20, 1, tzinfo=UTC),
        completeness="complete",
        content_type="text/html; charset=utf-8",
        content_size=len(payload),
        content_hash=hashlib.sha256(payload).hexdigest(),
        snapshot_key="sources/sec/article",
        trust_tier_snapshot="T1",
    )
    for key, value in overrides.items():
        setattr(document, key, value)
    return document


def test_html_parser_preserves_numbers_units_and_locators():
    text = ARTICLE_HTML.decode("utf-8")
    parsed, chunks = parse_content(
        ARTICLE_HTML,
        "text/html; charset=utf-8",
        chunk_size=1200,
        chunk_overlap=150,
    )

    assert parsed.encoding == "utf-8"
    assert chunks
    combined = "\n".join(chunk.text for chunk in chunks)
    assert "3.1%" in combined
    assert "Core inflation: 2.9%" in combined
    assert "Policy rate: 4.25%" in combined
    assert "CPI | 3.1%" in combined
    assert "Site navigation" not in combined
    assert "not evidence" not in combined

    for chunk in chunks:
        assert chunk.locator["version"] == 1
        for segment in chunk.locator["segments"]:
            locator = segment["locator"]
            assert locator["path"]
            assert 0 <= locator["char_start"] < locator["char_end"] <= len(text)
            source = text[locator["char_start"] : locator["char_end"]]
            if locator["element"] not in {"table-row", "table"}:
                visible = " ".join(source.replace("<", " <").split())
                assert any(
                    word in visible
                    for word in ("Quarterly", "annual", "Core", "Policy")
                )


def test_html_parser_keeps_text_around_nested_blocks():
    payload = (
        b"<html><body><article><div>Intro 10%"
        b"<p>Nested 20%</p>"
        b"Outro 30%</div></article></body></html>"
    )

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert chunks[0].text == "Intro 10%\nNested 20%\nOutro 30%"


def test_nested_inline_text_keeps_each_character_in_source_order():
    payload = (
        b"<html><body><main><p>"
        b"Headline 10% <strong>Nested 20%</strong> after."
        b"</p></main></body></html>"
    )

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert chunks[0].text == "Headline 10% Nested 20% after."


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            b"<html><body>Direct body text 3.1%.</body></html>",
            "Direct body text 3.1%.",
        ),
        (
            b"<html><body><main>Direct main text 3.1%.</main></body></html>",
            "Direct main text 3.1%.",
        ),
        (
            b"<html><body><article>Direct article text 3.1%.</article>"
            b"</body></html>",
            "Direct article text 3.1%.",
        ),
    ],
)
def test_text_directly_inside_container_stays_one_evidence_run(payload, expected):
    text = payload.decode()
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert len(chunks) == 1
    assert chunks[0].text == expected
    segments = chunks[0].locator["segments"]
    assert len(segments) == 1
    for segment in segments:
        assert_visible_source_ranges(
            text, {**segment["locator"], "text": segment["text"]}
        )


def test_inline_text_directly_inside_container_keeps_order_and_ranges():
    payload = (
        b"<html><body><main>Rate <strong>3.1%</strong> after.</main>"
        b"</body></html>"
    )
    text = payload.decode()
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert len(chunks) == 1
    assert chunks[0].text == "Rate 3.1% after."
    segments = chunks[0].locator["segments"]
    assert len(segments) == 1
    for segment in segments:
        assert_visible_source_ranges(
            text, {**segment["locator"], "text": segment["text"]}
        )


def test_html_parser_keeps_character_after_leading_whitespace():
    payload = b"<html><body><main><p> <strong>Trim</strong></p></main></body></html>"

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert chunks[0].text == "Trim"


def test_table_locator_keeps_visible_cells_coordinates_and_boundaries():
    payload = (
        b"<html><body><main><table><tr>"
        b"<td>CPI</td><td></td><td>3.1%</td>"
        b"</tr></table></main></body></html>"
    )
    text = payload.decode()
    numeric = payload.decode().replace("3.1%", "10.5%")
    parse_content(
        numeric.encode(),
        "text/html",
        chunk_size=6,
        chunk_overlap=0,
    )
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    locator = chunks[0].locator["segments"][0]["locator"]
    assert locator["element"] == "table-row"
    assert text[locator["char_start"] : locator["char_end"]] == (
        "<tr><td>CPI</td><td></td><td>3.1%</td></tr>"
    )
    cells = chunks[0].locator["segments"][0]["locator"]["cells"]
    assert [cell["text"] for cell in cells] == ["CPI", "cell", "3.1%"]
    assert text[cells[2]["char_start"] : cells[2]["char_end"]] == "<td>3.1%</td>"
    assert text[cells[1]["char_start"] : cells[1]["char_end"]] == "<td></td>"

    _, split_chunks = parse_content(
        payload,
        "text/html",
        chunk_size=6,
        chunk_overlap=0,
    )
    assert all(len(chunk.text) <= 6 for chunk in split_chunks)
    assert "3.1%" in "".join(chunk.text for chunk in split_chunks)
    for chunk in split_chunks:
        for segment in chunk.locator["segments"]:
            assert_visible_source_ranges(
                text,
                {**segment["locator"], "text": segment["text"]},
            )

    _, long_chunks = parse_content(
        numeric.encode(),
        "text/html",
        chunk_size=6,
        chunk_overlap=0,
    )
    assert "10.5%" in "".join(chunk.text for chunk in long_chunks)
    for chunk in long_chunks:
        for segment in chunk.locator["segments"]:
            assert_visible_source_ranges(
                numeric,
                {**segment["locator"], "text": segment["text"]},
            )


def test_preformatted_html_retains_line_breaks():
    payload = (
        b"<html><body><main><pre>line 1\n  line 2\n\tline 3</pre></main></body></html>"
    )

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert chunks[0].text == "line 1\nline 2\nline 3"
    locator = chunks[0].locator["segments"][0]["locator"]
    assert locator["preformatted"] is True


def test_line_breaks_survive_small_chunk_budgets():
    payload = b"<main><p>First line<br>Second line<br>Third line</p></main>"

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=20,
        chunk_overlap=0,
    )

    assert [chunk.text for chunk in chunks] == [
        "First line\n",
        "Second line\n",
        "Third line",
    ]
    for chunk in chunks:
        for segment in chunk.locator["segments"]:
            locator = segment["locator"]
            assert len(locator["source_ranges"]) == len(segment["text"])


def test_html_entity_ranges_cover_whole_references_and_inline_boundaries_are_exact():
    payload = (
        b"<html><body><main><p>Head "
        b"<strong>nested &amp;</strong> tail.</p></main></body></html>"
    )
    text = payload.decode()
    parsed, _ = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert parsed.segments[0].text == "Head nested & tail."
    source_ranges = parsed.segments[0].source_ranges
    assert source_ranges is not None
    ampersand = parsed.segments[0].text.index("&")
    assert text[slice(*source_ranges[ampersand])] == "&amp;"
    for index, character in enumerate(parsed.segments[0].text):
        if character.isalpha():
            assert text[slice(*source_ranges[index])] == character


def test_unknown_ampersand_sequences_remain_literal_text():
    payload = b"<main><p>Rate e5&sa stayed unchanged.</p></main>"

    parsed, _ = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert parsed.segments[0].text == "Rate e5&sa stayed unchanged."


def test_split_chunks_keep_entities_intact_and_obey_budget():
    payload = b"<p>First &amp; second &amp; third sentence &amp; fourth.</p>"
    text = payload.decode()
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=20,
        chunk_overlap=1,
    )

    assert all(len(chunk.text) <= 20 for chunk in chunks)
    for chunk in chunks:
        for segment in chunk.locator["segments"]:
            assert_visible_source_ranges(
                text,
                {**segment["locator"], "text": segment["text"]},
            )


def test_chunk_overlap_is_preserved_across_semantic_segments():
    payload = b"<p>Alpha Beta</p><p>Gamma Delta Epsilon Zeta</p>"
    parsed, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=22,
        chunk_overlap=6,
    )

    assert [segment.text for segment in parsed.segments] == [
        "Alpha Beta",
        "Gamma Delta Epsilon Zeta",
    ]
    assert all(len(chunk.text) <= 22 for chunk in chunks)
    assert len(chunks) > 1
    for previous, following in pairwise(chunks):
        previous_ranges = {
            tuple(source_range)
            for segment in previous.locator["segments"]
            for source_range in segment["locator"]["source_ranges"]
            if source_range
        }
        following_ranges = {
            tuple(source_range)
            for segment in following.locator["segments"]
            for source_range in segment["locator"]["source_ranges"]
            if source_range
        }
        overlap_ranges = previous_ranges & following_ranges
        assert overlap_ranges
        assert len(overlap_ranges) <= 6


def test_plain_text_parser_retains_paragraph_ranges():
    payload = b"Headline\n\nRevenue was $1.25 billion.\n\nMargin: 18.4%."
    parsed, chunks = parse_content(
        payload,
        "text/plain; charset=utf-8",
        chunk_size=1200,
        chunk_overlap=0,
    )

    source = payload.decode()
    assert parsed.segments
    assert chunks[0].text == "Headline\nRevenue was $1.25 billion.\nMargin: 18.4%."
    for segment in chunks[0].locator["segments"]:
        locator = segment["locator"]
        assert locator["coordinate_unit"] == "decoded_unicode_chars"
        assert (
            source[locator["char_start"] : locator["char_end"]].strip()
            == segment["text"]
        )


def test_chunk_budget_and_overlap_are_bounded():
    first, _ = parse_content(
        b"alpha beta gamma delta epsilon",
        "text/plain",
        chunk_size=1200,
        chunk_overlap=0,
    )
    second, chunks = parse_content(
        b"alpha beta gamma delta epsilon",
        "text/plain",
        chunk_size=20,
        chunk_overlap=6,
    )

    assert first.text == second.text
    assert "".join(chunk.text.replace("\n", "") for chunk in chunks).replace(
        "gammagamma", "gamma"
    ) == "alpha beta gamma delta epsilon"
    assert chunks[1].text.startswith("gamma")
    assert all(len(chunk.text) <= 20 for chunk in chunks)


def test_overlap_cannot_push_a_chunk_over_its_budget():
    _, chunks = parse_content(
        b"word. word. word. word.",
        "text/plain",
        chunk_size=13,
        chunk_overlap=1,
    )

    assert chunks[0].text == "word. word."
    assert chunks[1].text == "word. word."
    assert all(len(chunk.text) <= 13 for chunk in chunks)


def test_chunking_prefers_semantic_boundaries_before_overlap():
    payload = b"First paragraph ends here.\n\nSecond paragraph starts here."
    _, chunks = parse_content(
        payload,
        "text/plain",
        chunk_size=40,
        chunk_overlap=15,
    )

    assert chunks[0].text == "First paragraph ends here."
    assert chunks[1].text == "ends here.\nSecond paragraph starts here."
    assert all(len(chunk.text) <= 40 for chunk in chunks)


def test_non_void_self_closing_html_tag_does_not_change_sibling_path():
    payload = b"""\
<html><body><main>
  <section />
  <p>After the self-closing section.</p>
</main></body></html>
"""
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    segment = chunks[0].locator["segments"][0]["locator"]
    assert segment["element"] == "p"
    assert not any(item.startswith("section[") for item in segment["path"])


def test_split_html_locators_follow_exact_source_characters():
    payload = (
        b"<html><body><main><p>"
        b"First sentence. Second sentence is here. Third sentence ends."
        b"</p></main></body></html>"
    )
    text = payload.decode()
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=30,
        chunk_overlap=7,
    )

    assert all(
        sum(len(segment["text"]) for segment in chunk.locator["segments"])
        + len(chunk.locator["segments"])
        - 1
        == len(chunk.text)
        for chunk in chunks
    )
    for chunk in chunks:
        for segment in chunk.locator["segments"]:
            assert_visible_source_ranges(
                text,
                {**segment["locator"], "text": segment["text"]},
            )


def test_nested_html_locators_keep_source_ordering():
    payload = (
        b"<html><body><main><p>"
        b"First sentence. <strong>Nested</strong> after."
        b"</p></main></body></html>"
    )

    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=1200,
        chunk_overlap=0,
    )

    assert chunks[0].text == "First sentence. Nested after."


def test_chunk_overlap_keeps_exact_source_offsets_across_segments():
    payload = (
        b"<html><body><main>"
        b"<p>First sentence &amp; second sentence.</p>"
        b"<p>Third sentence &amp; fourth sentence.</p>"
        b"<p>Fifth sentence &amp; sixth sentence.</p>"
        b"</main></body></html>"
    )
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=45,
        chunk_overlap=15,
    )

    assert chunks[0].text[-8:] in chunks[1].text
    assert chunks[1].text.endswith("sentence.")
    assert "\n" in chunks[1].text

    _, split_chunks = parse_content(
        b"First sentence. Second sentence is here. Third sentence ends.",
        "text/plain",
        chunk_size=30,
        chunk_overlap=7,
    )
    for chunk in split_chunks:
        for segment in chunk.locator["segments"]:
            assert_visible_source_ranges(
                "First sentence. Second sentence is here. Third sentence ends.",
                {**segment["locator"], "text": segment["text"]},
            )


def test_chunk_piece_slice_uses_selected_source_ranges():
    piece = _ChunkPiece(
        text="ABCDE",
        locator={
            "kind": "text",
            "char_start": 0,
            "char_end": 5,
            "coordinate_unit": "decoded_unicode_chars",
        },
        source_ranges=tuple((index, index + 1) for index in range(5)),
    )

    sliced = piece.slice(2, 4)

    assert sliced.text == "CD"
    assert sliced.source_ranges == ((2, 3), (3, 4))
    assert sliced.locator["char_start"] == 2
    assert sliced.locator["char_end"] == 4
    assert sliced.locator["source_ranges"] == [[2, 3], [3, 4]]


def test_html_locators_use_only_visible_entity_characters():
    payload = (
        b"<html><body><main><p>"
        b"First sentence &amp; second sentence. Third sentence &amp; fourth."
        b"</p></main></body></html>"
    )
    text = payload.decode()
    _, chunks = parse_content(
        payload,
        "text/html",
        chunk_size=30,
        chunk_overlap=5,
    )

    assert chunks[0].text == "First sentence & second"
    for chunk in chunks:
        for segment in chunk.locator["segments"]:
            locator = segment["locator"]
            assert_visible_source_ranges(text, {**locator, "text": segment["text"]})


@pytest.mark.parametrize(
    ("payload", "content_type", "code", "status"),
    [
        (
            b"<html><body><nav>menu</nav><script>x</script></body></html>",
            "text/html",
            "NO_EXTRACTABLE_TEXT",
            "failed",
        ),
        (b"\x89PNG\r\n\x1a\n", "image/png", "UNSUPPORTED_CONTENT_TYPE", "unsupported"),
        (b"%PDF-1.7 truncated", "application/pdf", "PDF_DAMAGED", "failed"),
        (b"\xff\xfe", "text/plain; charset=utf-8", "INVALID_ENCODING", "failed"),
    ],
)
def test_invalid_or_unsupported_content_is_explicit(
    payload, content_type, code, status
):
    with pytest.raises(ParseFailure) as captured:
        parse_content(
            payload,
            content_type,
            chunk_size=1200,
            chunk_overlap=150,
        )

    assert captured.value.code == code
    assert captured.value.status == status


def test_pdf_document_is_parsed_into_page_located_chunks(engine, monkeypatch):
    from test_parsing_formats import minimal_pdf

    payload = minimal_pdf(["Fiscal 2026 revenue was 1234.5 million USD."])
    document = make_document(payload, content_type="application/pdf")
    monkeypatch.setattr(
        "stocks.repository.read_snapshot",
        lambda config, key: payload,
    )

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                trust_tier="T1",
            )
        )
        session.commit()
        session.add(document)
        session.commit()

        summary = process_document(session, make_config(), document)

        assert summary.status == "completed"
        chunks = list_parse_chunks(session, document.id)
        assert len(chunks) == 1
        locator = chunks[0].locator["segments"][0]["locator"]
        assert locator["kind"] == "pdf_page"
        assert locator["page"] == 1
        assert "1234.5 million USD" in chunks[0].text


def test_processing_does_not_read_storage_for_a_reused_completed_batch(
    monkeypatch,
):
    document = make_document(b"content")
    calls: list[object] = []

    class ExistingBatch:
        id = uuid4()
        status = "completed"
        chunk_count = 3
        error = None
        processor_version = "semantic-text-v1"
        config_fingerprint = "f" * 64

    monkeypatch.setattr(
        "stocks.processing.service.find_parse_batch",
        lambda *args, **kwargs: ExistingBatch(),
    )

    summary = process_document(
        object(),
        make_config(),
        document,
        read_bytes=lambda config, item: calls.append(item) or b"content",
    )

    assert summary.reused is True
    assert summary.status == "completed"
    assert calls == []


def test_processing_routes_content_hash_failure_to_failed_batch(monkeypatch):
    payload = b"content"
    document = make_document(payload)
    session = object()
    captured = {}

    def fake_persist(session_arg, document_id, **kwargs):
        captured.update(kwargs)
        return type(
            "Result",
            (),
            {
                "batch": type(
                    "Batch",
                    (),
                    {
                        "id": uuid4(),
                        "status": kwargs["status"],
                        "chunk_count": 0,
                        "error": kwargs["error"],
                        "processor_version": kwargs["processor_version"],
                        "config_fingerprint": kwargs["config_fingerprint"],
                    },
                )(),
                "reused": False,
            },
        )()

    monkeypatch.setattr(
        "stocks.processing.service.find_parse_batch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "stocks.processing.service.persist_parse_result",
        fake_persist,
    )

    summary = process_document(
        session,
        make_config(),
        document,
        read_bytes=lambda config, item: b"different bytes",
    )

    assert summary.status == "failed"
    assert captured["error"]["code"] == "CONTENT_HASH_MISMATCH"


def test_processing_does_not_hide_parser_programming_errors(monkeypatch):
    document = make_document(b"content")
    monkeypatch.setattr(
        "stocks.processing.service.find_parse_batch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "stocks.processing.service.persist_parse_result",
        lambda *args, **kwargs: pytest.fail("programming errors must propagate"),
    )
    monkeypatch.setattr(
        "stocks.processing.service.parse_content",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("parser defect")),
    )

    with pytest.raises(ValueError, match="parser defect"):
        process_document(
            object(),
            make_config(),
            document,
            read_bytes=lambda config, item: b"content",
        )


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL processing tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def test_persisted_batches_are_idempotent_and_preserve_referenced_chunks(
    engine,
    monkeypatch,
):
    document = make_document(ARTICLE_HTML)
    monkeypatch.setattr(
        "stocks.repository.read_snapshot",
        lambda config, key: ARTICLE_HTML,
    )
    config = make_config(parse_chunk_size=200, parse_chunk_overlap=30)

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                trust_tier="T1",
            )
        )
        session.commit()
        session.add(document)
        session.commit()

        first = process_document(session, config, document)
        reused = process_document(session, config, document)
        original_chunks = list_parse_chunks(
            session,
            document.id,
            batch_id=first.batch_id,
        )
        original_text = [chunk.text for chunk in original_chunks]
        original_locator = [chunk.locator for chunk in original_chunks]

        assert first.status == "completed"
        assert first.chunk_count > 0
        assert reused.reused is True
        assert reused.batch_id == first.batch_id
        assert len(list_parse_batches(session, document.id)) == 1

        state = session.get(DocumentParseState, document.id)
        assert state is not None
        assert state.current_batch_id == first.batch_id

        def broken_read(config, item):
            return b"corrupt"

        failed = process_document(
            session,
            config,
            document,
            force_reprocess=True,
            read_bytes=broken_read,
        )
        session.expire_all()
        state = session.get(DocumentParseState, document.id)
        assert failed.status == "failed"
        assert state is not None
        assert state.latest_batch_id == failed.batch_id
        assert state.current_batch_id == first.batch_id
        assert list_parse_chunks(session, document.id) == original_chunks

        repaired = process_document(
            session,
            config,
            document,
            force_reprocess=True,
        )
        session.expire_all()
        state = session.get(DocumentParseState, document.id)
        assert repaired.status == "completed"
        assert repaired.batch_id != first.batch_id
        assert state is not None
        assert state.current_batch_id == repaired.batch_id

        historical = list_parse_chunks(
            session,
            document.id,
            batch_id=first.batch_id,
        )
        assert [chunk.text for chunk in historical] == original_text
        assert [chunk.locator for chunk in historical] == original_locator
        assert {chunk.id for chunk in historical}.isdisjoint(
            {chunk.id for chunk in list_parse_chunks(session, document.id)}
        )


def test_unsupported_document_records_state_without_chunks(engine, monkeypatch):
    payload = b"\x89PNG\r\n\x1a\n"
    document = make_document(payload, content_type="image/png")
    monkeypatch.setattr(
        "stocks.repository.read_snapshot",
        lambda config, key: payload,
    )

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                trust_tier="T1",
            )
        )
        session.commit()
        session.add(document)
        session.commit()

        summary = process_document(session, make_config(), document)

        assert summary.status == "unsupported"
        state = session.get(DocumentParseState, document.id)
        assert state is not None
        assert state.status == "unsupported"
        assert state.current_batch_id is None
        assert session.scalars(select(DocumentChunk)).all() == []


def test_repository_rejects_invalid_batch_shape_before_write(engine):
    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                trust_tier="T1",
            )
        )
        session.commit()
        document = make_document(b"valid")
        session.add(document)
        session.commit()

        with pytest.raises(ValueError, match="must contain chunks"):
            persist_parse_result(
                session,
                document.id,
                processor_version="semantic-text-v1",
                config_fingerprint="a" * 64,
                content_hash=document.content_hash,
                status="completed",
                error=None,
                chunks=(),
                attempted_at=datetime.now(UTC),
            )

        assert session.scalars(select(DocumentParseBatch)).all() == []


def test_exact_search_returns_current_chunks_with_source_and_time_filters(
    engine,
):
    payload = ARTICLE_HTML
    document = make_document(payload)

    with Session(engine) as session:
        session.add(
            Source(
                id="sec",
                name="SEC",
                connector="rss",
                trust_tier="T1",
            )
        )
        session.commit()
        session.add(document)
        session.commit()

        summary = process_document(
            session,
            make_config(),
            document,
            read_bytes=lambda config, item: payload,
            clock=lambda: datetime(2026, 10, 20, 2, tzinfo=UTC),
        )
        result = exact_search(
            session,
            "cpi",
            filters=SearchFilters(
                source_ids=("sec",),
            ),
        )
        assert result.hits
        assert result.hits[0].document_id == document.id
        assert result.hits[0].parse_batch_id == summary.batch_id
        assert "CPI" in result.hits[0].text

        filtered = exact_search(
            session,
            "cpi",
            filters=SearchFilters(
                source_ids=("other",),
            ),
        )
        assert filtered.hits == ()


def test_exact_search_rejects_unbounded_or_unsafe_terms(engine):
    with Session(engine) as session:
        with pytest.raises(ValueError, match="short code"):
            exact_search(session, "CPI OR 1=1")
        with pytest.raises(ValueError, match="short code"):
            exact_search(session, "x" * 65)

        with pytest.raises(ValueError, match="timezone"):
            SearchFilters(
                published_from=datetime(2026, 10, 20),
            )
