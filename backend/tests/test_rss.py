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
