from __future__ import annotations

import argparse
import re

from src.action_extractor import extract_action_items
from src.briefing_writer import build_snippet, create_pre_meeting_brief
from src.document_reader import normalize_document_id, read_document
from src.feishu_client import LarkCLIClient, LarkCLIError
from src.models import DocumentRecord, HistoryActionItem, SearchResult
from src.normalizer import normalize_action_items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a pre-meeting background brief from related Feishu documents.",
    )
    parser.add_argument("--docx", default=None, help="Meeting docx id or URL")
    parser.add_argument("--query", default=None, help="Search query for related knowledge")
    parser.add_argument("--max-related", type=int, default=5, help="Max related documents to include")
    parser.add_argument("--history-base-token", default=None, help="Base token containing historical action items")
    parser.add_argument("--history-table-name", default="行动项追踪", help="Historical action table name")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.docx and not args.query:
        parser.error("one of --docx or --query is required")

    client = LarkCLIClient()

    try:
        source_document = read_document(client, normalize_document_id(args.docx)) if args.docx else None
        query = args.query or _derive_query(source_document)
        title = source_document.title if source_document is not None else query
        related_docs = _find_related_docs(client, query, source_document, args.max_related)
        snippets = _build_snippets(client, related_docs, query)
        source_highlights = _build_source_highlights(source_document)
        action_preview = _build_action_preview(source_document)
        history_items = _find_history_items(client, args.history_base_token, args.history_table_name, query)
        brief_url = create_pre_meeting_brief(
            client,
            title,
            query,
            source_document,
            related_docs,
            snippets,
            source_highlights,
            action_preview,
            history_items,
        )
    except LarkCLIError as exc:
        print(f"[lark-cli error] {exc}")
        return 1
    except ValueError as exc:
        print(f"[input error] {exc}")
        return 2

    print(f"Title: {title}")
    print(f"Query: {query}")
    print(f"Related docs: {len(related_docs)}")
    print(f"Brief URL: {brief_url}")
    return 0


def _derive_query(source_document: DocumentRecord | None) -> str:
    if source_document is None:
        raise ValueError("query is required when docx is not provided")
    title = source_document.title
    title = re.sub(r"智能纪要[:：]?", "", title).strip()
    title = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "", title).strip()
    return title or source_document.title


def _find_related_docs(
    client: LarkCLIClient,
    query: str,
    source_document: DocumentRecord | None,
    max_related: int,
) -> list[SearchResult]:
    results = client.search_documents(query, page_size=max(10, max_related + 3))
    filtered: list[SearchResult] = []
    source_token = source_document.document_id if source_document is not None else None
    for item in results:
        if source_token and item.token == source_token:
            continue
        filtered.append(item)
        if len(filtered) >= max_related:
            break
    return filtered


def _build_snippets(client: LarkCLIClient, related_docs: list[SearchResult], query: str) -> dict[str, str]:
    snippets: dict[str, str] = {}
    for item in related_docs:
        if item.docs_type != "docx":
            continue
        try:
            document = read_document(client, item.token)
        except (LarkCLIError, ValueError):
            continue
        snippets[item.token] = build_snippet(document.content, query)
    return snippets


def _build_source_highlights(source_document: DocumentRecord | None) -> list[str]:
    if source_document is None:
        return []
    content = source_document.content
    highlights: list[str] = []
    for marker in ("总结", "赛事安排", "资源支持", "参赛要求", "后续工作计划", "待办"):
        snippet = _section_snippet(content, marker)
        if snippet:
            highlights.append(f"{marker}：{snippet}")
    return highlights[:5]


def _build_action_preview(source_document: DocumentRecord | None) -> list[str]:
    if source_document is None:
        return []
    items = normalize_action_items(extract_action_items(source_document), source_document)
    preview: list[str] = []
    for item in items[:5]:
        owner = item.owner or "待确认"
        due = item.due_date_text or "待确认"
        preview.append(f"{item.task} | 负责人：{owner} | 截止：{due}")
    return preview


def _find_history_items(
    client: LarkCLIClient,
    history_base_token: str | None,
    history_table_name: str,
    query: str,
) -> list[HistoryActionItem]:
    if not history_base_token:
        return []
    table = client.ensure_table(history_base_token, history_table_name)
    return client.search_action_history(history_base_token, table["table_id"], query, limit=5)


def _section_snippet(content: str, marker: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.startswith(marker):
            parts: list[str] = []
            for candidate in lines[index + 1 : index + 4]:
                if len(candidate) <= 30 and not any(ch in candidate for ch in "：:。"):
                    break
                parts.append(candidate)
            text = " ".join(parts).strip()
            if text:
                return text[:180] + ("..." if len(text) > 180 else "")
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
