"""Connector package for ingesting content from multiple external systems."""

from .base import BaseConnector
from .internet_archive import InternetArchiveConnector
from .local_library import LocalLibraryConnector
from .raindrop_io import RaindropIOConnector
from .reader_io import ReaderIOConnector
from .schema import ENRICHMENT_SQLITE_DDL, NORMALIZED_ITEM_JSON_SCHEMA, NORMALIZED_ITEMS_SQLITE_DDL, NormalizedItem
from .tumblr import TumblrConnector
from .storage import init_sqlite, upsert_item, upsert_item_with_enrichment

__all__ = [
    "BaseConnector",
    "NormalizedItem",
    "NORMALIZED_ITEM_JSON_SCHEMA",
    "NORMALIZED_ITEMS_SQLITE_DDL",
    "ENRICHMENT_SQLITE_DDL",
    "init_sqlite",
    "upsert_item",
    "upsert_item_with_enrichment",
    "LocalLibraryConnector",
    "RaindropIOConnector",
    "ReaderIOConnector",
    "TumblrConnector",
    "InternetArchiveConnector",
]
