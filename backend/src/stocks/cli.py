import argparse
from stocks.db import session_factory
from stocks.rss import configured_sources, ingest_rss
from stocks.settings import settings

def main() -> None:
    parser = argparse.ArgumentParser(description="Stocks collection")
    parser.add_argument("command", choices=["collect"])
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source", help="Configured source ID")
    selection.add_argument("--all", action="store_true", help="All enabled sources")
    args = parser.parse_args()
    config = settings()
    sources = [
        item for item in configured_sources(config.sources_file)
        if item.enabled and (args.all or item.id == args.source)
    ]
    if not sources:
        raise SystemExit("No matching enabled source")
    failed = False
    for source in sources:
        try:
            with session_factory()() as session:
                count = ingest_rss(source, config, session)
            print(f"{source.id}: {count} new documents")
        except Exception as exc:
            print(f"{source.id}: FAILED: {type(exc).__name__}: {exc}")
            failed = True
    if failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
