from __future__ import annotations

import re

from src.feishu_client import LarkCLIClient, LarkCLIError
from src.models import ActionItem, DocumentRecord


def enrich_action_items_with_links(
    client: LarkCLIClient,
    items: list[ActionItem],
    document: DocumentRecord,
    max_links_per_item: int = 3,
) -> list[ActionItem]:
    enriched: list[ActionItem] = []
    for item in items:
        links: list[str] = []
        if document.url:
            links.append(document.url)
        for query in _build_queries(item, document):
            try:
                results = client.search_documents(query, page_size=5)
            except LarkCLIError:
                continue
            for result in results:
                if result.url == document.url:
                    continue
                links.append(result.url)
                if len(_dedupe(links)) >= max_links_per_item:
                    break
            if len(_dedupe(links)) >= max_links_per_item:
                break
        item.related_links = _dedupe(links)[:max_links_per_item]
        enriched.append(item)
    return enriched


def _build_queries(item: ActionItem, document: DocumentRecord) -> list[str]:
    base_queries = [
        item.task,
        _truncate_query(item.task),
        document.title,
        f"{document.title} {item.task}",
    ]
    queries: list[str] = []
    seen: set[str] = set()
    for query in base_queries:
        cleaned = re.sub(r"\s+", " ", query).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        queries.append(cleaned)
    return queries


def _truncate_query(task: str, limit: int = 24) -> str:
    text = re.sub(r"[：:|]+", " ", task)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
