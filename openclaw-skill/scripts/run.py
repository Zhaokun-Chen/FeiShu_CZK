#!/usr/bin/env python3
"""
OpenClaw Skill wrapper for meeting_companion.py and scheduled_runner.py.

This script translates OpenClaw invocation parameters into
meeting_companion.py / scheduled_runner.py CLI arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw skill wrapper")
    parser.add_argument("--topic", default="", help="Meeting topic")
    parser.add_argument("--docx", default=None, help="Meeting minutes docx id or URL")
    parser.add_argument("--pre-only", action="store_true", help="Only pre-meeting brief")
    parser.add_argument("--post-only", action="store_true", help="Only post-meeting actions")
    parser.add_argument("--attendees", default="", help="Comma-separated attendee names")
    parser.add_argument("--send-msg", action="store_true", help="Send Feishu messages")
    parser.add_argument("--base-name", default="OpenClaw 会议行动项验证", help="Base name")
    parser.add_argument("--table-name", default="行动项追踪", help="Table name")
    parser.add_argument("--base-token", default=None, help="Existing base token")
    parser.add_argument("--content", default=None, help="Inject document content directly (file path with @ prefix, or raw markdown)")
    parser.add_argument("--title", default=None, help="Override document title when using --content injection")
    # Proactive / scheduler mode
    parser.add_argument("--mode", default="companion", choices=["companion", "scheduler"], help="Run mode")
    parser.add_argument("--auto", action="store_true", help="Enable all proactive triggers")
    parser.add_argument("--pre-brief", action="store_true", help="Scan calendar and push pre-meeting briefs")
    parser.add_argument("--scan-minutes", action="store_true", help="Scan for new meeting minutes")
    parser.add_argument("--queue-base-token", default=None, help="Queue Base token")
    parser.add_argument("--queue-table-name", default="待处理会议纪要", help="Queue table name")
    parser.add_argument("--history-base-token", default=None, help="History base token")
    parser.add_argument("--history-table-name", default="行动项追踪", help="History table name")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve project root (parent of openclaw-skill/)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent

    # If no explicit companion arguments are provided, default to scheduler/auto mode
    # so that OpenClaw scheduled invocation (which passes no args) works out of the box.
    is_scheduler = (
        args.mode == "scheduler"
        or args.auto
        or args.pre_brief
        or args.scan_minutes
        or args.queue_base_token
    )
    if not is_scheduler and not args.topic and not args.docx:
        is_scheduler = True
        args.auto = True
        args.once = True

    if is_scheduler:
        runner_py = project_root / "scheduled_runner.py"
        if not runner_py.exists():
            print(f"[error] scheduled_runner.py not found at {runner_py}", file=sys.stderr)
            return 1
        cmd = [sys.executable, str(runner_py), "--once" if args.once else "--loop"]
        if args.auto:
            cmd.append("--auto")
        if args.pre_brief:
            cmd.append("--pre-brief")
        if args.scan_minutes:
            cmd.append("--scan-minutes")
        if args.queue_base_token:
            cmd.extend(["--queue-base-token", args.queue_base_token])
        if args.queue_table_name:
            cmd.extend(["--queue-table-name", args.queue_table_name])
        if args.base_token:
            cmd.extend(["--result-base-token", args.base_token])
        if args.base_name:
            cmd.extend(["--base-name", args.base_name])
        if args.table_name:
            cmd.extend(["--table-name", args.table_name])
        if args.history_base_token:
            cmd.extend(["--history-base-token", args.history_base_token])
        if args.history_table_name:
            cmd.extend(["--history-table-name", args.history_table_name])
        if args.send_msg:
            cmd.append("--send-msg")
    else:
        companion_py = project_root / "meeting_companion.py"
        if not companion_py.exists():
            print(f"[error] meeting_companion.py not found at {companion_py}", file=sys.stderr)
            return 1
        if not args.topic and not args.docx:
            print("[error] --topic or --docx is required in companion mode", file=sys.stderr)
            return 1
        cmd = [sys.executable, str(companion_py)]
        if args.topic:
            cmd.extend(["--topic", args.topic])
        if args.docx:
            cmd.extend(["--docx", args.docx])
        if args.pre_only:
            cmd.append("--pre-only")
        if args.post_only:
            cmd.append("--post-only")
        if args.attendees:
            cmd.extend(["--attendees", args.attendees])
        if args.send_msg:
            cmd.append("--send-msg")
        if args.base_name:
            cmd.extend(["--base-name", args.base_name])
        if args.table_name:
            cmd.extend(["--table-name", args.table_name])
        if args.base_token:
            cmd.extend(["--base-token", args.base_token])
        if args.content:
            cmd.extend(["--content", args.content])
        if args.title:
            cmd.extend(["--title", args.title])

    # Forward all environment variables (including lark-cli auth state and app credentials)
    env = os.environ.copy()

    result = subprocess.run(cmd, cwd=str(project_root), env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
