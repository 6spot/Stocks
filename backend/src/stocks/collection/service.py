from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy.orm import Session

from stocks.collection.config import SourceConfig
from stocks.collection.errors import CollectionError, sanitize_url
from stocks.collection.feed import FeedItem, parse_feed
from stocks.collection.http import (
    ARTICLE_CONTENT_TYPES,
    FEED_CONTENT_TYPES,
    STRUCTURED_CONTENT_TYPES,
    SafeFetcher,
)
from stocks.models import DocumentCollectionState, Source
from stocks.repository import (
    DocumentInput,
    get_collection_state,
    get_latest_document,
    ingest_document,
)
from stocks.s3 import store_snapshot
from stocks.settings import Settings

logger = logging.getLogger(__name__)

FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/rdf+xml, "
    "application/xml;q=0.9, text/xml;q=0.8"
)
ARTICLE_ACCEPT = (
    "text/html, application/xhtml+xml, text/plain;q=0.9, application/pdf;q=0.8"
)
STRUCTURED_ACCEPT = "application/json"


@dataclass
class CollectionItemResult:
    title: str
    url: str | None
    status: str
    dedupe_key: str | None = None
    completeness: str | None = None
    document_id: UUID | None = None
    version: int | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "title": self.title,
            "url": sanitize_url(self.url),
            "status": self.status,
        }
        if self.dedupe_key:
            result["dedupe_key"] = self.dedupe_key
        if self.completeness:
            result["completeness"] = self.completeness
        if self.document_id:
            result["document_id"] = str(self.document_id)
        if self.version is not None:
            result["version"] = self.version
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class CollectionSummary:
    source_id: str
    discovered: int = 0
    ingested: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    items: list[CollectionItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "discovered": self.discovered,
            "ingested": self.ingested,
            "reused": self.reused,
            "skipped": self.skipped,
            "failed": self.failed,
            "items": [item.to_dict() for item in self.items],
        }


def collect_source(
    session: Session,
    config: Settings,
    source: SourceConfig,
    *,
    fetcher: SafeFetcher | None = None,
    store: Callable[..., tuple[str, str]] = store_snapshot,
    clock: Callable[[], datetime] | None = None,
) -> CollectionSummary:
    """Collect one configured source into the Repository.

    A feed source discovers entries and fetches each article; an API source makes
    one bounded request, stores the raw response as a Document version and leaves
    field mapping to the ``facts`` stage. Both go through the same
    ``ingest_document`` entry point and the same idempotency rules, so nothing here
    branches into a second storage path.
    """

    fetcher = fetcher or SafeFetcher()
    if source.connector == "http_api":
        return _collect_structured_source(
            session, config, source, fetcher=fetcher, store=store, clock=clock
        )
    clock = clock or (lambda: datetime.now(timezone.utc))
    summary = CollectionSummary(source_id=source.source_id)
    sync_source(session, source)

    feed_response = fetcher.fetch(
        source.feed_url,
        timeout_seconds=source.timeout_seconds,
        max_response_bytes=source.max_response_bytes,
        max_redirects=source.max_redirects,
        accept=FEED_ACCEPT,
        allowed_content_types=FEED_CONTENT_TYPES,
        user_agent=source.user_agent,
    )
    discovery = parse_feed(feed_response.payload, source, feed_response.url)

    events: list[tuple[int, str, FeedItem | CollectionError]] = [
        (item.position, "item", item) for item in discovery.items
    ]
    events.extend(
        (failure.position, "failure", failure.error)
        for failure in discovery.failures
    )
    events.sort(key=lambda event: event[0])
    selected = events[: source.max_items]
    summary.discovered = len(selected)

    for _, kind, value in selected:
        if kind == "failure":
            error = value
            assert isinstance(error, CollectionError)
            summary.failed += 1
            summary.items.append(
                CollectionItemResult(
                    title="(invalid feed entry)",
                    url=None,
                    status="failed",
                    error=error.to_dict(),
                )
            )
            continue

        item = value
        assert isinstance(item, FeedItem)
        latest = get_latest_document(session, source.source_id, item.dedupe_key)
        state = get_collection_state(session, source.source_id, item.dedupe_key)
        if latest is not None and state is not None and not _should_recheck(item, state):
            summary.skipped += 1
            summary.items.append(
                CollectionItemResult(
                    title=item.title,
                    url=item.article_url,
                    status="skipped",
                    dedupe_key=item.dedupe_key,
                    completeness=state.completeness,
                    document_id=latest.id,
                    version=latest.version,
                )
            )
            continue

        fetch_error: CollectionError | None = None
        if not item.article_url:
            fetch_error = CollectionError(
                "MISSING_ARTICLE_URL",
                "Feed entry has no article URL",
                details={"dedupe_key": item.dedupe_key},
            )
            payload = _partial_payload(item)
            content_type = "text/plain; charset=utf-8"
            collected_at = clock()
            completeness = "partial"
        else:
            try:
                article = fetcher.fetch(
                    item.article_url,
                    timeout_seconds=source.timeout_seconds,
                    max_response_bytes=source.max_response_bytes,
                    max_redirects=source.max_redirects,
                    accept=ARTICLE_ACCEPT,
                    allowed_content_types=ARTICLE_CONTENT_TYPES,
                    user_agent=source.user_agent,
                )
            except CollectionError as exc:
                fetch_error = exc
                payload = _partial_payload(item)
                content_type = "text/plain; charset=utf-8"
                collected_at = clock()
                completeness = "partial"
            else:
                payload = article.payload
                content_type = article.content_type
                collected_at = clock()
                if payload:
                    completeness = "complete"
                else:
                    fetch_error = CollectionError(
                        "EMPTY_RESPONSE",
                        "Article response did not contain any bytes",
                        url=article.url,
                    )
                    payload = _partial_payload(item)
                    content_type = "text/plain; charset=utf-8"
                    completeness = "partial"

        if fetch_error is not None:
            summary.failed += 1
        item_input = DocumentInput(
            source_id=source.source_id,
            dedupe_key=item.dedupe_key,
            title=item.title,
            payload=payload,
            content_type=content_type,
            collected_at=collected_at,
            original_url=item.article_url,
            published_at=item.published_at,
            source_updated_at=item.source_updated_at,
            summary=item.summary,
            completeness=completeness,
            fetch_error=fetch_error.to_dict() if fetch_error else None,
        )
        try:
            ingested = ingest_document(session, config, item_input, store=store)
        except Exception as exc:
            session.rollback()
            logger.warning(
                "Collection ingestion failed for source=%s dedupe_key=%s error_type=%s",
                source.source_id,
                item.dedupe_key,
                type(exc).__name__,
            )
            error = CollectionError(
                "INGEST_FAILED",
                "Fetched content could not be registered in the repository",
                url=item.article_url,
            )
            if fetch_error is None:
                summary.failed += 1
            summary.items.append(
                CollectionItemResult(
                    title=item.title,
                    url=item.article_url,
                    status="failed",
                    dedupe_key=item.dedupe_key,
                    completeness=completeness,
                    error=error.to_dict(),
                )
            )
            continue

        if ingested.created:
            summary.ingested += 1
            status = "ingested"
        else:
            summary.reused += 1
            status = "reused"
        summary.items.append(
            CollectionItemResult(
                title=item.title,
                url=item.article_url,
                status=status,
                dedupe_key=item.dedupe_key,
                completeness=completeness,
                document_id=ingested.document.id,
                version=ingested.document.version,
                error=fetch_error.to_dict() if fetch_error else None,
            )
        )
    return summary


def sync_source(session: Session, source: SourceConfig) -> None:
    record = session.get(Source, source.source_id)
    if record is None:
        record = Source(
            id=source.source_id,
            name=source.name,
            connector=source.connector,
            publisher=source.publisher,
            trust_tier=source.trust_tier,
        )
        session.add(record)
    else:
        record.name = source.name
        record.connector = source.connector
        record.publisher = source.publisher
        record.trust_tier = source.trust_tier
    session.commit()


def _should_recheck(
    item: FeedItem, state: DocumentCollectionState
) -> bool:
    # Partial content only carries the Feed title and summary, so every later run
    # must attempt the article again even when the source gives no change signal.
    if state.completeness == "partial":
        return True
    if item.source_updated_at is not None:
        return state.source_updated_at != item.source_updated_at
    if item.published_at is not None:
        return state.published_at != item.published_at
    return False


def _partial_payload(item: FeedItem) -> bytes:
    payload = {
        "title": item.title,
        "summary": item.summary or "",
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")



def _collect_structured_source(
    session: Session,
    config: Settings,
    source: SourceConfig,
    *,
    fetcher: SafeFetcher,
    store: Callable[..., tuple[str, str]],
    clock: Callable[[], datetime] | None,
) -> CollectionSummary:
    """Fetch one API response and register it as a raw Document version.

    The response is stored byte-for-byte with its own content type. Deduplication
    uses the response hash, so an unchanged payload reuses the existing version
    while a changed payload becomes the next version of the same item. Nothing is
    parsed here: this only produces evidence the ``facts`` stage can map.
    """

    clock = clock or (lambda: datetime.now(timezone.utc))
    summary = CollectionSummary(source_id=source.source_id)
    sync_source(session, source)
    url = _with_query(source.api_url or "", source.query)
    dedupe_key = source.source_id
    try:
        response = fetcher.fetch(
            url,
            timeout_seconds=source.timeout_seconds,
            max_response_bytes=source.max_response_bytes,
            max_redirects=source.max_redirects,
            accept=STRUCTURED_ACCEPT,
            allowed_content_types=STRUCTURED_CONTENT_TYPES,
            user_agent=source.user_agent,
        )
    except CollectionError as exc:
        summary.discovered = 1
        summary.failed = 1
        summary.items.append(
            CollectionItemResult(
                title=source.name,
                url=url,
                status="failed",
                dedupe_key=dedupe_key,
                error=exc.to_dict(),
            )
        )
        return summary

    summary.discovered = 1
    if not response.payload:
        summary.failed = 1
        summary.items.append(
            CollectionItemResult(
                title=source.name,
                url=url,
                status="failed",
                dedupe_key=dedupe_key,
                error={
                    "code": "EMPTY_RESPONSE",
                    "message": "Structured response did not contain any bytes",
                },
            )
        )
        return summary

    item_input = DocumentInput(
        source_id=source.source_id,
        dedupe_key=dedupe_key,
        title=source.name,
        payload=response.payload,
        content_type=response.content_type,
        collected_at=clock(),
        original_url=url,
        completeness="complete",
    )
    try:
        ingested = ingest_document(session, config, item_input, store=store)
    except Exception as exc:  # noqa: BLE001 - one source failure, not a crash
        session.rollback()
        logger.warning(
            "Structured collection failed for source=%s error_type=%s",
            source.source_id,
            type(exc).__name__,
        )
        summary.failed += 1
        summary.items.append(
            CollectionItemResult(
                title=source.name,
                url=url,
                status="failed",
                dedupe_key=dedupe_key,
                error={
                    "code": "INGEST_FAILED",
                    "message": "Fetched response could not be registered",
                },
            )
        )
        return summary

    if ingested.created:
        summary.ingested += 1
        status = "ingested"
    else:
        summary.reused += 1
        status = "reused"
    summary.items.append(
        CollectionItemResult(
            title=source.name,
            url=url,
            status=status,
            dedupe_key=dedupe_key,
            completeness="complete",
            document_id=ingested.document.id,
            version=ingested.document.version,
        )
    )
    return summary


def _with_query(url: str, query: dict[str, str]) -> str:
    """Attach configured query parameters without duplicating an existing one."""

    if not query:
        return url
    parsed = urlsplit(url)
    existing = parse_qsl(parsed.query, keep_blank_values=True)
    merged = list(existing) + [
        (key, value) for key, value in sorted(query.items())
        if key not in {name for name, _ in existing}
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(merged), parsed.fragment)
    )
