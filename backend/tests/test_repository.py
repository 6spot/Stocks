import os
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stocks.models import Base, Document, DocumentCollectionState, Source
from stocks.repository import (
    DocumentInput,
    get_collection_state,
    get_latest_document,
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
        s3_access_key="test-access",
        s3_secret_key="test-secret",
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
        "completeness": "complete",
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


def test_ingest_reuses_immutable_version_and_updates_collection_state(
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
        state = get_collection_state(session, "sec", "filing-1")
        assert first.created is True
        assert duplicate.created is False
        assert duplicate.document.id == first.document.id
        assert second.created is True
        assert [document.version for document in versions] == [1, 2]
        assert versions[0].content_type == "text/plain"
        assert versions[1].content_type == "text/html"
        assert versions[0].completeness == "complete"
        assert versions[0].fetch_error is None
        assert versions[0].source_updated_at == datetime(
            2026, 10, 19, 1, tzinfo=timezone.utc
        )
        assert state is not None
        assert state.latest_document_id == versions[1].id
        assert state.source_updated_at == duplicate.document.source_updated_at
        assert len(session.scalars(select(Document)).all()) == 2
        assert len(session.scalars(select(DocumentCollectionState)).all()) == 1
        assert len(calls) == 2
        assert read(config, versions[0].snapshot_key) == b"first version"
        assert read_document_bytes(config, versions[1]) == b"second version"


def test_latest_document_follows_most_recent_acquisition_not_version_order(
    engine, storage, monkeypatch
):
    """A -> B -> A must read back A while keeping both immutable versions."""
    _, calls, store, read = storage
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    config = make_config()

    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="http", trust_tier="T1"))
        session.commit()

        first = ingest_document(session, config, make_item(), store=store)
        second = ingest_document(
            session,
            config,
            make_item(
                payload=b"second version",
                collected_at=datetime(2026, 10, 20, 2, tzinfo=timezone.utc),
            ),
            store=store,
        )
        restored = ingest_document(
            session,
            config,
            make_item(
                title="Restored title",
                collected_at=datetime(2026, 10, 20, 3, tzinfo=timezone.utc),
            ),
            store=store,
        )

        assert restored.created is False
        assert restored.document.id == first.document.id
        assert second.document.version == 2

        latest = get_latest_document(session, "sec", "filing-1")
        state = get_collection_state(session, "sec", "filing-1")
        assert latest is not None and state is not None
        assert latest.id == first.document.id
        assert latest.version == 1
        assert state.latest_document_id == latest.id
        # History stays immutable: the reused version keeps its own acquired bytes
        # and metadata, and the newer version remains readable.
        versions = list_document_versions(session, "sec", "filing-1")
        assert [document.version for document in versions] == [1, 2]
        assert versions[0].title == "Filing"
        assert read_document_bytes(config, versions[0]) == b"first version"
        assert read_document_bytes(config, versions[1]) == b"second version"
    assert len(calls) == 2


def test_latest_document_reports_recovered_content_after_partial(engine, storage, monkeypatch):
    """Partial content then the same complete bytes point at the recovered original."""
    _, calls, store, read = storage
    monkeypatch.setattr("stocks.repository.read_snapshot", read)
    config = make_config()
    complete = b"<html><body>Full release text.</body></html>"
    partial = b'{"title":"Release","summary":"Summary only"}'

    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="http", trust_tier="T1"))
        session.commit()

        full = ingest_document(
            session,
            config,
            make_item(payload=complete, content_type="text/html"),
            store=store,
        )
        degraded = ingest_document(
            session,
            config,
            make_item(
                payload=partial,
                content_type="text/plain; charset=utf-8",
                completeness="partial",
                fetch_error={"code": "HTTP_STATUS", "message": "Unavailable", "status": 503},
                collected_at=datetime(2026, 10, 20, 2, tzinfo=timezone.utc),
            ),
            store=store,
        )
        recovered = ingest_document(
            session,
            config,
            make_item(payload=complete, content_type="text/html"),
            store=store,
        )

        state = get_collection_state(session, "sec", "filing-1")
        latest = get_latest_document(session, "sec", "filing-1")
        assert degraded.created is True
        assert recovered.created is False
        assert recovered.document.id == full.document.id
        assert state is not None and latest is not None
        assert latest.id == full.document.id
        assert state.completeness == "complete"
        assert state.fetch_error is None
        assert read_document_bytes(config, latest) == complete
        assert len(list_document_versions(session, "sec", "filing-1")) == 2


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


def test_partial_content_requires_and_persists_fetch_error(engine, storage):
    _, calls, store, _ = storage
    error = {
        "code": "HTTP_STATUS",
        "message": "Remote server returned an unsuccessful status",
        "url": "https://www.sec.gov/news/example",
        "status": 503,
    }
    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
        session.commit()

        result = ingest_document(
            session,
            make_config(),
            make_item(
                payload=b'{"summary":"available"}',
                content_type="text/plain; charset=utf-8",
                completeness="partial",
                fetch_error=error,
            ),
            store=store,
        )

        assert result.document.completeness == "partial"
        assert result.document.fetch_error == error
        assert len(calls) == 1

        with pytest.raises(ValueError, match="must include fetch_error"):
            ingest_document(
                session,
                make_config(),
                make_item(
                    dedupe_key="missing-error",
                    completeness="partial",
                ),
                store=store,
            )

    assert len(calls) == 1


def test_repeated_partial_bytes_reuse_version_and_update_collection_state(
    engine, storage,
):
    _, calls, store, _ = storage
    first_error = {
        "code": "HTTP_STATUS",
        "message": "Remote server returned an unsuccessful status",
        "status": 503,
    }
    second_error = {
        "code": "TIMEOUT",
        "message": "Request exceeded its timeout",
    }
    payload = b'{"title":"Article","summary":"Available summary"}'
    with Session(engine) as session:
        session.add(Source(id="sec", name="SEC", connector="rss", trust_tier="T1"))
        session.commit()

        partial = ingest_document(
            session,
            make_config(),
            make_item(
                payload=payload,
                content_type="text/plain; charset=utf-8",
                completeness="partial",
                fetch_error=first_error,
            ),
            store=store,
        )
        repeated = ingest_document(
            session,
            make_config(),
            make_item(
                payload=payload,
                content_type="text/plain; charset=utf-8",
                completeness="partial",
                fetch_error=second_error,
            ),
            store=store,
        )

        versions = list_document_versions(session, "sec", "filing-1")
        state = get_collection_state(session, "sec", "filing-1")
        assert partial.created is True
        assert repeated.created is False
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].completeness == "partial"
        assert versions[0].fetch_error == first_error
        assert state is not None
        assert state.completeness == "partial"
        assert state.fetch_error == second_error
    assert len(calls) == 1
