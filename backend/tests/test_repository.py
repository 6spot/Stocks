import os
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.models import Base, Document, Source
from stocks.repository import (
    DocumentInput,
    ingest_document,
    list_document_versions,
    read_document_bytes,
)
from stocks.settings import Settings


def make_config() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="evidence",
    )


def make_item(**overrides) -> DocumentInput:
    values = {
        "source_id": "sec",
        "dedupe_key": "filing-1",
        "title": "Filing",
        "payload": b"first version",
        "content_type": "text/plain",
        "collected_at": datetime(2026, 10, 20, 1, tzinfo=timezone.utc),
        "published_at": datetime(2026, 10, 19, tzinfo=timezone.utc),
        "source_updated_at": datetime(2026, 10, 19, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return DocumentInput(**values)


@pytest.fixture
def engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository tests")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


@pytest.fixture
def storage():
    objects: dict[str, bytes] = {}
    calls = []

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        calls.append((key, content_type))
        return key, digest

    def read(config, key):
        return objects[key]

    return objects, calls, store, read


def test_ingest_preserves_versions_and_ignores_metadata_only_update(
    engine, storage, monkeypatch
):
    _, calls, store, read = storage
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    config = make_config()

    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="http", trust_tier="T1"))
        session.commit()

        first = ingest_document(session, config, make_item(), store=store)
        duplicate = ingest_document(
            session,
            config,
            make_item(
                source_updated_at=datetime(
                    2026, 10, 20, 2, tzinfo=timezone.utc
                )
            ),
            store=store,
        )
        second = ingest_document(
            session,
            config,
            make_item(
                payload=b"second version",
                content_type="text/html",
                collected_at=datetime(
                    2026, 10, 20, 3, tzinfo=timezone.utc
                ),
            ),
            store=store,
        )

        versions = list_document_versions(session, "sec", "filing-1")
        assert first.created is True
        assert duplicate.created is False
        assert duplicate.document.id == first.document.id
        assert second.created is True
        assert [document.version for document in versions] == [1, 2]
        assert versions[0].content_type == "text/plain"
        assert versions[1].content_type == "text/html"
        assert len(session.scalars(select(Document)).all()) == 2
        assert len(calls) == 2
        assert read(config, versions[0].snapshot_key) == b"first version"
        assert read_document_bytes(config, versions[1]) == b"second version"


def test_unknown_source_is_rejected_before_storage(engine, storage):
    _, calls, store, _ = storage

    with Session(engine) as session, pytest.raises(ValueError, match="Unknown source"):
        ingest_document(session, make_config(), make_item(), store=store)

    assert calls == []


def test_storage_failure_does_not_create_document(engine):
    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="http", trust_tier="T1"))
        session.commit()
        failing_store = Mock(side_effect=RuntimeError("storage unavailable"))

        with pytest.raises(RuntimeError, match="storage unavailable"):
            ingest_document(
                session,
                make_config(),
                make_item(),
                store=failing_store,
            )

        assert session.scalars(select(Document)).all() == []


def test_invalid_identity_is_rejected_before_storage(engine, storage):
    _, calls, store, _ = storage
    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="http", trust_tier="T1"))
        session.commit()

        with pytest.raises(ValueError, match="dedupe_key"):
            ingest_document(
                session,
                make_config(),
                make_item(dedupe_key=" "),
                store=store,
            )

    assert calls == []
