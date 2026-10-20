from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from stocks.collection.config import load_sources, select_sources
from stocks.collection.errors import CollectionError
from stocks.collection.service import collect_source
from stocks.db import session_factory
from stocks.settings import settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover configured RSS/Atom items and ingest their article bytes."
    )
    parser.add_argument(
        "--config",
        default="config/sources.yaml",
        help="Path to the source configuration YAML.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source identifier to collect; repeat to collect multiple sources.",
    )
    args = parser.parse_args(argv)

    try:
        configured = load_sources(args.config)
        selected = select_sources(configured, args.source_ids)
    except CollectionError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2

    output: dict[str, object] = {"sources": []}
    failed = False
    try:
        with session_factory()() as session:
            for source in selected:
                try:
                    summary = collect_source(session, settings(), source)
                except CollectionError as exc:
                    session.rollback()
                    failed = True
                    output["sources"].append(
                        {
                            "source_id": source.source_id,
                            "error": exc.to_dict(),
                        }
                    )
                except Exception:
                    session.rollback()
                    failed = True
                    output["sources"].append(
                        {
                            "source_id": source.source_id,
                            "error": {
                                "code": "COLLECTION_FAILED",
                                "message": "Collection failed unexpectedly",
                            },
                        }
                    )
                else:
                    failed = failed or summary.failed > 0
                    output["sources"].append(summary.to_dict())
    except Exception:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Database session could not be initialized",
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
