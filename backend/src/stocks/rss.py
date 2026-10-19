import calendar
import hashlib
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import feedparser
import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stocks.models import Document, Source
from stocks.s3 import store_snapshot
from stocks.settings import Settings

class RssSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    name: str
    connector: Literal["rss"]
    enabled: bool = True
    publisher: str | None = None
    trust_tier: str = Field(default="T4", pattern=r"^T[1-4]$")
    url: str

def configured_sources(path: str) -> list[RssSource]:
    with open(path, encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    return [RssSource.model_validate(source) for source in payload.get("sources", [])]

def fetch_feed(url: str, max_bytes: int = 5_000_000) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Configured RSS URL must use HTTPS")
    # Configured sources are owner-controlled. Do not follow redirects.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "StocksIntelligence/0.1"}) as response:
            response.raise_for_status()
            pieces = []
            size = 0
            for part in response.iter_bytes():
                size += len(part)
                if size > max_bytes:
                    raise ValueError("RSS feed exceeded maximum allowed size")
                pieces.append(part)
    return b"".join(pieces)

def parse_feed(raw: bytes) -> list[dict]:
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        raise ValueError("Invalid RSS/Atom response")
    records = []
    for entry in parsed.entries:
        title = str(entry.get("title", "(untitled)"))
        summary = str(entry.get("summary", ""))
        uid = str(entry.get("id") or entry.get("link") or "")
        if not uid:
            uid = hashlib.sha256((title + "|" + summary).encode()).hexdigest()
        published_time = entry.get("published_parsed")
        updated_time = entry.get("updated_parsed")
        published = (
            datetime.fromtimestamp(calendar.timegm(published_time), timezone.utc)
            if published_time else None
        )
        updated = (
            datetime.fromtimestamp(calendar.timegm(updated_time), timezone.utc)
            if updated_time else None
        )
        # Some feeds alias the publication date as their update date.
        # A matching date is not evidence of a later revision.
        if updated == published:
            updated = None
        item_hash = hashlib.sha256((title + "|" + summary).encode()).hexdigest()
        records.append({
            # The feed entry ID locates a story; changed content is a new snapshot.
            "dedupe_key": hashlib.sha256((uid + "|" + item_hash).encode()).hexdigest(),
            "title": title,
            "original_url": entry.get("link"),
            "summary": summary,
            "published_at": published,
            "source_updated_at": updated,
            "content_hash": item_hash,
        })
    return records

def ingest_rss(source: RssSource, config: Settings, session: Session) -> int:
    raw = fetch_feed(source.url)
    records = parse_feed(raw)
    snapshot_key, _ = store_snapshot(config, source.id, raw)
    statement = insert(Source).values(
        id=source.id, name=source.name, connector="rss",
        publisher=source.publisher, trust_tier=source.trust_tier,
    ).on_conflict_do_update(index_elements=["id"], set_={
        "name": source.name, "publisher": source.publisher,
        "trust_tier": source.trust_tier,
    })
    try:
        session.execute(statement)
        inserted = 0
        for record in records:
            statement = insert(Document).values(
                id=uuid4(),
                source_id=source.id,
                snapshot_key=snapshot_key,
                trust_tier_snapshot=source.trust_tier,
                extra={},
                **record,
            ).on_conflict_do_nothing(constraint="uq_documents_source_dedupe")
            result = session.execute(statement)
            inserted += result.rowcount or 0
        session.commit()
        return inserted
    except Exception:
        session.rollback()
        raise
