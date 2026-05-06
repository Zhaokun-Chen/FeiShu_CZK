from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from dataclasses import asdict

from src.action_extractor import extract_action_items
from src.base_writer import ensure_base_table, write_action_items
from src.distribution_writer import create_distribution_document
from src.document_reader import read_document
from src.feishu_client import LarkCLIClient, LarkCLIError
from src.knowledge_linker import enrich_action_items_with_links
from src.models import ActionItem, DocumentInput, RunResult
from src.normalizer import normalize_action_items
from src.owner_packet_writer import create_owner_packets
from src.smart_minutes_parser import is_smart_minutes, parse_smart_minutes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate action items and a distribution doc from a Feishu meeting note.",
    )
    parser.add_argument("--docx", required=True, help="Feishu docx document id or URL")
    parser.add_argument(
        "--base-name",
        default="OpenClaw 会议行动项验证",
        help="Base app name",
    )
    parser.add_argument(
        "--table-name",
        default="行动项追踪",
        help="Base table name",
    )
    parser.add_argument(
        "--base-token",
        default=None,
        help="Reuse an existing Base by token instead of creating/searching by name",
    )
    parser.add_argument(
        "--content",
        default=None,
        help="Inject document content directly (file path with @ prefix, or raw markdown). Skips API fetch.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Override document title when using --content injection",
    )
    parser.add_argument(
        "--report-json",
        default="tmp/last_run.json",
        help="Write a local JSON report for demo inspection",
    )
    return parser


def run(input_data: DocumentInput) -> tuple[RunResult, list[ActionItem], dict[str, str]]:
    client = LarkCLIClient()

    # OpenClaw pre-fetch path: content injected via --content
    if input_data.content:
        from src.document_reader import build_document_record
        document = build_document_record(
            input_data.docx_id,
            input_data.content,
            title=input_data.title,
        )
        print("[info] Using injected content (OpenClaw pre-fetch path)")
    else:
        document = read_document(client, input_data.docx_id)

    # Tier 1: Feishu AI-generated smart minutes (checkbox todos)
    if is_smart_minutes(document):
        print("[detected] Feishu smart minutes — parsing AI-generated todos")
        normalized_items = parse_smart_minutes(document)
        if not normalized_items:
            print("[fallback] Smart minutes parser returned empty, trying rule-based extraction")
    else:
        normalized_items = []

    # Tier 2: Rule-based extraction fallback
    if not normalized_items:
        extracted_items = extract_action_items(document)
        normalized_items = normalize_action_items(extracted_items, document)

    normalized_items = enrich_action_items_with_links(client, normalized_items, document)
    base_context = ensure_base_table(client, input_data.base_name, input_data.table_name, input_data.base_token)
    created_count, updated_count = write_action_items(client, base_context, normalized_items)
    distribution_doc_url = create_distribution_document(client, document, base_context, normalized_items)
    owner_packet_urls = create_owner_packets(client, document, normalized_items)

    needs_confirmation_count = sum(1 for item in normalized_items if item.needs_confirmation)
    return (
        RunResult(
            document_title=document.title,
            action_item_count=len(normalized_items),
            base_url=base_context.base_url,
            distribution_doc_url=distribution_doc_url,
            needs_confirmation_count=needs_confirmation_count,
            created_count=created_count,
            updated_count=updated_count,
            owner_packet_count=len(owner_packet_urls),
        ),
        normalized_items,
        owner_packet_urls,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    from src.document_reader import resolve_content_path
    content = resolve_content_path(args.content) if args.content else None
    input_data = DocumentInput(
        docx_id=args.docx,
        base_name=args.base_name,
        table_name=args.table_name,
        base_token=args.base_token,
        content=content,
        title=args.title,
    )
    try:
        result, normalized_items, owner_packet_urls = run(input_data)
    except LarkCLIError as exc:
        print(f"[lark-cli error] {exc}", file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print(f"[not implemented] {exc}", file=sys.stderr)
        return 2

    write_report(args.report_json, input_data, result, normalized_items, owner_packet_urls)

    print(f"Document: {result.document_title}")
    print(f"Action items: {result.action_item_count}")
    print(f"Created: {result.created_count}")
    print(f"Updated: {result.updated_count}")
    print(f"Needs confirmation: {result.needs_confirmation_count}")
    print(f"Owner packets: {result.owner_packet_count}")
    print(f"Base URL: {result.base_url}")
    print(f"Distribution doc: {result.distribution_doc_url}")
    print(f"Local report: {args.report_json}")
    return 0


def write_report(
    report_path: str,
    input_data: DocumentInput,
    result: RunResult,
    items: list[ActionItem],
    owner_packet_urls: dict[str, str],
) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "input": asdict(input_data),
        "result": asdict(result),
        "items": [asdict(item) for item in items],
        "owner_packet_urls": owner_packet_urls,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
