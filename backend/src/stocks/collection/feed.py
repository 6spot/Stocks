from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from stocks.collection.config import SourceConfig
from stocks.collection.errors import CollectionError
from stocks.collection.urls import validate_https_url


@dataclass(frozen=True)
class FeedItem:
    source_id: str
    position: int
    external_id: str | None
    dedupe_key: str
    title: str
    article_url: str | None
    summary: str | None
    published_at: datetime | None
    source_updated_at: datetime | None


@dataclass(frozen=True)
class FeedItemFailure:
    position: int
    error: CollectionError


@dataclass(frozen=True)
class FeedParseResult:
    items: tuple[FeedItem, ...]
    failures: tuple[FeedItemFailure, ...]


def parse_feed(payload: bytes, source: SourceConfig, base_url: str) -> FeedParseResult:
    try:
        root = ElementTree.fromstring(payload)
    except (ParseError, ValueError, DefusedXmlException) as exc:
        raise CollectionError(
            "INVALID_XML",
            "Feed response is not valid XML",
            url=base_url,
        ) from exc

    root_name = _local_name(root.tag)
    if root_name == "rss":
        container = _first_child(root, "channel")
        if container is None:
            raise CollectionError("INVALID_FEED", "RSS feed has no channel", url=base_url)
        entries = _children(container, "item")
        is_atom = False
    elif root_name == "feed":
        container = root
        entries = _children(root, "entry")
        is_atom = True
    elif root_name == "RDF":
        container = root
        entries = _children(root, "item")
        is_atom = False
    else:
        raise CollectionError(
            "INVALID_FEED",
            "Response is not an RSS or Atom feed",
            url=base_url,
        )

    items: list[FeedItem] = []
    failures: list[FeedItemFailure] = []
    for position, entry in enumerate(entries):
        try:
            items.append(_parse_entry(entry, source, base_url, position, is_atom))
        except CollectionError as exc:
            failures.append(FeedItemFailure(position=position, error=exc))
    return FeedParseResult(items=tuple(items), failures=tuple(failures))


def _parse_entry(
    entry: ElementTree.Element,
    source: SourceConfig,
    base_url: str,
    position: int,
    is_atom: bool,
) -> FeedItem:
    title = _text(entry, "title") or ""
    if not title:
        raise CollectionError(
            "INVALID_FEED_ITEM",
            "Feed entry has no title",
            url=base_url,
            details={"position": position},
        )

    if is_atom:
        external_id = _text(entry, "id")
        link = _atom_link(entry)
        summary = _text(entry, "summary") or _text(entry, "content")
        published = _parse_datetime(_text(entry, "published"))
        updated = _parse_datetime(_text(entry, "updated"))
    else:
        external_id = _text(entry, "guid")
        link = _text(entry, "link")
        summary = (
            _text(entry, "description")
            or _text(entry, "encoded")
            or _text(entry, "summary")
        )
        published = _parse_datetime(
            _text(entry, "pubDate") or _text(entry, "date")
        )
        updated = _parse_datetime(
            _text(entry, "updated") or _text(entry, "modified")
        )

    article_url: str | None = None
    if link and link.strip():
        try:
            article_url = urljoin(base_url, link.strip())
        except ValueError as exc:
            raise CollectionError(
                "INVALID_ARTICLE_URL",
                "Feed entry article URL could not be resolved",
                url=base_url,
                details={"position": position, "reason": "malformed_link"},
            ) from exc
        try:
            validate_https_url(article_url)
        except CollectionError as exc:
            raise CollectionError(
                "INVALID_ARTICLE_URL",
                "Feed entry article URL is not allowed",
                url=article_url,
                details={"position": position, "reason": exc.code},
            ) from exc
    identity = (external_id or "").strip() or article_url
    if not identity:
        raise CollectionError(
            "INVALID_FEED_ITEM",
            "Feed entry has no stable identifier or article URL",
            url=base_url,
            details={"position": position},
        )
    dedupe_key = hashlib.sha256(
        f"{source.source_id}\0{identity}".encode("utf-8")
    ).hexdigest()
    return FeedItem(
        source_id=source.source_id,
        position=position,
        external_id=(external_id or "").strip() or None,
        dedupe_key=dedupe_key,
        title=title,
        article_url=article_url,
        summary=summary,
        published_at=published,
        source_updated_at=updated,
    )


def _atom_link(entry: ElementTree.Element) -> str | None:
    links = _children(entry, "link")
    preferred = next(
        (
            link
            for link in links
            if link.attrib.get("rel", "alternate") == "alternate"
        ),
        None,
    )
    return preferred.attrib.get("href") if preferred is not None else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _text(element: ElementTree.Element, name: str) -> str | None:
    child = _first_child(element, name)
    if child is None:
        return None
    text = "".join(child.itertext()).strip()
    return text or None


def _first_child(
    element: ElementTree.Element, name: str
) -> ElementTree.Element | None:
    return next(
        (child for child in list(element) if _local_name(child.tag) == name),
        None,
    )


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
