"""Configured source discovery, acquisition, and repository ingestion."""

from stocks.collection.config import SourceConfig, load_sources
from stocks.collection.errors import CollectionError
from stocks.collection.http import FetchedResponse, SafeFetcher
from stocks.collection.service import CollectionSummary, collect_source

__all__ = [
    "CollectionError",
    "CollectionSummary",
    "FetchedResponse",
    "SafeFetcher",
    "SourceConfig",
    "collect_source",
    "load_sources",
]
