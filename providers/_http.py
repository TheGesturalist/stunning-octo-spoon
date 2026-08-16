"""Shared HTTP plumbing for live providers (stdlib only).

Every provider goes through here so that timeouts, the User-Agent, and error
translation are uniform. A real User-Agent is not optional: several of these
hosts (Are.na behind Cloudflare, Wikimedia's API etiquette policy) reject or
throttle the default ``Python-urllib`` identity.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .base import ProviderError

# Wikimedia's API etiquette policy asks for a UA that identifies the tool and
# offers a way to make contact; a vague UA is a documented reason for throttling.
USER_AGENT = os.environ.get("SPOON_USER_AGENT") or (
    "stunning-octo-spoon/1.0 "
    "(https://github.com/TheGesturalist/stunning-octo-spoon; local research indexer)"
)
DEFAULT_TIMEOUT = 12.0

# Retry policy for transient throttling. Kept small: a live search must not
# stall the whole federated fan-out waiting on one rude host.
_RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = 3
MAX_BACKOFF = 8.0


def build_url(base: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return base
    cleaned = {k: v for k, v in params.items() if v is not None}
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urllib.parse.urlencode(cleaned)}"


def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    full = build_url(url, params)
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(full, headers=request_headers)

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                time.sleep(_backoff_seconds(exc, attempt))
                continue
            raise ProviderError(f"HTTP {exc.code} from {_host(full)}") from exc
        except TimeoutError as exc:
            raise ProviderError(
                f"timed out after {timeout:g}s reaching {_host(full)}"
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ProviderError(
                    f"timed out after {timeout:g}s reaching {_host(full)}"
                ) from exc
            raise ProviderError(
                f"network error reaching {_host(full)}: {reason}"
            ) from exc
    raise ProviderError(f"{_host(full)} kept throttling after {MAX_RETRIES} attempts")


def _backoff_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honor Retry-After when the server sends one; exponential otherwise."""

    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass
    return min(2.0**attempt, MAX_BACKOFF)


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    raw = fetch_text(url, params=params, headers=request_headers, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{_host(url)} returned non-JSON ({exc.msg})") from exc


def fetch_xml(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ET.Element:
    raw = fetch_text(url, params=params, headers=headers, timeout=timeout)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderError(f"{_host(url)} returned unparseable XML ({exc})") from exc


def strip_html(value: str | None) -> str:
    """Crude tag stripper — enough to turn API-supplied HTML into index text."""

    if not value:
        return ""
    import html
    import re

    text = re.sub(r"<script.*?</script>", " ", value, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc or url
    except ValueError:
        return url
