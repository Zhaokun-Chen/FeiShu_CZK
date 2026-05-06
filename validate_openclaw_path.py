#!/usr/bin/env python3
"""
Validate the OpenClaw content-injection path locally.
Steps:
  1. Fetch document content via lark-cli (simulating OpenClaw's feishu_fetch_doc)
  2. Save to file
  3. Run agent.py with --content @filepath
  4. Compare action item count with normal path
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DOCX_ID = "FIA5d3Vc3oI9QyxicMBcqBjlnAf"
CONTENT_FILE = Path("tmp/injected_content.md")


def fetch_and_save() -> None:
    print("[step 1] Fetching document content via lark-cli...")
    from src.feishu_client import LarkCLIClient
    client = LarkCLIClient()
    payload = client.get_document_content(DOCX_ID)
    content = payload.get("content", "")
    title = payload.get("title", DOCX_ID)
    if not content.strip():
        print("[error] Empty content")
        raise SystemExit(1)
    CONTENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTENT_FILE.write_text(content, encoding="utf-8")
    print(f"[step 1] Saved {len(content)} chars to {CONTENT_FILE}")
    return title


def run_injected(title: str) -> int:
    print("\n[step 2] Running with --content injection...")
    cmd = [
        sys.executable,
        "agent.py",
        "--docx", DOCX_ID,
        "--base-name", "OpenClaw 注入验证",
        "--content", f"@{CONTENT_FILE}",
        "--title", title,
        "--report-json", "tmp/validation_injected.json",
    ]
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    title = fetch_and_save()
    return run_injected(title)


if __name__ == "__main__":
    raise SystemExit(main())
