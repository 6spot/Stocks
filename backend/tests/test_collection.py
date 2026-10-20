import gzip
import json
from datetime import datetime, timezone

import httpx
import pytest

from stocks.collection.config import load_sources, validate_user_agent
from stocks.collection.errors import CollectionError, ConfigurationError
from stocks.collection.feed import parse_feed
from stocks.collection.http import (
    FEED_CONTENT_TYPES,
    SafeFetcher,
)
from stocks.collection.service import collect_source
from stocks.collection.urls import validate_https_url


SOURCE_YAML = """\
sources:
  - source_id: sec_press_releases
    name: SEC Press Releases
    connector: rss
    enabled: true
    publisher: U.S. Securities and Exchange Commission
    trust_tier: T1
    feed_url: https://www.sec.gov/news/pressreleases.rss
    timeout_seconds: 30
    max_response_bytes: 5000000
    max_redirects: 3
    max_items: 20
    user_agent: StocksIntelligence/0.1 (owner@example.com)
"""

RSS = b"""\
<?xml version="1.0"?>
<rss version="2.0"><channel><title>SEC</title>
  <item>
    <guid>release-1</guid>
    <title>SEC announces unrelated policy action</title>
    <link>https://www.sec.gov/news/release-1</link>
    <description>Policy summary.</description>
    <pubDate>Mon, 19 Oct 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ATOM = b"""\
<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>SEC</title>
  <entry>
    <id>tag:sec.gov,2026:release-2</id>
    <title>SEC updates disclosure guidance</title>
    <link rel="alternate" href="https://www.sec.gov/news/release-2"/>
    <summary>Guidance summary.</summary>
    <published>2026-10-17T10:00:00Z</published>
    <updated>2026-10-18T12:00:00Z</updated>
  </entry>
</feed>
"""

ARTICLE = b"<html><body><article>Full release text.</article></body></html>"
TEST_USER_AGENT = "StocksTests/1.0 (owner@example.com)"


def make_source(tmp_path, *, max_items=20, max_bytes=5_000_000, redirects=3):
    path = tmp_path / "sources.yaml"
    path.write_text(
        SOURCE_YAML.replace("max_items: 20", f"max_items: {max_items}")
        .replace("max_response_bytes: 5000000", f"max_response_bytes: {max_bytes}")
        .replace("max_redirects: 3", f"max_redirects: {redirects}"),
        encoding="utf-8",
    )
    return load_sources(path)[0]


def public_resolver(host, port):
    return ["93.184.216.34"]


def safe_fetcher(handler, resolver=public_resolver):
    return SafeFetcher(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )


def test_source_config_is_bounded_and_rejects_unknown_fields(tmp_path):
    source = make_source(tmp_path)
    assert source.connector == "rss"
    assert source.trust_tier == "T1"
    assert source.max_items == 20
    assert source.user_agent.endswith("(owner@example.com)")

    path = tmp_path / "unsafe.yaml"
    path.write_text(
        SOURCE_YAML.replace(
            "https://www.sec.gov/news/pressreleases.rss",
            "https://127.0.0.1/feed",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="invalid|not valid YAML"):
        load_sources(path)

    path.write_text(
        SOURCE_YAML.replace("max_items: 20", "max_items: 0"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="invalid|not valid YAML"):
        load_sources(path)

    path.write_text(
        SOURCE_YAML.replace(
            "StocksIntelligence/0.1",
            '"StocksIntelligence/0.1 (owner@example.com)\\r\\nX-Injected: yes"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="invalid|not valid YAML"):
        load_sources(path)

    path.write_text(
        SOURCE_YAML.replace(
            "StocksIntelligence/0.1 (owner@example.com)",
            "StocksIntelligence/0.1",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="invalid"):
        load_sources(path)


@pytest.mark.parametrize(
    "value",
    [
        "StocksIntelligence/0.1",
        "StocksIntelligence/0.1 (owner)",
        "StocksIntelligence/0.1 (owner@example)",
        "StocksIntelligence/0.1 (owner@example.com)\r\nX-Injected: yes",
    ],
)
def test_user_agent_requires_safe_contact_identity(value):
    with pytest.raises(ValueError):
        validate_user_agent(value)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/feed",
        "https://user@www.sec.gov/feed",
        "https://www.sec.gov:8443/feed",
        "https://127.0.0.1/feed",
        "https://10.0.0.1/feed",
        "https://169.254.1.1/feed",
        "https://[::1]/feed",
        "https://0x7f000001/feed",
        "https://2130706433/feed",
    ],
)
def test_url_validation_rejects_unsafe_destinations(url):
    with pytest.raises(CollectionError):
        validate_https_url(url)


def test_redirect_is_revalidated_and_private_target_is_rejected():
    def resolver(host, port):
        if host == "www.sec.gov":
            return ["93.184.216.34"]
        return ["10.0.0.8"]

    def handler(request):
        return httpx.Response(
            302,
            headers={"Location": "https://private.example/article"},
        )

    fetcher = safe_fetcher(handler, resolver)
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=2,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "UNSAFE_ADDRESS"
    assert captured.value.to_dict()["url"] == "https://private.example/article"


def test_dns_answers_must_all_be_public():
    fetcher = safe_fetcher(
        lambda request: httpx.Response(200),
        lambda host, port: ["93.184.216.34", "127.0.0.1"],
    )
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "UNSAFE_ADDRESS"


def test_content_type_and_size_limits_are_enforced():
    fetcher = safe_fetcher(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html>not a feed</html>",
        )
    )
    with pytest.raises(CollectionError) as wrong_type:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert wrong_type.value.code == "UNSUPPORTED_CONTENT_TYPE"

    fetcher = safe_fetcher(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=b"x" * 1025,
        )
    )
    with pytest.raises(CollectionError) as too_large:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert too_large.value.code == "RESPONSE_TOO_LARGE"


def test_compressed_decoded_size_is_bounded():
    compressed = gzip.compress(b"x" * 2048)
    fetcher = safe_fetcher(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "application/xml",
                "Content-Encoding": "gzip",
            },
            stream=httpx.ByteStream(compressed),
        )
    )
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "RESPONSE_TOO_LARGE"


def test_unsafe_xml_entity_is_reported_as_invalid_feed(tmp_path):
    source = make_source(tmp_path)
    payload = b"""\
<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY x "secret">]>
<rss version="2.0"><channel><title>&x;</title></channel></rss>
"""

    with pytest.raises(CollectionError) as captured:
        parse_feed(payload, source, "https://www.sec.gov/feed")

    assert captured.value.code == "INVALID_XML"
    assert "secret" not in str(captured.value)


def test_timeout_is_reported_without_raw_request_details():
    def handler(request):
        raise httpx.ReadTimeout("secret response body", request=request)

    fetcher = safe_fetcher(handler)
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed?token=secret",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "TIMEOUT"
    assert captured.value.to_dict()["url"] == "https://www.sec.gov/feed"
    assert "secret" not in str(captured.value)


def test_error_details_cannot_override_safe_identity_or_url():
    error = CollectionError(
        "REQUEST_FAILED",
        "Remote request failed",
        url="https://www.sec.gov/feed?token=secret",
        details={
            "code": "UNTRUSTED",
            "message": "secret response body",
            "url": "https://www.sec.gov/feed?token=secret",
            "attempt": 2,
        },
    )

    assert error.to_dict() == {
        "attempt": 2,
        "code": "REQUEST_FAILED",
        "message": "Remote request failed",
        "url": "https://www.sec.gov/feed",
    }


def test_safe_fetcher_requires_contact_user_agent_before_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"Content-Type": "application/xml"})

    fetcher = safe_fetcher(handler)
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent="StocksTests/1.0",
        )

    assert captured.value.code == "INVALID_USER_AGENT"
    assert calls == []


def test_rss_and_atom_parsing_preserve_source_times(tmp_path):
    source = make_source(tmp_path)
    rss = parse_feed(RSS, source, "https://www.sec.gov/feed").items[0]
    atom = parse_feed(ATOM, source, "https://www.sec.gov/feed").items[0]

    assert rss.external_id == "release-1"
    assert rss.article_url == "https://www.sec.gov/news/release-1"
    assert rss.published_at == datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    assert atom.external_id == "tag:sec.gov,2026:release-2"
    assert atom.published_at == datetime(2026, 10, 17, 10, tzinfo=timezone.utc)
    assert atom.source_updated_at == datetime(
        2026, 10, 18, 12, tzinfo=timezone.utc
    )
    assert rss.dedupe_key == parse_feed(
        RSS, source, "https://www.sec.gov/feed"
    ).items[0].dedupe_key


def test_feed_time_without_timezone_remains_unknown(tmp_path):
    source = make_source(tmp_path)
    payload = RSS.replace(
        b"Mon, 19 Oct 2026 12:00:00 GMT",
        b"Mon, 19 Oct 2026 12:00:00",
    )

    item = parse_feed(payload, source, "https://www.sec.gov/feed").items[0]

    assert item.published_at is None


def test_atom_self_link_is_not_used_as_article_url(tmp_path):
    source = make_source(tmp_path)
    payload = ATOM.replace(
        b'<link rel="alternate" href="https://www.sec.gov/news/release-2"/>',
        b'<link rel="self" href="https://www.sec.gov/feed"/>',
    )

    item = parse_feed(payload, source, "https://www.sec.gov/feed").items[0]

    assert item.article_url is None


@pytest.mark.parametrize(
    "link",
    [
        "http://www.sec.gov/news/release-1",
        "https://127.0.0.1/release",
        "https://user@www.sec.gov/release",
    ],
)
def test_feed_parser_rejects_unsafe_article_urls(tmp_path, link):
    source = make_source(tmp_path)
    payload = RSS.replace(
        b"https://www.sec.gov/news/release-1", link.encode("utf-8")
    )

    parsed = parse_feed(payload, source, "https://www.sec.gov/feed")

    assert parsed.items == ()
    assert len(parsed.failures) == 1
    assert parsed.failures[0].error.code == "INVALID_ARTICLE_URL"


class StubFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def fetch(self, url, **kwargs):
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def test_collection_ingests_full_article_and_skips_unchanged_item(
    tmp_path, monkeypatch
):
    from stocks.collection.http import FetchedResponse
    from stocks.models import DocumentCollectionState, Source
    from stocks.settings import Settings

    source = make_source(tmp_path)
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=RSS,
            ),
            "https://www.sec.gov/news/release-1": FetchedResponse(
                url="https://www.sec.gov/news/release-1",
                content_type="text/html",
                payload=ARTICLE,
            ),
        }
    )
    objects = {}

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        return key, digest

    class StubSession:
        def __init__(self):
            self.source = None
            self.documents = []
            self.states = {}

        def get(self, model, key):
            if model is Source:
                return self.source
            if model is DocumentCollectionState:
                return self.states.get(key)
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            else:
                self.documents.append(value)

        def flush(self):
            for document in self.documents:
                if getattr(document, "id", None) is None:
                    from uuid import uuid4

                    document.id = uuid4()

        def commit(self):
            self.flush()

        def rollback(self):
            return None

        def scalar(self, statement):
            return self.documents[-1] if self.documents else None

        def scalars(self, statement):
            class Rows:
                def __init__(self, rows):
                    self.rows = rows

                def all(self):
                    return self.rows

            return Rows(self.documents)

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: [
            document
            for document in session.documents
            if document.source_id == item.source_id
            and document.dedupe_key == item.dedupe_key
        ],
    )
    session = StubSession()
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
    )

    first = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 1, tzinfo=timezone.utc),
    )
    second = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 2, tzinfo=timezone.utc),
    )

    assert first.to_dict()["discovered"] == 1
    assert first.ingested == 1
    assert first.failed == 0
    assert first.items[0].completeness == "complete"
    assert second.skipped == 1
    assert second.ingested == 0
    assert fetcher.requests == [
        source.feed_url,
        "https://www.sec.gov/news/release-1",
        source.feed_url,
    ]
    assert session.states[(source.source_id, session.documents[0].dedupe_key)].completeness == "complete"
    assert objects[session.documents[0].snapshot_key] == ARTICLE


def test_collection_failure_saves_partial_content_and_retries(
    tmp_path, monkeypatch
):
    from stocks.collection.http import FetchedResponse
    from stocks.models import DocumentCollectionState, Source
    from stocks.settings import Settings

    source = make_source(tmp_path)
    failed_article = CollectionError(
        "HTTP_STATUS",
        "Remote server returned an unsuccessful status",
        url="https://www.sec.gov/news/release-1?credential=secret",
        details={"status": 503},
    )
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=RSS,
            ),
            "https://www.sec.gov/news/release-1": failed_article,
        }
    )
    objects = {}

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        key = f"sources/{source_id}/{digest}"
        objects[key] = payload
        return key, digest

    class StubSession:
        def __init__(self):
            self.source = None
            self.documents = []
            self.states = {}

        def get(self, model, key):
            if model is Source:
                return self.source
            if model is DocumentCollectionState:
                return self.states.get(key)
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            else:
                self.documents.append(value)

        def flush(self):
            from uuid import uuid4

            for document in self.documents:
                if getattr(document, "id", None) is None:
                    document.id = uuid4()

        def commit(self):
            self.flush()

        def rollback(self):
            return None

        def scalar(self, statement):
            return self.documents[-1] if self.documents else None

        def scalars(self, statement):
            class Rows:
                def __init__(self, rows):
                    self.rows = rows

                def all(self):
                    return self.rows

            return Rows(self.documents)

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: [
            document
            for document in session.documents
            if document.source_id == item.source_id
            and document.dedupe_key == item.dedupe_key
        ],
    )
    session = StubSession()
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
    )

    first = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 1, tzinfo=timezone.utc),
    )
    assert first.failed == 1
    assert first.ingested == 1
    assert first.items[0].completeness == "partial"
    assert first.items[0].error["code"] == "HTTP_STATUS"
    assert first.items[0].error["url"] == "https://www.sec.gov/news/release-1"
    document = session.documents[0]
    assert document.completeness == "partial"
    assert document.fetch_error["status"] == 503
    state = session.states[(source.source_id, document.dedupe_key)]
    assert state.latest_document_id == document.id
    assert state.completeness == "partial"
    assert state.fetch_error["status"] == 503
    partial_payload = json.loads(objects[document.snapshot_key])
    assert partial_payload == {
        "title": "SEC announces unrelated policy action",
        "summary": "Policy summary.",
    }

    fetcher.responses["https://www.sec.gov/news/release-1"] = FetchedResponse(
        url="https://www.sec.gov/news/release-1",
        content_type="text/html",
        payload=ARTICLE,
    )
    second = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 2, tzinfo=timezone.utc),
    )
    assert second.ingested == 1
    assert len(session.documents) == 2
    assert session.documents[-1].version == 2
    assert session.documents[-1].completeness == "complete"
    assert session.documents[-1].fetch_error is None
    assert state.latest_document_id == session.documents[-1].id
    assert state.completeness == "complete"
    assert state.fetch_error is None
    assert objects[session.documents[-1].snapshot_key] == ARTICLE


def test_empty_article_response_is_saved_as_partial(tmp_path, monkeypatch):
    from stocks.collection.http import FetchedResponse
    from stocks.models import DocumentCollectionState, Source
    from stocks.settings import Settings

    source = make_source(tmp_path)
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=RSS,
            ),
            "https://www.sec.gov/news/release-1": FetchedResponse(
                url="https://www.sec.gov/news/release-1",
                content_type="text/html",
                payload=b"",
            ),
        }
    )

    class StubSession:
        def __init__(self):
            self.documents = []
            self.source = None
            self.states = {}

        def get(self, model, key):
            if model is Source:
                return self.source
            if model is DocumentCollectionState:
                return self.states.get(key)
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            else:
                self.documents.append(value)

        def flush(self):
            from uuid import uuid4

            for document in self.documents:
                if document.id is None:
                    document.id = uuid4()

        def commit(self):
            self.flush()

        def scalar(self, statement):
            return None

        def scalars(self, statement):
            class Rows:
                def all(self):
                    return []

            return Rows()

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

        def rollback(self):
            return None

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: [],
    )

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        return f"sources/{source_id}/{digest}", digest

    summary = collect_source(
        StubSession(),
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://unused/unused",
            s3_endpoint_url="https://storage.example",
        ),
        source,
        fetcher=fetcher,
        store=store,
    )

    assert summary.failed == 1
    assert summary.ingested == 1
    assert summary.items[0].completeness == "partial"
    assert summary.items[0].error["code"] == "EMPTY_RESPONSE"


def test_collection_limits_items_without_filtering_relevance(
    tmp_path, monkeypatch
):
    from stocks.collection.http import FetchedResponse
    from stocks.models import DocumentCollectionState, Source
    from stocks.settings import Settings

    source = make_source(tmp_path, max_items=1)
    second_item = RSS.replace(
        b"</channel>",
        b"<item><guid>release-3</guid><title>Second</title>"
        b"<link>https://www.sec.gov/news/release-3</link>"
        b"<description>Second summary.</description></item></channel>",
    )
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=second_item,
            ),
            "https://www.sec.gov/news/release-1": FetchedResponse(
                url="https://www.sec.gov/news/release-1",
                content_type="text/html",
                payload=ARTICLE,
            ),
        }
    )

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        return f"sources/{source_id}/{digest}", digest

    class StubSession:
        def __init__(self):
            self.source = None
            self.documents = []
            self.states = {}

        def get(self, model, key):
            if model is Source:
                return self.source
            if model is DocumentCollectionState:
                return self.states.get(key)
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            else:
                self.documents.append(value)

        def flush(self):
            from uuid import uuid4

            for document in self.documents:
                if getattr(document, "id", None) is None:
                    document.id = uuid4()

        def commit(self):
            self.flush()

        def rollback(self):
            return None

        def scalar(self, statement):
            return self.documents[-1] if self.documents else None

        def scalars(self, statement):
            class Rows:
                def __init__(self, rows):
                    self.rows = rows

                def all(self):
                    return self.rows

            return Rows(self.documents)

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: [
            document
            for document in session.documents
            if document.source_id == item.source_id
            and document.dedupe_key == item.dedupe_key
        ],
    )
    summary = collect_source(
        StubSession(),
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://unused/unused",
            s3_endpoint_url="https://storage.example",
        ),
        source,
        fetcher=fetcher,
        store=store,
    )
    assert summary.discovered == 1
    assert summary.ingested == 1
    assert fetcher.requests == [
        source.feed_url,
        "https://www.sec.gov/news/release-1",
    ]


def test_pubdate_change_triggers_recheck(tmp_path, monkeypatch):
    from stocks.collection.http import FetchedResponse
    from stocks.models import Document, DocumentCollectionState, Source
    from stocks.settings import Settings

    source = make_source(tmp_path)
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=RSS,
            ),
            "https://www.sec.gov/news/release-1": FetchedResponse(
                url="https://www.sec.gov/news/release-1",
                content_type="text/html",
                payload=ARTICLE,
            ),
        }
    )
    document = Document(
        source_id=source.source_id,
        dedupe_key=parse_feed(RSS, source, source.feed_url).items[0].dedupe_key,
        version=1,
        title="Existing",
        original_url="https://www.sec.gov/news/release-1",
        published_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
        source_updated_at=None,
        collected_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        completeness="complete",
        content_type="text/html",
        content_size=1,
        content_hash="a" * 64,
        snapshot_key="sources/sec/a",
        trust_tier_snapshot="T1",
    )
    from uuid import uuid4

    document.id = uuid4()
    state = DocumentCollectionState(
        source_id=source.source_id,
        dedupe_key=document.dedupe_key,
        latest_document_id=document.id,
        completeness="complete",
        published_at=document.published_at,
        source_updated_at=None,
        last_attempted_at=document.collected_at,
    )

    class StubSession:
        def __init__(self):
            self.documents = [document]
            self.states = {
                (state.source_id, state.dedupe_key): state,
            }

        def get(self, model, key):
            return Source(
                id=source.source_id,
                name=source.name,
                connector="rss",
                publisher=source.publisher,
                trust_tier=source.trust_tier,
            ) if model is Source else (
                state if model is DocumentCollectionState else None
            )

        def add(self, value):
            if isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, Source):
                self.documents.append(value)

        def flush(self):
            from uuid import uuid4

            for document in self.documents:
                if getattr(document, "id", None) is None:
                    document.id = uuid4()

        def commit(self):
            self.flush()

        def scalar(self, statement):
            return self.documents[-1]

        def scalars(self, statement):
            class Rows:
                def all(self):
                    return self.documents

            return Rows()

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return False

        def rollback(self):
            return None

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: list(session.documents),
    )

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        return f"sources/{source_id}/{digest}", digest

    summary = collect_source(
        StubSession(),
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://unused/unused",
            s3_endpoint_url="https://storage.example",
        ),
        source,
        fetcher=fetcher,
        store=store,
    )

    assert summary.ingested == 1
    assert fetcher.requests == [
        source.feed_url,
        "https://www.sec.gov/news/release-1",
    ]
