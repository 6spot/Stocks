"""Explicit origin declarations that identify two copies of one original.

Reporting volume must never be mistaken for independent confirmation, so the
system merges two Documents only when the sources themselves declare the same
original: an HTML ``<link rel="canonical">``, the same article URL reached
through a second feed, or an identical absolute http(s) Atom ``id`` / RSS
``guid``. An opaque, non-URL id stays unusable as a merge key rather than risk
joining two unrelated articles that happen to share a short string.

Titles, summaries and vector similarity are deliberately absent here: they
cannot confirm that two articles are the same original, so they never merge
anything. A feed-level ``<source>`` element is recorded as provenance ("this
item was syndicated from …") but is not a merge key, because one channel
publishes many different articles.

Normalization keeps the comparison deterministic: the scheme collapses to
``https``, the host loses ``www.`` and its default port, tracking query
parameters are dropped, the query is sorted, and the fragment and trailing slash
are removed. Nothing is fetched or guessed — an origin key is only ever built
from a value the source already wrote down.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit

ORIGIN_KINDS = ("canonical_url", "article_url", "external_id")

HTML_CONTENT_TYPES = frozenset({"application/xhtml+xml", "text/html"})

_CHARSET = re.compile(
    r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE
)
_TRACKING_PARAMETERS = frozenset(
    {
        "cmpid",
        "fbclid",
        "gclid",
        "igshid",
        "ito",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ocid",
        "ref",
        "ref_src",
        "s_kwcid",
        "yclid",
    }
)
_TRACKING_PREFIXES = ("utm_", "pk_", "piwik_")


def _drop_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMETERS or lowered.startswith(_TRACKING_PREFIXES)


def normalize_origin_url(value: str | None) -> str | None:
    """One canonical comparison form for an absolute http(s) address."""

    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if port is not None and port not in {80, 443}:
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _drop_tracking(name)
        )
    )
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class _CanonicalLinkParser(HTMLParser):
    """Collect ``<link rel="canonical" href>`` without running any markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.href is not None or tag.lower() != "link":
            return
        values = {name.lower(): value for name, value in attrs if value is not None}
        rel = (values.get("rel") or "").lower().split()
        if "canonical" in rel and values.get("href"):
            self.href = values["href"]


def _decode_html(payload: bytes, content_type: str) -> str | None:
    charset: str | None = None
    for value in (content_type, payload[:2048].decode("ascii", "ignore")):
        match = _CHARSET.search(value or "")
        if match is not None:
            charset = match.group(1).strip().lower()
            break
    try:
        return payload.decode(charset or "utf-8", errors="strict")
    except (LookupError, UnicodeDecodeError):
        return None


def canonical_href(payload: bytes, content_type: str) -> str | None:
    """The declared canonical URL of a stored HTML page, if it declares one."""

    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in HTML_CONTENT_TYPES:
        return None
    text = _decode_html(payload, content_type)
    if text is None:
        return None
    parser = _CanonicalLinkParser()
    try:
        parser.feed(text)
    except (ValueError, AssertionError):
        return None
    return parser.href


def declared_sources(values: list[dict] | None) -> list[dict]:
    """Keep the recorded syndication labels, bounded and deduplicated."""

    output: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "").strip()[:200]
        url = str(value.get("url") or "").strip()[:2048]
        if not name and not url:
            continue
        key = (name, url)
        if key in seen:
            continue
        seen.add(key)
        output.append({"name": name, "url": url})
        if len(output) >= 5:
            break
    return output


def document_origin(
    *,
    article_url: str | None,
    payload: bytes,
    content_type: str,
    declared: list[dict] | None = None,
    external_id: str | None = None,
) -> dict | None:
    """Build the stored origin declaration for one collected Document.

    Precedence follows how explicit each signal is for *this* document: a
    canonical link names the original page, the article URL is where the bytes
    were fetched, and an absolute feed id identifies the entry across feeds.
    """

    labels = declared_sources(declared)
    canonical = normalize_origin_url(canonical_href(payload, content_type))
    if canonical is not None:
        return _origin("canonical_url", canonical, canonical, labels)
    article = normalize_origin_url(article_url)
    if article is not None:
        return _origin("article_url", article, article, labels)
    external = normalize_origin_url(external_id)
    if external is not None:
        return _origin("external_id", external, external, labels)
    return None


def _origin(
    kind: str, key: str, url: str, labels: list[dict]
) -> dict:
    return {
        "kind": kind,
        "key": f"{kind}:{key}",
        "url": url,
        "declared_sources": labels,
    }


def origin_key(origin: dict | None) -> str | None:
    """The stored merge key, or ``None`` when nothing confirmed an original."""

    if not isinstance(origin, dict):
        return None
    kind = origin.get("kind")
    key = origin.get("key")
    if kind not in ORIGIN_KINDS or not isinstance(key, str):
        return None
    key = key.strip()
    prefix = f"{kind}:"
    if (
        not key
        or len(key) > 2048
        or len(key) <= len(prefix)
        or not key.startswith(prefix)
    ):
        return None
    return key
