"""Small HTTP utility to avoid hard dependency on a specific client."""

from __future__ import annotations

from typing import Any
import json
import urllib.request


def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted caller controls URL)
        return json.loads(resp.read().decode("utf-8"))


def get_text(url: str, headers: dict[str, str] | None = None) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted caller controls URL)
        return resp.read().decode("utf-8", errors="ignore")
