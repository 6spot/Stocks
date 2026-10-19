from stocks.rss import parse_feed

def test_rss_can_be_parsed_without_any_model_calls():
    raw = b'''<?xml version="1.0"?>
    <rss version="2.0"><channel><title>sample</title>
    <item><guid>abc</guid><title>Example</title>
    <description>Disclosure</description></item>
    </channel></rss>'''
    items = parse_feed(raw)
    assert len(items) == 1
    assert items[0]["title"] == "Example"
    assert len(items[0]["dedupe_key"]) == 64

def test_atom_published_and_updated_times_are_distinct():
    raw = b'''<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Test feed</title>
      <id>https://example.com/feed</id>
      <updated>2026-10-19T18:30:00Z</updated>
      <entry>
        <id>https://example.com/story</id><title>Story</title>
        <published>2026-10-17T10:00:00Z</published>
        <updated>2026-10-18T12:00:00Z</updated>
        <summary>First version</summary>
      </entry>
    </feed>'''
    item = parse_feed(raw)[0]
    assert item["published_at"].isoformat() == "2026-10-17T10:00:00+00:00"
    assert item["source_updated_at"].isoformat() == "2026-10-18T12:00:00+00:00"


def test_same_item_changed_content_creates_new_snapshot_identity():
    def make_feed(summary: str, updated: str) -> bytes:
        return f"""<rss version="2.0"><channel><title>Test</title>
            <item><guid>same-story</guid><title>Example</title>
            <description>{summary}</description>
            <pubDate>Sat, 17 Oct 2026 10:00:00 GMT</pubDate>
            <updated>{updated}</updated></item>
            </channel></rss>""".encode()

    first = parse_feed(make_feed("first", "2026-10-18T12:00:00Z"))[0]
    changed = parse_feed(make_feed("changed", "2026-10-18T12:00:00Z"))[0]
    assert first["dedupe_key"] != changed["dedupe_key"]
    assert first["content_hash"] != changed["content_hash"]
