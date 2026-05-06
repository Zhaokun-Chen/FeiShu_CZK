from __future__ import annotations

import json
from pathlib import Path

from src.feishu_client import LarkCLIClient
from src.models import SearchResult


_PROCESSED_LOG = Path("tmp/processed_docs.json")


def load_processed_doc_ids() -> set[str]:
    """Load the set of already-processed document ids."""
    if not _PROCESSED_LOG.exists():
        return set()
    try:
        data = json.loads(_PROCESSED_LOG.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(data)
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def save_processed_doc_ids(doc_ids: set[str]) -> None:
    """Persist the processed-document id set."""
    _PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    _PROCESSED_LOG.write_text(json.dumps(sorted(doc_ids), ensure_ascii=False), encoding="utf-8")


def scan_recent_meeting_docs(
    client: LarkCLIClient,
    keywords: tuple[str, ...] = ("纪要", "Minutes", "会议"),
    limit_per_keyword: int = 5,
) -> list[SearchResult]:
    """Search for docs that look like meeting minutes and return unprocessed ones."""
    processed = load_processed_doc_ids()
    found: dict[str, SearchResult] = {}
    for keyword in keywords:
        try:
            results = client.search_meeting_minutes(query=keyword, page_size=limit_per_keyword)
        except Exception:
            continue
        for r in results:
            if r.token not in processed and r.token not in found:
                found[r.token] = r
    return list(found.values())


def mark_doc_processed(doc_id: str) -> None:
    processed = load_processed_doc_ids()
    processed.add(doc_id)
    save_processed_doc_ids(processed)
