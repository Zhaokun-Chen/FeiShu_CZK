from __future__ import annotations

import html
import re
from pathlib import Path

from src.feishu_client import LarkCLIClient
from src.models import DocumentRecord


def read_document(client: LarkCLIClient, document_id: str) -> DocumentRecord:
    normalized_document_id = normalize_document_id(document_id)
    payload = client.get_document_content(normalized_document_id)
    title = payload.get("title") or document_id
    raw_content = payload.get("content") or ""
    content = _normalize_document_content(raw_content)
    if not content.strip():
        raise ValueError("document content is empty")
    return DocumentRecord(
        document_id=normalized_document_id,
        title=title,
        url=payload.get("url") or f"https://jcneyh7qlo8i.feishu.cn/docx/{normalized_document_id}",
        content=content,
        raw_content=raw_content,
    )


def resolve_content_path(value: str) -> str:
    """If value starts with '@', treat remainder as a file path and read it."""
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.exists():
            raise ValueError(f"content file not found: {path}")
        return path.read_text(encoding="utf-8")
    return value


def build_document_record(document_id: str, content: str, title: str | None = None) -> DocumentRecord:
    """Build a DocumentRecord from injected content (OpenClaw pre-fetch path)."""
    normalized_document_id = normalize_document_id(document_id)
    raw_content = content
    normalized = _normalize_document_content(raw_content)
    if not normalized.strip():
        raise ValueError("document content is empty")
    return DocumentRecord(
        document_id=normalized_document_id,
        title=title or document_id,
        url=f"https://jcneyh7qlo8i.feishu.cn/docx/{normalized_document_id}",
        content=normalized,
        raw_content=raw_content,
    )


def normalize_document_id(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("document id is empty")
    match = re.search(r"/docx/([A-Za-z0-9]+)", text)
    if match:
        return match.group(1)
    return text


def _normalize_document_content(content: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    text = re.sub(r"</(p|h1|h2|h3|li|blockquote|ul|ol|grid|column|checkbox)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
