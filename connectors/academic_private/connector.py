"""Institution-accessed academic database connector with rights enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..base import BaseConnector
from ..schema import NormalizedItem


@dataclass(frozen=True)
class ProviderAccessPolicy:
    """Per-provider rights policy for stored fields."""

    provider: str
    allow_abstract: bool
    allow_fulltext: bool


class AcademicPrivateConnector(BaseConnector):
    """Normalize records from institution-only academic providers.

    Storage policy:
    - citation metadata is always stored
    - abstract is stored only when provider policy allows it
    - full text is stored only when provider policy allows it and the record is explicitly allowed
    """

    name = "academic_private"

    def __init__(self, provider_policies: Mapping[str, ProviderAccessPolicy]) -> None:
        self.provider_policies = dict(provider_policies)

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "AcademicPrivateConnector expects provider-specific clients to supply source records."
        )

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        provider = str(item.get("provider", "")).strip().lower()
        policy = self.provider_policies.get(provider)
        if not policy or not policy.allow_fulltext:
            return None
        if not bool(item.get("fulltext_explicitly_allowed", False)):
            return None
        value = item.get("fulltext")
        return value if isinstance(value, str) else None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        provider = str(item.get("provider", "")).strip().lower()
        policy = self.provider_policies.get(provider)
        if not policy:
            raise KeyError(f"Missing provider policy for '{provider}'.")

        citation = _citation_metadata(item)
        abstract = item.get("abstract") if policy.allow_abstract else None
        fulltext = self.fetch_fulltext(item)

        rights = {
            "provider": provider,
            "access": "institutional",
            "allow_abstract": policy.allow_abstract,
            "allow_fulltext": policy.allow_fulltext,
            "fulltext_explicitly_allowed": bool(item.get("fulltext_explicitly_allowed", False)),
            "can_export": bool(item.get("can_export", False)),
            "export_policy": item.get("export_policy") or "citation_only",
        }

        return NormalizedItem(
            connector=self.name,
            source_id=str(item.get("id")),
            source_url=item.get("url"),
            title=item.get("title"),
            author=item.get("author"),
            summary=abstract if isinstance(abstract, str) else None,
            fulltext=fulltext,
            content_type="academic_document",
            created_at=item.get("published_at"),
            updated_at=item.get("updated_at"),
            tags=[tag for tag in item.get("subjects", []) if isinstance(tag, str)],
            metadata={
                "provider": provider,
                "citation": citation,
                "journal": item.get("journal"),
                "doi": item.get("doi"),
            },
            rights=rights,
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        return str(last_item.get("updated_at") or last_item.get("id"))


def _citation_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    authors = item.get("authors")
    normalized_authors: Iterable[str] = authors if isinstance(authors, list) else []
    return {
        "title": item.get("title"),
        "authors": [a for a in normalized_authors if isinstance(a, str)],
        "journal": item.get("journal"),
        "year": item.get("year"),
        "doi": item.get("doi"),
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "pages": item.get("pages"),
    }
