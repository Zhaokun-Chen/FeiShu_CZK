from __future__ import annotations

import argparse
import time

from agent import run
from src.calendar_watcher import push_pre_brief_for_event, scan_upcoming_events
from src.document_reader import normalize_document_id
from src.feishu_client import LarkCLIClient, LarkCLIError
from src.minutes_watcher import mark_doc_processed, scan_recent_meeting_docs
from src.models import DocumentInput, QueueJob


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan a Feishu queue table and process pending meeting documents.",
    )
    parser.add_argument("--queue-base-token", default=None, help="Base token containing the queue table")
    parser.add_argument("--queue-table-name", default="待处理会议纪要", help="Queue table name")
    parser.add_argument("--result-base-token", default=None, help="Optional target Base token for action items")
    parser.add_argument("--base-name", default="OpenClaw 会议行动项验证", help="Result Base name")
    parser.add_argument("--table-name", default="行动项追踪", help="Result Base table name")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval in seconds")
    parser.add_argument("--auto", action="store_true", help="Enable all proactive triggers (pre-brief + minutes scan + queue)")
    parser.add_argument("--pre-brief", action="store_true", help="Scan calendar and push pre-meeting briefs")
    parser.add_argument("--scan-minutes", action="store_true", help="Scan for new meeting minutes and auto-process")
    parser.add_argument("--history-base-token", default=None, help="Base token for historical action items")
    parser.add_argument("--history-table-name", default="行动项追踪", help="Historical action table name")
    parser.add_argument("--send-msg", action="store_true", help="Send Feishu messages for proactive triggers")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.once and not args.loop:
        parser.error("one of --once or --loop is required")

    if args.auto:
        args.pre_brief = True
        args.scan_minutes = True

    client = LarkCLIClient()

    if args.queue_base_token:
        queue_table = client.ensure_queue_table(args.queue_base_token, args.queue_table_name)
        queue_table_id = queue_table["table_id"]
    else:
        queue_table_id = ""

    if args.once:
        run_cycle(client, args.queue_base_token, queue_table_id, args)
        return 0

    while True:
        run_cycle(client, args.queue_base_token, queue_table_id, args)
        time.sleep(args.interval)


def run_cycle(
    client: LarkCLIClient,
    queue_base_token: str | None,
    queue_table_id: str,
    args: argparse.Namespace,
) -> None:
    # ------------------------------------------------------------------
    # 1. Proactive: pre-meeting briefs
    # ------------------------------------------------------------------
    if args.pre_brief:
        try:
            events = scan_upcoming_events(client, window_minutes=30)
            if events:
                print(f"[calendar] Found {len(events)} upcoming event(s)")
            for event in events:
                try:
                    brief_url = push_pre_brief_for_event(
                        client,
                        event,
                        max_related=5,
                        history_base_token=args.history_base_token,
                        history_table_name=args.history_table_name,
                        send_msg=args.send_msg,
                    )
                    print(f"[pre-brief] {event.summary} -> {brief_url}")
                except Exception as exc:
                    print(f"[pre-brief error] {event.summary}: {exc}")
        except Exception as exc:
            print(f"[calendar scan error] {exc}")

    # ------------------------------------------------------------------
    # 2. Proactive: scan recent meeting minutes and auto-process
    # ------------------------------------------------------------------
    if args.scan_minutes:
        try:
            docs = scan_recent_meeting_docs(client)
            if docs:
                print(f"[minutes] Found {len(docs)} new meeting doc(s)")
            for doc in docs:
                try:
                    print(f"[minutes] Auto-processing {doc.title} ({doc.token})")
                    result, _, _ = run(
                        DocumentInput(
                            docx_id=doc.token,
                            base_name=args.base_name,
                            table_name=args.table_name,
                            base_token=args.result_base_token,
                        )
                    )
                    mark_doc_processed(doc.token)
                    print(
                        f"[minutes] Done {doc.title}: "
                        f"items={result.action_item_count} created={result.created_count}"
                    )
                except Exception as exc:
                    print(f"[minutes error] {doc.title}: {exc}")
        except Exception as exc:
            print(f"[minutes scan error] {exc}")

    # ------------------------------------------------------------------
    # 3. Queue-based post-meeting processing
    # ------------------------------------------------------------------
    if queue_base_token and queue_table_id:
        try:
            jobs = client.list_pending_queue_jobs(queue_base_token, queue_table_id)
            if not jobs:
                print("[queue] No pending queue jobs.")
                return
            print(f"[queue] Found {len(jobs)} pending queue job(s).")
            for job in jobs:
                process_job(client, queue_base_token, queue_table_id, job, args)
        except Exception as exc:
            print(f"[queue scan error] {exc}")


def process_job(
    client: LarkCLIClient,
    queue_base_token: str,
    queue_table_id: str,
    job: QueueJob,
    args: argparse.Namespace,
) -> None:
    doc_ref = job.docx_id or job.document_url
    if not doc_ref:
        client.mark_queue_job_failed(queue_base_token, queue_table_id, job.record_id, "missing 文档ID / 文档链接")
        print(f"Queue job {job.record_id}: missing document reference.")
        return

    try:
        client.mark_queue_job_processing(queue_base_token, queue_table_id, job.record_id)
        result, _, _ = run(
            DocumentInput(
                docx_id=normalize_document_id(doc_ref),
                base_name=args.base_name,
                table_name=args.table_name,
                base_token=args.result_base_token,
            )
        )
        client.mark_queue_job_done(
            queue_base_token,
            queue_table_id,
            job.record_id,
            result.base_url,
            result.distribution_doc_url,
        )
        print(
            f"Queue job {job.record_id}: done. "
            f"items={result.action_item_count} created={result.created_count} updated={result.updated_count}"
        )
    except (LarkCLIError, ValueError, NotImplementedError) as exc:
        client.mark_queue_job_failed(queue_base_token, queue_table_id, job.record_id, str(exc))
        print(f"Queue job {job.record_id}: failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
