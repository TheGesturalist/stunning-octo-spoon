# Connectors

This package provides source-specific ingest adapters that normalize content into a shared schema.

## Base interface

Each connector implements:

- `fetch_items(cursor=None, limit=100)`
- `fetch_fulltext(item)`
- `normalize_item(item)`
- `sync_cursor(last_item)`

## Shared normalized schema

The ranking/query layer should read/write only the normalized schema in `schema.py`:

- `NormalizedItem` dataclass for in-memory transport
- `NORMALIZED_ITEM_JSON_SCHEMA` for JSON validation
- `NORMALIZED_ITEMS_SQLITE_DDL` for durable storage

## Included connectors

- `LocalLibraryConnector`: local filesystem + sidecar full-text indexes
- `RaindropIOConnector`: bookmarks + highlights
- `ReaderIOConnector`: saved reading list + highlights
- `TumblrConnector`: post metadata + captions/tags
- `InternetArchiveConnector`: metadata + text where available
- `AcademicPrivateConnector`: institution-accessed databases with per-provider abstract/full-text rights policies

## SQLite helpers

`storage.py` includes:

- `init_sqlite(db_path)`
- `upsert_item(db_path, normalized_item)`
