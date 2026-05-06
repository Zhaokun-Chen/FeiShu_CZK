#!/usr/bin/env python3
"""
Quick connectivity test for Feishu HTTP API fallback.
Run this after setting LARK_APP_ID and LARK_APP_SECRET.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FS_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FS_APP_SECRET")

    # Import our dual-backend client
    from src.feishu_client import LarkCLIClient, LarkCLIError

    try:
        client = LarkCLIClient()
    except LarkCLIError as exc:
        print(f"[error] Failed to initialize client: {exc}")
        if "not found" in str(exc).lower():
            print("  lark-cli is unavailable and LARK_APP_ID / LARK_APP_SECRET not set.")
            print("  Windows PowerShell: $env:LARK_APP_ID='cli_xxxx'; $env:LARK_APP_SECRET='xxxx'")
        return 1

    if not client.http_mode:
        print("[info] lark-cli is available — running in CLI mode (HTTP fallback not needed locally).")
    else:
        print(f"[info] lark-cli not found — running in HTTP fallback mode.")
        print(f"App ID: {app_id[:8] if app_id else 'N/A'}...")
        if not app_id or not app_secret:
            print("[error] HTTP fallback requires LARK_APP_ID and LARK_APP_SECRET.")
            return 1

    # Test 1: Search documents
    print("\n[Test 1] Search documents (纪要)...")
    try:
        results = client.search_documents("纪要", page_size=3)
        print(f"  OK — found {len(results)} doc(s)")
        for r in results:
            print(f"    - {r.title} ({r.docs_type})")
    except Exception as exc:
        print(f"  FAIL — {exc}")

    # Test 2: Create / find Base
    print("\n[Test 2] Create/find Base...")
    try:
        base_info = client.find_base_by_name("OpenClaw 连接测试") or client.create_base("OpenClaw 连接测试")
        print(f"  OK — Base token: {base_info['app_token'][:8]}...")
    except Exception as exc:
        print(f"  FAIL — {exc}")

    # Test 3: List calendar events (next 1 hour)
    print("\n[Test 3] List calendar events (next 1 hour)...")
    try:
        from datetime import datetime, timedelta, timezone
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        end = now + timedelta(hours=1)
        events = client.list_calendar_events(now.isoformat(), end.isoformat(), page_size=5)
        print(f"  OK — found {len(events)} event(s)")
        for ev in events[:3]:
            summary = ev.get("summary", "N/A") if isinstance(ev, dict) else "N/A"
            print(f"    - {summary}")
    except Exception as exc:
        print(f"  FAIL — {exc}")

    # Test 4: Send a message to yourself (optional)
    print("\n[Test 4] IM message send (skipped by default)")
    print("  To test messaging, run with --send-msg after authorizing im:message:send")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
