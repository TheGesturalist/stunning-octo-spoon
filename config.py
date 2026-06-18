"""Credential and path configuration from environment variables.

Environment variables (CLI flags override these; env vars override defaults):
    SPOON_RAINDROP_TOKEN   — Raindrop.io API token
    SPOON_READWISE_TOKEN   — Readwise Reader API token
    SPOON_TUMBLR_API_KEY   — Tumblr API key
    SPOON_TUMBLR_BLOG      — Tumblr blog hostname
    SPOON_DB_PATH          — SQLite database path (default: ./spoon.db)
"""

from __future__ import annotations
import os

DEFAULT_DB_PATH = "./spoon.db"

def db_path() -> str:
    return os.environ.get("SPOON_DB_PATH", DEFAULT_DB_PATH)

def raindrop_token() -> str | None:
    return os.environ.get("SPOON_RAINDROP_TOKEN")

def readwise_token() -> str | None:
    return os.environ.get("SPOON_READWISE_TOKEN")

def tumblr_api_key() -> str | None:
    return os.environ.get("SPOON_TUMBLR_API_KEY")

def tumblr_blog() -> str | None:
    return os.environ.get("SPOON_TUMBLR_BLOG")
