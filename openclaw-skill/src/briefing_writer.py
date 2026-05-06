from __future__ import annotations

import re

from src.feishu_client import LarkCLIClient
from src.models import DocumentRecord, HistoryActionItem, SearchResult


def create_pre_meeting_brief(
    client: LarkCLIClient,
    title: str,
    query: str,
    source_document: DocumentRecord | None,
    related_docs: list[SearchResult],
    snippets: dict[str, str],
    source_highlights: list[str],
    action_preview: list[str],
    history_items: list[HistoryActionItem],
) -> str:
    markdown = render_pre_meeting_brief_markdown(
        title,
        query,
        source_document,
        related_docs,
        snippets,
        source_highlights,
        action_preview,
        history_items,
    )
    payload = client.import_markdown(
        file_name=f"{title}-会前简报.md",
        markdown=markdown,
    )
    return payload["url"]


def render_pre_meeting_brief_markdown(
    title: str,
    query: str,
    source_document: DocumentRecord | None,
    related_docs: list[SearchResult],
    snippets: dict[str, str],
    source_highlights: list[str],
    action_preview: list[str],
    history_items: list[HistoryActionItem],
) -> str:
    lines = [
        f"# {title} 会前背景简报",
        "",
        "## 概览",
        "",
        f"- 检索主题：{query}",
        f"- 相关资料数：{len(related_docs)}",
    ]
    if source_document is not None:
        lines.append(f"- 当前会议文档：[{source_document.title}]({source_document.url})")

    lines.extend(
        [
            "",
            "## 建议会前关注",
            "",
        ]
    )
    focus_points = _build_focus_points(source_document, related_docs, snippets)
    if focus_points:
        for point in focus_points:
            lines.append(f"- {point}")
    else:
        lines.append("- 暂未自动提炼出重点，建议优先阅读下方相关资料。")

    if source_highlights:
        lines.extend(["", "## 当前会议速览", ""])
        for item in source_highlights:
            lines.append(f"- {item}")

    if action_preview:
        lines.extend(["", "## 会后可能延续的行动项", ""])
        for item in action_preview:
            lines.append(f"- {item}")

    if history_items:
        lines.extend(["", "## 历史相关行动项", ""])
        for item in history_items:
            lines.append(
                f"- {item.task} | 负责人：{item.owner or '待确认'} | 截止：{item.due_date or '待确认'} | 状态：{item.status or '待确认'}"
            )
            if item.source_meeting:
                lines.append(f"  来源会议：{item.source_meeting}")

    lines.extend(["", "## 相关资料", ""])
    if related_docs:
        for index, item in enumerate(related_docs, start=1):
            lines.append(f"### {index}. [{item.title}]({item.url})")
            lines.append("")
            lines.append(f"- 类型：`{item.docs_type}`")
            snippet = snippets.get(item.token)
            if snippet:
                lines.append(f"- 摘要：{snippet}")
            lines.append("")
    else:
        lines.append("- 当前未检索到额外相关资料，建议先基于本会议文档完成会前准备。")
        lines.append("")

    if source_document is not None:
        lines.extend(["## 当前会议原始背景", "", _truncate(source_document.content, 800), ""])

    return "\n".join(lines)


def build_snippet(content: str, query: str) -> str:
    text = re.sub(r"\s+", " ", content).strip()
    if not text:
        return ""
    keywords = [part for part in re.split(r"[\s：:，,]+", query) if part]
    for keyword in keywords:
        pos = text.find(keyword)
        if pos >= 0:
            start = max(0, pos - 80)
            end = min(len(text), pos + 180)
            return _truncate(text[start:end], 220)
    return _truncate(text, 220)


def _build_focus_points(
    source_document: DocumentRecord | None,
    related_docs: list[SearchResult],
    snippets: dict[str, str],
) -> list[str]:
    points: list[str] = []
    if source_document is not None:
        points.append("先阅读当前会议文档的摘要与待办区块，确认本次会议的目标与决策范围。")
    if related_docs:
        points.append(f"优先查看最相关的 {min(3, len(related_docs))} 份历史资料，避免会中重复同步背景。")
    for item in related_docs[:3]:
        snippet = snippets.get(item.token)
        if snippet:
            points.append(f"《{item.title}》可能与本次会议直接相关：{snippet}")
    return points[:5]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 3]}..."
