from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from agent import run as post_meeting_run
from src.briefing_writer import build_snippet, create_pre_meeting_brief
from src.document_reader import normalize_document_id, read_document
from src.feishu_client import LarkCLIClient, LarkCLIError
from src.models import DocumentInput, DocumentRecord, SearchResult
from src.normalizer import normalize_action_items
from src.smart_minutes_parser import is_smart_minutes, parse_smart_minutes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Meeting Companion Agent: pre-meeting brief + post-meeting action tracking.",
    )
    parser.add_argument("--topic", required=True, help="Meeting topic / title")
    parser.add_argument("--docx", default=None, help="Meeting minutes docx id or URL (for post-meeting)")
    parser.add_argument("--pre-only", action="store_true", help="Only generate pre-meeting brief")
    parser.add_argument("--post-only", action="store_true", help="Only process post-meeting actions")
    parser.add_argument(
        "--attendees",
        default="",
        help="Comma-separated attendee names/emails for Feishu message push",
    )
    parser.add_argument("--send-msg", action="store_true", help="Send results via Feishu message")
    parser.add_argument("--history-base-token", default=None, help="Base token for historical action items")
    parser.add_argument("--history-table-name", default="行动项追踪", help="Historical action table name")
    parser.add_argument("--max-related", type=int, default=5, help="Max related documents for pre-meeting brief")
    parser.add_argument("--base-name", default="OpenClaw 会议行动项验证", help="Result Base name")
    parser.add_argument("--table-name", default="行动项追踪", help="Result Base table name")
    parser.add_argument("--base-token", default=None, help="Reuse an existing Base by token")
    parser.add_argument("--content", default=None, help="Inject document content directly (file path with @ prefix, or raw markdown). Skips API fetch.")
    parser.add_argument("--title", default=None, help="Override document title when using --content injection")
    parser.add_argument("--report-json", default="tmp/companion_report.json", help="Local JSON report path")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.docx and not args.pre_only:
        print("[error] --docx is required unless --pre-only is set", file=sys.stderr)
        return 1

    client = LarkCLIClient()
    report: dict[str, object] = {"topic": args.topic, "phases": {}}

    try:
        # --------------------------------------------------------------
        # Phase 1: Pre-meeting brief
        # --------------------------------------------------------------
        if not args.post_only:
            print("=" * 60)
            print("Phase 1: Generating pre-meeting brief...")
            print("=" * 60)
            brief_url, pre_details = run_pre_meeting(client, args)
            report["phases"]["pre_meeting"] = pre_details
            print(f"\nPre-meeting brief: {brief_url}")

            if args.send_msg and brief_url:
                push_pre_meeting_message(client, args, brief_url)

        # --------------------------------------------------------------
        # Phase 2: Post-meeting action tracking
        # --------------------------------------------------------------
        if not args.pre_only and args.docx:
            print("\n" + "=" * 60)
            print("Phase 2: Processing post-meeting actions...")
            print("=" * 60)
            post_result, post_details = run_post_meeting(client, args)
            report["phases"]["post_meeting"] = post_details
            print(f"\nAction items: {post_result.action_item_count}")
            print(f"Base URL: {post_result.base_url}")
            print(f"Distribution doc: {post_result.distribution_doc_url}")

            if args.send_msg:
                push_post_meeting_message(client, args, post_result)

    except LarkCLIError as exc:
        print(f"[lark-cli error] {exc}", file=sys.stderr)
        return 1
    except (ValueError, NotImplementedError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    # Write local report
    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved: {args.report_json}")
    return 0


# ------------------------------------------------------------------
# Pre-meeting phase
# ------------------------------------------------------------------

def run_pre_meeting(client: LarkCLIClient, args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    source_document = None
    if args.docx:
        try:
            source_document = read_document(client, normalize_document_id(args.docx))
        except (LarkCLIError, ValueError):
            pass

    query = _derive_query(source_document) if source_document else args.topic
    title = source_document.title if source_document else args.topic

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

    details: dict[str, object] = {
        "title": title,
        "query": query,
        "related_docs_count": len(related_docs),
        "history_items_count": len(history_items),
        "brief_url": brief_url,
    }
    return brief_url, details


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

    # Try smart-minutes first, then rule-based
    if is_smart_minutes(source_document):
        items = parse_smart_minutes(source_document)
    else:
        from src.action_extractor import extract_action_items
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
) -> list:
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


# ------------------------------------------------------------------
# Post-meeting phase
# ------------------------------------------------------------------

def run_post_meeting(client: LarkCLIClient, args: argparse.Namespace) -> tuple:
    from src.models import RunResult
    from src.document_reader import resolve_content_path

    content = resolve_content_path(args.content) if args.content else None
    result, normalized_items, owner_packet_urls = post_meeting_run(
        DocumentInput(
            docx_id=args.docx,
            base_name=args.base_name,
            table_name=args.table_name,
            base_token=args.base_token,
            content=content,
            title=args.title,
        )
    )

    details: dict[str, object] = {
        "document_title": result.document_title,
        "action_item_count": result.action_item_count,
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "needs_confirmation_count": result.needs_confirmation_count,
        "base_url": result.base_url,
        "distribution_doc_url": result.distribution_doc_url,
        "owner_packet_count": result.owner_packet_count,
    }
    return result, details


# ------------------------------------------------------------------
# Feishu message push
# ------------------------------------------------------------------

def push_pre_meeting_message(client: LarkCLIClient, args: argparse.Namespace, brief_url: str) -> None:
    if not args.attendees:
        return
    text = (
        f"📢 会议提醒\n"
        f"主题：{args.topic}\n"
        f"会前简报已生成，请提前阅读相关背景资料。\n"
        f"📎 {brief_url}"
    )
    _send_to_attendees(client, args.attendees, text)


def push_post_meeting_message(client: LarkCLIClient, args: argparse.Namespace, result) -> None:
    if not args.attendees:
        return
    text = (
        f"✅ 会议行动项已整理\n"
        f"主题：{args.topic}\n"
        f"行动项数：{result.action_item_count}\n"
        f"待确认：{result.needs_confirmation_count}\n"
        f"📊 追踪表：{result.base_url}\n"
        f"📋 分发稿：{result.distribution_doc_url}"
    )
    _send_to_attendees(client, args.attendees, text)


def _send_to_attendees(client: LarkCLIClient, attendees_str: str, text: str) -> None:
    names = [n.strip() for n in attendees_str.split(",") if n.strip()]
    for name in names:
        try:
            users = client.search_user(name)
            if users:
                client.send_text_message(users[0]["open_id"], text)
                print(f"  Message sent to {users[0]['name']} ({users[0]['open_id']})")
            else:
                # Fallback: treat input as a raw open_id and try sending directly
                client.send_text_message(name, text)
                print(f"  Message sent to {name} (direct open_id)")
        except LarkCLIError as exc:
            print(f"  Failed to send to {name}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
