from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from stocks.processing.chunking import split_text
from stocks.processing.pdf import PdfError, pdf_pages
from stocks.processing.structured import (
    StructuredError,
    json_values,
    xml_values,
)

PROCESSOR_VERSION = "semantic-text-v2"

HTML_CONTENT_TYPES = frozenset({"application/xhtml+xml", "text/html"})
TEXT_CONTENT_TYPES = frozenset({"text/plain"})
JSON_MEDIA_TYPES = frozenset({"application/json", "text/json"})
XML_MEDIA_TYPES = frozenset({"application/xml", "text/xml"})
PDF_CONTENT_TYPES = frozenset({"application/pdf"})
SUPPORTED_CONTENT_TYPES = (
    HTML_CONTENT_TYPES
    | TEXT_CONTENT_TYPES
    | JSON_MEDIA_TYPES
    | XML_MEDIA_TYPES
    | PDF_CONTENT_TYPES
)

NOISE_ELEMENTS = frozenset(
    {
        "aside",
        "button",
        "canvas",
        "dialog",
        "footer",
        "form",
        "head",
        "iframe",
        "input",
        "nav",
        "ng-template",
        "noscript",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
    }
)
BLOCK_ELEMENTS = frozenset(
    {
        "address",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "pre",
        "section",
    }
)
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
TABLE_CELLS = frozenset({"td", "th"})

_MEDIA_TYPE = re.compile(
    r"^\s*([!#$%&'*+\-.^_`|~0-9A-Za-z]+/[!#$%&'*+\-.^_`|~0-9A-Za-z]+)"
    r"\s*(?:;\s*charset\s*=\s*[\"']?([^;\"'\s]+)[\"']?)?",
    re.IGNORECASE,
)
SourceRange = tuple[int, int]


class LocatorAlignmentError(ValueError):
    """Raised when visible text cannot be mapped back to its source."""


class ParseFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: str = "failed",
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class SourceSegment:
    text: str
    locator: dict
    source_ranges: tuple[SourceRange, ...] | None = None
    source_text: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    content_type: str
    text: str
    encoding: str | None
    segments: tuple[SourceSegment, ...]


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    locator: dict


@dataclass(frozen=True)
class _ChunkPiece:
    text: str
    locator: dict
    source_ranges: tuple[SourceRange, ...] | None = None

    def slice(self, start: int, end: int) -> _ChunkPiece:
        ranges = (
            self.source_ranges[start:end]
            if self.source_ranges is not None
            else None
        )
        text = self.text[start:end]
        return _ChunkPiece(
            text,
            _slice_locator(self.locator, source_ranges=ranges, text=text),
            ranges,
        )


def parse_content(
    payload: bytes,
    content_type: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[ParsedDocument, tuple[ParsedChunk, ...]]:
    media_type, charset = parse_media_type(content_type)
    family = _content_family(media_type)
    if family is None:
        raise ParseFailure(
            "UNSUPPORTED_CONTENT_TYPE",
            "Document content type is not supported by this parser",
            status="unsupported",
            details={"content_type": media_type[:255]},
        )
    if family == "pdf":
        return _parse_pdf(
            payload,
            media_type=media_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    text, encoding = decode_bytes(payload, charset)
    if family == "html":
        extracted = _extract_html(text)
    elif family == "text":
        extracted = _extract_plain_text(text)
    elif family == "json":
        extracted = _structured_segments(json_values, text)
    else:
        extracted = _structured_segments(xml_values, text)
    segments = tuple(segment for segment in extracted if segment.text.strip())
    if not segments:
        raise ParseFailure(
            "NO_EXTRACTABLE_TEXT",
            "Document did not contain extractable evidence text",
            details={"content_chars": len(text)},
        )
    chunks = build_chunks(
        segments,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if not chunks:
        raise ParseFailure(
            "NO_EXTRACTABLE_TEXT",
            "Document did not contain extractable evidence text",
            details={"content_chars": len(text)},
        )
    return (
        ParsedDocument(media_type, text, encoding, segments),
        chunks,
    )


def _content_family(media_type: str) -> str | None:
    """The parser family for one media type, including ``+json``/``+xml``."""

    if media_type in HTML_CONTENT_TYPES:
        return "html"
    if media_type in TEXT_CONTENT_TYPES:
        return "text"
    if media_type in JSON_MEDIA_TYPES or media_type.endswith("+json"):
        return "json"
    if media_type in XML_MEDIA_TYPES or media_type.endswith("+xml"):
        return "xml"
    if media_type in PDF_CONTENT_TYPES:
        return "pdf"
    return None


def _structured_segments(
    extract: Callable[[str], list[tuple[str, dict]]],
    text: str,
) -> list[SourceSegment]:
    """Located values from a structured original, as parse segments."""

    try:
        values = extract(text)
    except StructuredError as exc:
        raise ParseFailure(
            exc.code,
            exc.message,
            details=exc.details,
        ) from exc
    return [
        SourceSegment(text=value, locator=locator)
        for value, locator in values
    ]


def _parse_pdf(
    payload: bytes,
    *,
    media_type: str,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[ParsedDocument, tuple[ParsedChunk, ...]]:
    """Page text with its page locator; a PDF is never decoded as text."""

    try:
        pages = pdf_pages(payload)
    except PdfError as exc:
        raise ParseFailure(
            exc.code,
            exc.message,
            details=exc.details,
        ) from exc
    segments = tuple(
        SourceSegment(
            text=page_text,
            locator=locator,
            # Only whitespace normalization happens, so every extracted
            # character keeps its own position inside this page's text.
            source_ranges=tuple(
                (index, index + 1) for index in range(len(page_text))
            ),
        )
        for page_text, locator in pages
        if page_text.strip()
    )
    if not segments:
        raise ParseFailure(
            "PDF_TEXT_UNAVAILABLE",
            "Stored PDF has no extractable text layer",
            details={"bytes": len(payload)},
        )
    chunks = build_chunks(
        segments,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if not chunks:
        raise ParseFailure(
            "NO_EXTRACTABLE_TEXT",
            "Document did not contain extractable evidence text",
            details={"content_chars": 0},
        )
    return (
        ParsedDocument(
            media_type,
            "\n".join(segment.text for segment in segments),
            None,
            segments,
        ),
        chunks,
    )


def parse_media_type(value: str) -> tuple[str, str | None]:
    match = _MEDIA_TYPE.match(value)
    if match is None:
        raise ParseFailure(
            "INVALID_CONTENT_TYPE",
            "Document content type could not be parsed",
            status="unsupported",
        )
    return match.group(1).lower(), match.group(2)


def decode_bytes(payload: bytes, charset: str | None) -> tuple[str, str]:
    encoding = (charset or "utf-8").strip().lower()
    try:
        text = payload.decode(encoding, errors="strict")
    except LookupError as exc:
        raise ParseFailure(
            "UNSUPPORTED_CHARSET",
            "Document charset is not supported",
            details={"charset": encoding[:80]},
        ) from exc
    except UnicodeDecodeError as exc:
        raise ParseFailure(
            "INVALID_ENCODING",
            "Document bytes are not valid in the declared encoding",
            details={"charset": encoding[:80], "byte_offset": exc.start},
        ) from exc
    return text, encoding


def build_chunks(
    segments: tuple[SourceSegment, ...],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[ParsedChunk, ...]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    prepared: list[tuple[SourceSegment, _ChunkPiece]] = []
    text_parts: list[str] = []
    protected: set[int] = set()
    cursor = 0
    for segment in segments:
        value, ranges = _normalize_segment_with_ranges(segment)
        if not value:
            continue
        piece = _ChunkPiece(value, segment.locator, ranges)
        prepared.append((segment, piece))
        if text_parts:
            text_parts.append("\n")
            cursor += 1
        text_parts.append(value)
        cursor += len(value)
        if ranges is not None:
            run_start = 0
            while run_start < len(ranges):
                run_end = run_start + 1
                while (
                    run_end < len(ranges)
                    and ranges[run_end] == ranges[run_start]
                    and ranges[run_start][0] < ranges[run_start][1]
                ):
                    run_end += 1
                if run_end - run_start > 1:
                    start = cursor - len(value)
                    protected.update(range(start + run_start, start + run_end))
                run_start = run_end
        token_start = cursor - len(value)
        protected.update(
            token_start + index
            for index, character in enumerate(value)
            if character == "\n"
        )
        for match in re.finditer(r"\d[\d,]*(?:\.\d+)?(?:%|[A-Za-z]{1,6}\b)?", value):
            protected.update(range(token_start + match.start(), token_start + match.end()))
    plain = "".join(text_parts)
    if not plain:
        return ()
    spans = split_text(
        plain,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        protected=frozenset(protected),
    )
    output: list[ParsedChunk] = []
    prepared_offsets: list[int] = []
    offset = 0
    for _segment, piece in prepared:
        prepared_offsets.append(offset)
        offset += len(piece.text) + 1
    for span_start, span_end in spans:
        pieces: list[_ChunkPiece] = []
        for (segment, piece), source_start in zip(prepared, prepared_offsets):
            source_end = source_start + len(piece.text)
            start = max(span_start, source_start)
            end = min(span_end, source_end)
            if start < end:
                pieces.append(piece.slice(start - source_start, end - source_start))
        if not pieces:
            raise LocatorAlignmentError("chunk does not contain a source piece")
        chunk_text = "\n".join(piece.text for piece in pieces)
        if len(chunk_text) != span_end - span_start:
            raise LocatorAlignmentError("chunk text cannot be reconstructed from source")
        output.append(
            ParsedChunk(
                chunk_text,
                {
                    "version": 1,
                    "segments": [
                        {"text": piece.text, "locator": piece.locator}
                        for piece in pieces
                    ],
                },
            )
        )
    return tuple(output)


def _normalize_segment_with_ranges(
    segment: SourceSegment,
) -> tuple[str, tuple[SourceRange, ...] | None]:
    value = segment.text
    ranges = segment.source_ranges
    preserve_lines = bool(segment.locator.get("preformatted", False))
    html_segment = segment.locator.get("kind") == "html"
    if ranges is None:
        if html_segment:
            raise LocatorAlignmentError(
                "HTML source ranges must align with segment text"
            )
        source_text = segment.source_text or value
        normalized, indexes = (
            _preformatted_lines(source_text)
            if preserve_lines
            else _normalize_lines(source_text)
        )
        return normalized, tuple((index, index + 1) for index in indexes)
    if len(ranges) != len(value):
        raise LocatorAlignmentError("source ranges must align with segment text")
    text, indexes = _normalize_mapped_text(
        value,
        ranges,
        preserve_lines=preserve_lines,
        preserve_system_lines=html_segment,
    )
    return text, tuple(ranges[index] for index in indexes)


def _normalize_mapped_text(
    value: str,
    ranges: tuple[SourceRange, ...],
    *,
    preserve_lines: bool,
    preserve_system_lines: bool,
) -> tuple[str, tuple[int, ...]]:
    output: list[str] = []
    indexes: list[int] = []
    pending_space: int | None = None
    for index, character in enumerate(value):
        if character in {" ", "\t"}:
            if pending_space is None:
                pending_space = index
            continue
        if character == "\n":
            if (
                pending_space is not None
                and output
                and output[-1] != "\n"
                and preserve_system_lines
            ):
                output.append(" ")
                indexes.append(pending_space)
            pending_space = None
            if (
                preserve_system_lines
                and (not output or output[-1] != "\n")
            ):
                output.append("\n")
                indexes.append(index)
            continue
        if pending_space is not None and output:
            output.append(" ")
            indexes.append(pending_space)
        pending_space = None
        output.append(character)
        indexes.append(index)
    return "".join(output), tuple(indexes)


def _normalize_lines(value: str) -> tuple[str, tuple[int, ...]]:
    output: list[str] = []
    indexes: list[int] = []
    for start, end in _line_bounds(value):
        start, end = _strip_bounds(value, start, end)
        if start == end:
            continue
        if output:
            output.append("\n")
            indexes.append(max(0, start - 1))
        for index in range(start, end):
            if (output and output[-1] == " "
                    and value[index].isspace()
                    and indexes[-1] + 1 == index):
                continue
            if value[index].isspace():
                output.append(" ")
            else:
                output.append(value[index])
            indexes.append(index)
    return "".join(output), tuple(indexes)


def _preformatted_lines(value: str) -> tuple[str, tuple[int, ...]]:
    lines = value.splitlines(keepends=True)
    if not lines:
        return "", ()
    output: list[str] = []
    indexes: list[int] = []
    raw_start = 0
    for line in lines:
        raw_end = raw_start + len(line)
        content_end = raw_end - len(line) + len(line.rstrip("\r\n"))
        line_start, line_end = _strip_bounds(value, raw_start, content_end)
        if output:
            output.append("\n")
            indexes.append(content_end)
        for index in range(line_start, line_end):
            output.append(value[index])
            indexes.append(index)
        raw_start = raw_end
    return "".join(output).strip("\n"), tuple(indexes)


def _line_bounds(value: str) -> list[SourceRange]:
    lines: list[SourceRange] = []
    start = 0
    for index, character in enumerate(value):
        if character == "\n":
            end = index - 1 if index > start and value[index - 1] == "\r" else index
            lines.append((start, end))
            start = index + 1
        elif character == "\r":
            lines.append((start, index))
            start = index + 1
    lines.append((start, len(value)))
    return lines


def _strip_bounds(value: str, start: int, end: int) -> SourceRange:
    while start < end and value[start].isspace():
        start += 1
    while end > start and value[end - 1].isspace():
        end -= 1
    return start, end


def _source_char_ranges(
    ranges: tuple[SourceRange, ...],
) -> list[SourceRange]:
    return [source_range for source_range in ranges if source_range[0] < source_range[1]]


def _slice_locator(
    locator: dict,
    *,
    source_ranges: tuple[SourceRange, ...] | None,
    text: str,
) -> dict:
    sliced = dict(locator)
    sliced.pop("source_ranges", None)
    sliced.pop("separators", None)
    if source_ranges is None:
        if locator.get("kind") == "html":
            raise LocatorAlignmentError("HTML segments must retain source ranges")
        return sliced
    if len(source_ranges) != len(text):
        raise LocatorAlignmentError("sliced source ranges must align with text")

    sliced["source_ranges"] = [
        [start, end] if start < end else []
        for start, end in source_ranges
    ]
    synthetic = [
        {
            "char_index": index,
            "text": character,
            "coordinate_unit": "synthetic_text",
        }
        for index, ((start, end), character) in enumerate(zip(source_ranges, text))
        if start == end
    ]
    if synthetic:
        sliced["coordinate_unit"] = "source_html_with_synthetic_separators"
        sliced["separators"] = synthetic
    if locator.get("kind") == "text":
        real_ranges = _source_char_ranges(source_ranges)
        indices = [
            index
            for start, end in real_ranges
            for index in range(start, end)
        ]
        if indices:
            sliced["char_start"] = min(indices)
            sliced["char_end"] = max(indices) + 1
    return sliced


def _extract_plain_text(text: str) -> list[SourceSegment]:
    segments: list[SourceSegment] = []
    paragraph_start: int | None = None
    cursor = 0
    for line in text.splitlines(keepends=True):
        if line.strip() and paragraph_start is None:
            paragraph_start = cursor
        if not line.strip() and paragraph_start is not None:
            segments.append(
                SourceSegment(
                    text=text[paragraph_start:cursor],
                    locator=_text_locator(paragraph_start, cursor),
                    source_ranges=tuple(
                        (index, index + 1)
                        for index in range(paragraph_start, cursor)
                    ),
                )
            )
            paragraph_start = None
        cursor += len(line)
    if paragraph_start is not None:
        segments.append(
            SourceSegment(
                text=text[paragraph_start:],
                locator=_text_locator(paragraph_start, len(text)),
                source_ranges=tuple(
                    (index, index + 1)
                    for index in range(paragraph_start, len(text))
                ),
            )
        )
    return segments


def _text_locator(start: int, end: int) -> dict:
    return {
        "kind": "text",
        "char_start": start,
        "char_end": end,
        "coordinate_unit": "decoded_unicode_chars",
    }


@dataclass(frozen=True)
class _HtmlText:
    text: str
    source_start: int
    source_end: int


@dataclass
class _HtmlNode:
    tag: str
    path: tuple[str, ...]
    source_start: int
    source_end: int
    children: list[_HtmlNode | _HtmlText]


class _HtmlTreeBuilder(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.root = _HtmlNode("document", ("document[0]",), 0, len(source), [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        self._start(tag.lower())

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        node = self._start(tag)
        if tag not in VOID_ELEMENTS:
            node.source_end = self._start_tag_end(node.source_start)
            if self.stack[-1] is node:
                self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        row, column = self.getpos()
        start = _index_from_line_column(self.source, row, column)
        close_end = self.source.find(">", start)
        source_end = len(self.source) if close_end < 0 else close_end + 1
        if tag in {"td", "th", "tr"}:
            node = next(
                (
                    item
                    for item in reversed(self.stack)
                    if isinstance(item, _HtmlNode) and item.tag == tag
                ),
                None,
            )
            if node is not None:
                node.source_end = max(node.source_end, source_end)
        matching = next(
            (
                index
                for index in range(len(self.stack) - 1, 0, -1)
                if self.stack[index].tag == tag
            ),
            None,
        )
        if matching is None:
            return
        while len(self.stack) > matching:
            self.stack.pop().source_end = source_end

    def handle_data(self, data: str) -> None:
        row, column = self.getpos()
        start = _index_from_line_column(self.source, row, column)
        if not data:
            return
        if self.source[start : start + len(data)] != data:
            raise LocatorAlignmentError("HTML character data cannot be mapped to source")
        self.stack[-1].children.extend(
            _HtmlText(character, start + index, start + index + 1)
            for index, character in enumerate(data)
        )

    def handle_entityref(self, name: str) -> None:
        self._handle_reference(name)

    def handle_charref(self, name: str) -> None:
        self._handle_reference(f"#{name}")

    def _handle_reference(self, name: str) -> None:
        row, column = self.getpos()
        start = _index_from_line_column(self.source, row, column)
        opening = f"&{name}"
        if not self.source.startswith(opening, start):
            raise LocatorAlignmentError("HTML entity cannot be mapped to source")
        end = start + len(opening)
        if end < len(self.source) and self.source[end] == ";":
            end += 1
        decoded = html.unescape(self.source[start:end])
        if not decoded:
            raise LocatorAlignmentError("HTML entity cannot be mapped to source")
        if decoded == self.source[start:end]:
            self.stack[-1].children.extend(
                _HtmlText(self.source[index], index, index + 1)
                for index in range(start, end)
            )
            return
        self.stack[-1].children.extend(
            _HtmlText(character, start, end)
            for character in decoded
        )

    def close(self) -> None:
        super().close()
        while len(self.stack) > 1:
            self.stack.pop().source_end = len(self.source)

    def _start(self, tag: str) -> _HtmlNode:
        row, column = self.getpos()
        start = _index_from_line_column(self.source, row, column)
        parent = self.stack[-1]
        same_tag_count = sum(
            1
            for child in parent.children
            if isinstance(child, _HtmlNode) and child.tag == tag
        )
        node = _HtmlNode(
            tag,
            parent.path + (f"{tag}[{same_tag_count}]",),
            start,
            self._start_tag_end(start),
            [],
        )
        parent.children.append(node)
        if tag not in VOID_ELEMENTS:
            self.stack.append(node)
        return node

    def _start_tag_end(self, start: int) -> int:
        end = self.source.find(">", start)
        return len(self.source) if end < 0 else end + 1


def _char_range(value: _HtmlText, index: int) -> SourceRange:
    if len(value.text) == 1:
        return value.source_start, value.source_end
    return value.source_start + index, value.source_start + index + 1


def _extract_html(text: str) -> list[SourceSegment]:
    builder = _HtmlTreeBuilder(text)
    builder.feed(text)
    builder.close()
    primary = _find_evidence_container(builder.root)
    segments: list[SourceSegment] = []
    _walk_evidence(primary, segments, primary=primary)
    return segments


def _find_evidence_container(root: _HtmlNode) -> _HtmlNode:
    found: dict[str, list[_HtmlNode]] = {"article": [], "main": [], "body": []}

    def visit(node: _HtmlNode) -> None:
        if node.tag in NOISE_ELEMENTS:
            return
        if node.tag in found:
            found[node.tag].append(node)
        for child in node.children:
            if isinstance(child, _HtmlNode):
                visit(child)

    visit(root)
    return next((nodes[0] for nodes in found.values() if nodes), root)


def _walk_evidence(
    node: _HtmlNode,
    output: list[SourceSegment],
    *,
    primary: _HtmlNode,
) -> None:
    if node.tag in NOISE_ELEMENTS:
        return
    if node.tag == "table":
        output.extend(_table_segments(node, primary))
        return
    if node.tag in BLOCK_ELEMENTS:
        _walk_block(node, output, primary=primary)
        return
    for child in node.children:
        if isinstance(child, _HtmlNode):
            _walk_evidence(child, output, primary=primary)
        elif child.text.strip():
            output.append(
                SourceSegment(
                    child.text,
                    _html_node_locator(node, primary),
                    tuple(
                        _char_range(child, index)
                        for index in range(len(child.text))
                    ),
                )
            )


def _walk_block(
    node: _HtmlNode,
    output: list[SourceSegment],
    *,
    primary: _HtmlNode,
) -> None:
    inline: list[_HtmlNode | _HtmlText] = []

    def flush() -> None:
        if not inline:
            return
        value, ranges = _node_text_with_ranges(
            node,
            children=tuple(inline),
            preserve_lines=node.tag == "pre",
        )
        if value.strip():
            output.append(
                SourceSegment(
                    value,
                    _html_node_locator(node, primary),
                    ranges,
                )
            )
        inline.clear()

    for child in node.children:
        if isinstance(child, _HtmlText):
            inline.append(child)
        elif child.tag in NOISE_ELEMENTS:
            flush()
        elif child.tag in BLOCK_ELEMENTS or child.tag == "table":
            flush()
            _walk_evidence(child, output, primary=primary)
        else:
            inline.append(child)
    flush()


def _node_text_with_ranges(
    node: _HtmlNode,
    *,
    children: tuple[_HtmlNode | _HtmlText, ...] | None = None,
    preserve_lines: bool = False,
) -> tuple[str, tuple[SourceRange, ...]]:
    characters: list[tuple[str, SourceRange]] = []

    def visit(value: _HtmlNode | _HtmlText) -> None:
        if isinstance(value, _HtmlText):
            characters.extend(
                (character, _char_range(value, index))
                for index, character in enumerate(value.text)
            )
        elif value.tag == "br":
            characters.append(("\n", (0, 0)))
        elif value.tag not in NOISE_ELEMENTS and value.tag != "table":
            for child in value.children:
                visit(child)

    if children is None:
        visit(node)
    else:
        for value in children:
            visit(value)
    if not characters:
        return "", ()
    while characters and characters[0][0].isspace():
        characters.pop(0)
    while characters and characters[-1][0].isspace():
        characters.pop()
    normalized = _normalize_inline(characters, preserve_lines=preserve_lines)
    return (
        "".join(character for character, _source_range in normalized),
        tuple(source_range for _character, source_range in normalized),
    )


def _normalize_inline(
    characters: list[tuple[str, SourceRange]],
    *,
    preserve_lines: bool,
) -> list[tuple[str, SourceRange]]:
    output: list[tuple[str, SourceRange]] = []
    pending_space: tuple[str, SourceRange] | None = None
    for character, source_range in characters:
        if character == "\n" and preserve_lines:
            pending_space = None
            output.append(("\n", source_range))
            continue
        if character in {" ", "\t", "\r"}:
            if pending_space is None and (not output or output[-1][0] != "\n"):
                pending_space = (" ", source_range)
            continue
        if character == "\n" and not preserve_lines:
            if output and output[-1][0] != "\n":
                output.append(("\n", source_range))
            pending_space = None
            continue
        if pending_space is not None and output:
            output.append(pending_space)
            pending_space = None
        output.append((character, source_range))
    return output


def _table_segments(table: _HtmlNode, primary: _HtmlNode) -> list[SourceSegment]:
    segments: list[SourceSegment] = []
    for row in _descendants(table, "tr", skip_nested_tables=True):
        cells = [
            cell
            for cell in _descendants(row, *TABLE_CELLS, skip_nested_tables=True)
            if not any(ancestor.tag in TABLE_CELLS for ancestor in _ancestors(cell, row))
        ]
        parts: list[tuple[str, tuple[SourceRange, ...], bool]] = []
        cell_details: list[dict] = []
        for cell in cells:
            text, ranges = _node_text_with_ranges(cell)
            empty = not text
            if not text:
                text = "cell"
                ranges = ((0, 0),) * len(text)
            cell_details.append(
                {
                    "path": list(cell.path),
                    "char_start": cell.source_start,
                    "char_end": cell.source_end,
                    "text": text,
                    "empty": empty,
                }
            )
            parts.append((text, ranges, empty))
        if not parts:
            continue
        text_parts: list[str] = []
        ranges: list[SourceRange] = []
        for index, (part, part_ranges, _empty) in enumerate(parts):
            if index:
                text_parts.extend((" ", "|", " "))
                ranges.append((0, 0))
                ranges.append((0, 0))
                ranges.append((0, 0))
            text_parts.append(part)
            ranges.extend(part_ranges)
        text = "".join(text_parts)
        segments.append(
            SourceSegment(
                text,
                {
                    **_html_node_locator(row, primary),
                    "element": "table-row",
                    "text": text,
                    "cells": cell_details,
                },
                tuple(ranges),
            )
        )
    return segments


def _ancestors(node: _HtmlNode, root: _HtmlNode) -> list[_HtmlNode]:
    ancestors: list[_HtmlNode] = []
    current = root
    for depth in range(len(root.path) + 1, len(node.path)):
        prefix = node.path[:depth]
        match = next(
            (
                child
                for child in current.children
                if isinstance(child, _HtmlNode) and child.path == prefix
            ),
            None,
        )
        if match is None:
            break
        ancestors.append(match)
        current = match
    return ancestors


def _descendants(
    node: _HtmlNode,
    *tags: str,
    skip_nested_tables: bool = False,
) -> list[_HtmlNode]:
    found: list[_HtmlNode] = []
    for child in node.children:
        if not isinstance(child, _HtmlNode):
            continue
        if skip_nested_tables and child.tag == "table":
            continue
        if child.tag in tags:
            found.append(child)
        found.extend(_descendants(child, *tags, skip_nested_tables=skip_nested_tables))
    return found


def _html_node_locator(node: _HtmlNode, primary: _HtmlNode) -> dict:
    return {
        "kind": "html",
        "element": node.tag,
        "path": list(node.path),
        "char_start": node.source_start,
        "char_end": node.source_end,
        "coordinate_unit": "decoded_html_chars",
        "primary": (
            primary.path == node.path
            or primary.path == node.path[: len(primary.path)]
        ),
        "preformatted": node.tag == "pre",
    }


def _index_from_line_column(source: str, line: int, column: int) -> int:
    if line <= 1:
        return column
    offset = 0
    for _ in range(line - 1):
        newline = source.find("\n", offset)
        if newline < 0:
            return len(source)
        offset = newline + 1
    return offset + column
