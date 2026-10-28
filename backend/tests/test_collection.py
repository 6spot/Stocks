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
    DohResolver,
    SafeFetcher,
)
from stocks.collection.service import collect_source
from stocks.collection.urls import validate_https_url
from stocks.models import (
    Document,
    DocumentCollectionState,
    Source,
)
from stocks.settings import Settings


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
PDF_ARTICLE = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
)


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_collection_cli_configuration_path_errors(
    tmp_path, monkeypatch, capsys, explicit, invalid
):
    from stocks.collection import __main__ as cli
    from stocks.settings import Settings

    selected = tmp_path / "selected-sources.yaml"
    if invalid:
        selected.write_text("sources: invalid", encoding="utf-8")
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        sources_config_path=str(tmp_path / "wrong.yaml") if explicit else str(selected),
    )
    monkeypatch.setattr(cli, "settings", lambda: config)
    arguments = ["--config", str(selected)] if explicit else []
    assert cli.main(arguments) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == (
        "CONFIG_INVALID" if invalid else "CONFIG_UNAVAILABLE"
    )


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
    ("value", "expected"),
    [
        ("15m", "15m"),
        ("12h", "12h"),
    ],
)
def test_source_expected_freshness_is_optional_and_bounded(
    tmp_path, value, expected
):
    path = tmp_path / "freshness.yaml"
    path.write_text(
        SOURCE_YAML.replace(
            "    trust_tier: T1\n",
            f"    trust_tier: T1\n    expected_freshness: {value}\n",
        ),
        encoding="utf-8",
    )
    assert load_sources(path)[0].expected_freshness == expected


@pytest.mark.parametrize(
    "value",
    ["''", "0m", "15s", "hour", "9999999d", "31d", "../1h"],
)
def test_source_rejects_invalid_expected_freshness(tmp_path, value):
    path = tmp_path / "freshness.yaml"
    path.write_text(
        SOURCE_YAML.replace(
            "    trust_tier: T1\n",
            f"    trust_tier: T1\n    expected_freshness: {value}\n",
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


def test_article_fetch_accepts_a_pdf_original_and_rejects_other_binaries():
    """A feed entry may point at a filing PDF; the bytes are what gets stored."""

    from stocks.collection.http import ARTICLE_CONTENT_TYPES

    fetcher = safe_fetcher(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            stream=httpx.ByteStream(PDF_ARTICLE),
        )
    )
    response = fetcher.fetch(
        "https://www.sec.gov/news/filing.pdf",
        timeout_seconds=5,
        max_response_bytes=1_000_000,
        max_redirects=0,
        accept="application/pdf",
        allowed_content_types=ARTICLE_CONTENT_TYPES,
        user_agent=TEST_USER_AGENT,
    )
    assert response.content_type == "application/pdf"
    assert response.payload == PDF_ARTICLE

    binary = safe_fetcher(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            stream=httpx.ByteStream(b"\x89PNG\r\n\x1a\n"),
        )
    )
    with pytest.raises(CollectionError) as captured:
        binary.fetch(
            "https://www.sec.gov/news/chart.png",
            timeout_seconds=5,
            max_response_bytes=1_000_000,
            max_redirects=0,
            accept="application/pdf",
            allowed_content_types=ARTICLE_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "UNSUPPORTED_CONTENT_TYPE"


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
<!DOCTYPE rss [<!ENTITY x "hunter2">]>
<rss version="2.0"><channel><title>&x;</title></channel></rss>
"""

    with pytest.raises(CollectionError) as captured:
        parse_feed(payload, source, "https://www.sec.gov/feed")

    assert captured.value.code == "INVALID_XML"
    assert "hunter2" not in str(captured.value)


def test_timeout_is_reported_without_raw_request_details():
    def handler(request):
        raise httpx.ReadTimeout("hunter2 response body", request=request)

    fetcher = safe_fetcher(handler)
    with pytest.raises(CollectionError) as captured:
        fetcher.fetch(
            "https://www.sec.gov/feed?token=hunter2",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=0,
            accept="application/xml",
            allowed_content_types=FEED_CONTENT_TYPES,
            user_agent=TEST_USER_AGENT,
        )
    assert captured.value.code == "TIMEOUT"
    assert captured.value.to_dict()["url"] == "https://www.sec.gov/feed"
    assert "hunter2" not in str(captured.value)


def test_error_details_cannot_override_safe_identity_or_url():
    error = CollectionError(
        "REQUEST_FAILED",
        "Remote request failed",
        url="https://www.sec.gov/feed?token=hunter2",
        details={
            "code": "UNTRUSTED",
            "message": "hunter2 response body",
            "url": "https://www.sec.gov/feed?token=hunter2",
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


def test_declared_sources_are_recorded_as_provenance_not_merge_keys(tmp_path):
    """V2-03: a feed's own syndication label never identifies the original."""

    source = make_source(tmp_path)
    payload = RSS.replace(
        b"<description>Policy summary.</description>",
        b'<source url="https://wire.example/feed">Wire Service</source>'
        b'<source url="https://wire.example/feed">Wire Service</source>'
        b"<description>Policy summary.</description>",
    )

    item = parse_feed(payload, source, "https://www.sec.gov/feed").items[0]

    assert item.declared_sources == (
        {"name": "Wire Service", "url": "https://wire.example/feed"},
    )
    # The label describes the publisher, so the article URL stays the entry's.
    assert item.article_url == "https://www.sec.gov/news/release-1"

    atom_payload = ATOM.replace(
        b"<summary>Guidance summary.</summary>",
        b"<source><title>Wire Service</title>"
        b'<link rel="alternate" href="https://wire.example/feed"/></source>'
        b"<summary>Guidance summary.</summary>",
    )
    atom = parse_feed(atom_payload, source, "https://www.sec.gov/feed").items[0]

    assert atom.declared_sources == (
        {"name": "Wire Service", "url": "https://wire.example/feed"},
    )
    assert atom.article_url == "https://www.sec.gov/news/release-2"


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


def test_partial_content_without_source_time_is_retried_on_next_run(
    tmp_path, monkeypatch
):
    """Unknown published/updated signals must not suppress the retry of partial content."""
    from stocks.collection.http import FetchedResponse
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
    from stocks.settings import Settings

    source = make_source(tmp_path)
    no_time_rss = RSS.replace(
        b"<pubDate>Mon, 19 Oct 2026 12:00:00 GMT</pubDate>", b""
    )
    failure = CollectionError(
        "HTTP_STATUS",
        "Remote server returned an unsuccessful status",
        url="https://www.sec.gov/news/release-1",
        details={"status": 503},
    )
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=no_time_rss,
            ),
            "https://www.sec.gov/news/release-1": failure,
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark the update
            # produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
                def all(self):
                    return list(self.documents)

            return Rows()

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: list(session.documents),
    )
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
    )
    session = StubSession()

    first = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 1, tzinfo=timezone.utc),
    )
    assert first.ingested == 1
    assert first.items[0].completeness == "partial"
    state = session.states[(source.source_id, first.items[0].dedupe_key)]
    assert state.published_at is None and state.source_updated_at is None

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
    assert second.skipped == 0
    assert second.ingested == 1
    assert second.items[0].completeness == "complete"
    assert fetcher.requests.count("https://www.sec.gov/news/release-1") == 2

    third = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 20, 3, tzinfo=timezone.utc),
    )
    assert third.skipped == 1
    assert third.failed == 0
    assert fetcher.requests.count("https://www.sec.gov/news/release-1") == 2


def test_malformed_article_url_fails_only_its_own_feed_entry(tmp_path):
    source = make_source(tmp_path)
    payload = RSS.replace(
        b"</channel>",
        b"<item><guid>release-9</guid><title>Broken link</title>"
        b"<link>https://[::1</link>"
        b"<description>Malformed article link.</description></item></channel>",
    )

    parsed = parse_feed(payload, source, "https://www.sec.gov/news/feed")

    assert [item.external_id for item in parsed.items] == ["release-1"]
    assert len(parsed.failures) == 1
    error = parsed.failures[0].error
    assert error.code == "INVALID_ARTICLE_URL"
    assert error.details == {"position": 1, "reason": "malformed_link"}
    assert error.url == "https://www.sec.gov/news/feed"


def test_malformed_redirect_location_is_a_bounded_collection_error():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(301, headers={"Location": "https://[::1hunter2-host"})
        raise AssertionError("redirect target must not be requested")

    fetcher = safe_fetcher(handler)

    with pytest.raises(CollectionError) as caught:
        fetcher.fetch(
            "https://www.sec.gov/start?token=hunter2",
            timeout_seconds=5,
            max_response_bytes=1_000_000,
            max_redirects=3,
            accept="text/html",
            allowed_content_types=["text/html"],
            user_agent=TEST_USER_AGENT,
        )

    payload = caught.value.to_dict()
    assert payload["code"] == "REQUEST_FAILED"
    assert payload["url"] == "https://www.sec.gov/start"
    assert "hunter2" not in str(payload)


def test_declared_charset_survives_fetch_and_allow_list():
    body = (
        b"<html><body><article><p>Inflation eased to 2,4 percent.</p></article>"
        b"</body></html>"
    )

    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=windows-1252"},
            stream=httpx.ByteStream(body),
        )

    fetched = safe_fetcher(handler).fetch(
        "https://www.sec.gov/news/release-1",
        timeout_seconds=5,
        max_response_bytes=1_000_000,
        max_redirects=3,
        accept="text/html",
        allowed_content_types=["text/html"],
        user_agent=TEST_USER_AGENT,
    )

    assert fetched.content_type == "text/html; charset=windows-1252"
    assert fetched.payload == body


def test_collection_stores_declared_charset_for_decoder(tmp_path, monkeypatch):
    from stocks.collection.http import FetchedResponse
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
    from stocks.processing.parser import parse_content
    from stocks.settings import Settings

    source = make_source(tmp_path)
    body = (
        "<html><body><article><p>Le taux d'inflation est retombé à 2,4 %.</p>"
        "</article></body></html>"
    ).encode("windows-1252")
    fetcher = StubFetcher(
        {
            source.feed_url: FetchedResponse(
                url=source.feed_url,
                content_type="application/rss+xml",
                payload=RSS,
            ),
            "https://www.sec.gov/news/release-1": FetchedResponse(
                url="https://www.sec.gov/news/release-1",
                content_type="text/html; charset=windows-1252",
                payload=body,
            ),
        }
    )
    stored = []

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        stored.append((content_type, payload))
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark the update
            # produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
                def all(self):
                    return list(self.documents)

            return Rows()

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: list(session.documents),
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
        clock=lambda: datetime(2026, 10, 20, 1, tzinfo=timezone.utc),
    )

    assert summary.ingested == 1
    assert summary.items[0].completeness == "complete"
    assert stored[0][0] == "text/html; charset=windows-1252"
    assert stored[0][1] == body
    # The declared charset lets the existing parser strictly decode the stored bytes;
    # the original bytes are never re-encoded or replaced with substitution characters.
    document, chunks = parse_content(
        stored[0][1],
        stored[0][0],
        chunk_size=1200,
        chunk_overlap=150,
    )
    assert document.encoding == "windows-1252"
    assert document.content_type == "text/html"
    assert chunks[0].text == "Le taux d'inflation est retombé à 2,4 %."
    assert chunks[0].locator["segments"][0]["locator"]["element"] == "p"
    assert "\ufffd" not in document.text


def test_collection_ingests_full_article_and_skips_unchanged_item(
    tmp_path, monkeypatch
):
    from stocks.collection.http import FetchedResponse
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
    # The stored origin is what the collection saw, so a reprint can be grouped
    # later without guessing from titles or similarity.
    assert session.documents[0].extra["origin"] == {
        "kind": "article_url",
        "key": "article_url:https://sec.gov/news/release-1",
        "url": "https://sec.gov/news/release-1",
        "declared_sources": [],
    }


def test_collection_failure_saves_partial_content_and_retries(
    tmp_path, monkeypatch
):
    from stocks.collection.http import FetchedResponse
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
    from stocks.settings import Settings

    source = make_source(tmp_path)
    failed_article = CollectionError(
        "HTTP_STATUS",
        "Remote server returned an unsuccessful status",
        url="https://www.sec.gov/news/release-1?credential=hunter2",
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark the update
            # produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(value, (DocumentProcessingState, ChangeRecord)):
                # Stage todo rows and change records are written by
                # ``ingest_document``; this stub only models Documents, so they are
                # ignored rather than becoming fake Document rows.
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark the update
            # produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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
    from stocks.models import (
        ChangeRecord,
        Document,
        DocumentCollectionState,
        DocumentProcessingState,
        Source,
    )
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
            if model is Source:
                return Source(
                    id=source.source_id,
                    name=source.name,
                    connector="rss",
                    publisher=source.publisher,
                    trust_tier=source.trust_tier,
                )
            if model is DocumentCollectionState:
                return state
            if model is Document:
                return document if document.id == key else None
            return None

        def add(self, value):
            if isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif not isinstance(
                value, (Source, DocumentProcessingState, ChangeRecord)
            ):
                self.documents.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            # ``ingest_document`` records a committed-visible change in the same
            # transaction, which needs the counter upsert/update path. This stub
            # has no SQL engine, so it only has to report the watermark the update
            # produced.
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

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


STRUCTURED_YAML = """\
sources:
  - source_id: fred_cpiaucsl
    name: FRED CPI
    connector: http_api
    publisher: Federal Reserve Bank of St. Louis
    trust_tier: T1
    api_url: https://api.stlouisfed.org/fred/series/observations
    query:
      series_id: CPIAUCSL
      file_type: json
      api_key: ${FRED_API_KEY}
    timeout_seconds: 30
    max_response_bytes: 1000000
    max_redirects: 3
    max_items: 1
    user_agent: StocksIntelligence/0.1 (owner@example.com)
    mapper:
      id: fred-observations
      fields:
        - kind: numeric
          pointer: /observations/0/value
          fact_type: macro_indicator
          metric_id: cpi_index_sa
          unit: index_1982_84_100
"""

CPI_OBSERVATIONS = b"""\
{"observations": [{"date": "2026-09-01", "value": "322.3"}]}
"""


def make_structured_source(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(STRUCTURED_YAML, encoding="utf-8")
    return load_sources(path)[0]


def test_structured_query_resolves_environment_placeholder(monkeypatch, tmp_path):
    """A credential enters the request only through the environment."""

    source = make_structured_source(tmp_path)
    monkeypatch.setenv("FRED_API_KEY", "32-char-lowercase-alphanumeric-key")

    resolved = source.resolved_query()

    assert resolved == {
        "series_id": "CPIAUCSL",
        "file_type": "json",
        "api_key": "32-char-lowercase-alphanumeric-key",
    }
    # The stored configuration keeps the placeholder, never the value.
    assert source.query["api_key"] == "${FRED_API_KEY}"


def test_structured_query_without_environment_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    source = make_structured_source(tmp_path)

    with pytest.raises(ConfigurationError, match="environment"):
        source.resolved_query()


def test_structured_collection_never_stores_the_request_query(
    monkeypatch, tmp_path
):
    """The credential stays out of Document provenance and Run payloads."""

    from stocks.collection.http import FetchedResponse

    monkeypatch.setenv("FRED_API_KEY", "32-char-lowercase-alphanumeric-key")
    source = make_structured_source(tmp_path)
    request_url = (
        "https://api.stlouisfed.org/fred/series/observations"
        "?api_key=32-char-lowercase-alphanumeric-key&file_type=json&series_id=CPIAUCSL"
    )
    fetcher = StubFetcher(
        {
            request_url: FetchedResponse(
                url=request_url,
                content_type="application/json",
                payload=CPI_OBSERVATIONS,
            )
        }
    )
    stored: list[tuple[str, bytes]] = []

    def store(config, source_id, payload, *, content_type):
        from hashlib import sha256

        digest = sha256(payload).hexdigest()
        stored.append((f"sources/{source_id}/{digest}", payload))
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
            if model is Document:
                return next(
                    (item for item in self.documents if item.id == key), None
                )
            return None

        def add(self, value):
            if isinstance(value, Source):
                self.source = value
            elif isinstance(value, DocumentCollectionState):
                self.states[(value.source_id, value.dedupe_key)] = value
            elif isinstance(value, Document):
                self.documents.append(value)

        def rollback(self):
            return None

        def refresh(self, value):
            return None

        def is_modified(self, value):
            return True

        def flush(self):
            from uuid import uuid4

            for document in self.documents:
                if getattr(document, "id", None) is None:
                    document.id = uuid4()

        def commit(self):
            self.flush()

        def add_all(self, values):
            for value in values:
                self.add(value)

        def execute(self, statement, *args, **kwargs):
            class Result:
                def scalar_one(self):
                    return 1

                def scalar_one_or_none(self):
                    return 1

            return Result()

        def scalar(self, statement):
            return self.documents[-1] if self.documents else None

        def scalars(self, statement):
            class Rows:
                def all(self):
                    return list(self.documents)

            return Rows()

    monkeypatch.setattr(
        "stocks.repository._document_rows",
        lambda session, item: list(session.documents),
    )
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
    )
    session = StubSession()

    summary = collect_source(
        session,
        config,
        source,
        fetcher=fetcher,
        store=store,
        clock=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc),
    )

    assert summary.ingested == 1
    # The request itself carried the key, but nothing registered does.
    assert fetcher.requests == [request_url]
    document = session.documents[-1]
    assert document.original_url == (
        "https://api.stlouisfed.org/fred/series/observations"
    )
    assert "api_key" not in document.original_url
    for item in summary.items:
        assert "api_key" not in (item.url or "")


def test_source_proxy_resolves_environment_and_rejects_invalid_values(
    tmp_path, monkeypatch
):
    path = tmp_path / "sources.yaml"
    path.write_text(
        SOURCE_YAML.replace(
            "user_agent:",
            "proxy_url: ${COLLECTION_PROXY_URL}\n    user_agent:",
        ),
        encoding="utf-8",
    )
    source = load_sources(path)[0]

    monkeypatch.delenv("COLLECTION_PROXY_URL", raising=False)
    with pytest.raises(ConfigurationError) as missing:
        source.resolved_proxy_url()
    assert missing.value.code == "SOURCE_PROXY_UNAVAILABLE"
    assert "proxy.example" not in str(missing.value.to_dict())

    monkeypatch.setenv(
        "COLLECTION_PROXY_URL", "http://user:hunter2@proxy.example:3128"
    )
    assert source.resolved_proxy_url() == "http://user:hunter2@proxy.example:3128"

    monkeypatch.setenv("COLLECTION_PROXY_URL", "http://user:hunter2@")
    with pytest.raises(ConfigurationError) as invalid:
        source.resolved_proxy_url()
    assert invalid.value.code == "SOURCE_PROXY_INVALID"
    assert "hunter2" not in str(invalid.value.to_dict())

    path.write_text(
        SOURCE_YAML.replace(
            "user_agent:", "proxy_url: socks5://proxy.example:1080\n    user_agent:"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_sources(path)


def test_safe_fetcher_uses_configured_proxy(monkeypatch):
    captured: dict[str, object] = {}

    class Stop(Exception):
        pass

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise Stop()

    monkeypatch.setattr("stocks.collection.http.DohResolver", lambda proxy_url: public_resolver)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(Stop):
        SafeFetcher(resolver=public_resolver).fetch(
            "https://example.com/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=1,
            accept="application/rss+xml",
            allowed_content_types=["application/rss+xml"],
            user_agent=TEST_USER_AGENT,
            proxy_url="http://proxy.example:3128",
        )

    assert captured["proxy"] == "http://proxy.example:3128"
    assert "transport" not in captured


def test_invalid_proxy_is_rejected_without_echoing_credentials():
    with pytest.raises(CollectionError) as invalid:
        SafeFetcher(resolver=public_resolver).fetch(
            "https://example.com/feed",
            timeout_seconds=5,
            max_response_bytes=1024,
            max_redirects=1,
            accept="application/rss+xml",
            allowed_content_types=["application/rss+xml"],
            user_agent=TEST_USER_AGENT,
            proxy_url="socks5://user:hunter2@proxy.example:1080",
        )

    assert invalid.value.code == "PROXY_INVALID"
    assert "hunter2" not in str(invalid.value.to_dict())


def test_collect_source_passes_proxy_to_feed_fetch(tmp_path, monkeypatch):
    path = tmp_path / "sources.yaml"
    path.write_text(
        SOURCE_YAML.replace(
            "user_agent:",
            "proxy_url: http://proxy.example:3128\n    user_agent:",
        ),
        encoding="utf-8",
    )
    source = load_sources(path)[0]
    seen: list[str | None] = []

    class Fetcher:
        def fetch(self, url, **kwargs):
            seen.append(kwargs.get("proxy_url"))
            raise CollectionError("STOP", "stop before storage")

    monkeypatch.setattr("stocks.collection.service.sync_source", lambda *args: None)
    with pytest.raises(CollectionError):
        collect_source(
            object(),
            Settings(
                _env_file=None,
                database_url="postgresql+psycopg://unused/unused",
            ),
            source,
            fetcher=Fetcher(),
        )

    assert seen == ["http://proxy.example:3128"]


def test_doh_resolver_reads_a_and_aaaa():
    def handler(request):
        record_type = request.url.params["type"]
        data = (
            {"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]}
            if record_type == "A"
            else {"Status": 0, "Answer": [{"type": 28, "data": "2606:2800:220:1:248:1893:25c8:1946"}]}
        )
        return httpx.Response(200, json=data)

    resolver = DohResolver(
        "http://proxy.example:3128",
        transport=httpx.MockTransport(handler),
    )
    assert resolver("example.com", 443) == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]


def test_structured_collection_passes_proxy_to_api_fetch(monkeypatch):
    from stocks.collection.config import SourceConfig
    from stocks.collection.service import _collect_structured_source

    source = SourceConfig(
        source_id="test_api",
        name="Test API",
        connector="http_api",
        publisher="Test",
        trust_tier="T1",
        api_url="https://api.example.com/data",
        mapper={
            "id": "test",
            "fields": [
                {
                    "kind": "numeric",
                    "pointer": "/value",
                    "fact_type": "metric",
                    "unit": "index",
                }
            ],
        },
        timeout_seconds=10,
        max_response_bytes=1024,
        max_redirects=1,
        max_items=1,
        user_agent=TEST_USER_AGENT,
        proxy_url="http://proxy.example:3128",
    )
    seen: list[str | None] = []

    class Fetcher:
        def fetch(self, url, **kwargs):
            seen.append(kwargs.get("proxy_url"))
            raise CollectionError("STOP", "stop before storage")

    monkeypatch.setattr("stocks.collection.service.sync_source", lambda *args: None)
    summary = _collect_structured_source(
        object(),
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://unused/unused",
        ),
        source,
        fetcher=Fetcher(),
        proxy_url=source.resolved_proxy_url(),
        store=lambda **kwargs: ("key", "hash"),
        clock=None,
    )

    assert summary.failed == 1
    assert seen == ["http://proxy.example:3128"]
