from __future__ import annotations

from src.feishu_client import LarkCLIClient
from src.models import ActionItem, BaseContext, DocumentRecord


def create_distribution_document(
    client: LarkCLIClient,
    document: DocumentRecord,
    context: BaseContext,
    items: list[ActionItem],
) -> str:
    markdown = render_distribution_markdown(document, context, items)
    payload = client.import_markdown(
        file_name=f"{document.title}-行动项分发稿.md",
        markdown=markdown,
    )
    return payload["url"]


def render_distribution_markdown(
    document: DocumentRecord,
    context: BaseContext,
    items: list[ActionItem],
) -> str:
    pending_items = [item for item in items if item.needs_confirmation]
    lines = [
        f"# {document.title} 行动项分发稿",
        "",
        "## 概览",
        "",
        f"- 来源文档：[{document.title}]({document.url})",
        f"- 行动项追踪 Base：[{context.base_name}]({context.base_url})",
        f"- 行动项总数：{len(items)}",
        f"- 待确认项：{len(pending_items)}",
        "",
        "## 行动项汇总",
        "",
        "| 任务 | 负责人 | 截止时间 | 状态 |",
        "|---|---|---|---|",
    ]
    for item in items:
        due_date = item.due_date_text or "待确认"
        owner = item.owner or "待确认"
        lines.append(f"| {item.task} | {owner} | {due_date} | {item.status} |")

    if pending_items:
        lines.extend(["", "## 待确认项", ""])
        for item in pending_items:
            lines.append(f"- {item.task} | 负责人：{item.owner or '待确认'} | 截止时间：{item.due_date_text or '待确认'}")

    lines.extend(["", "## 任务背景", ""])
    for item in items:
        lines.append(f"### {item.task}")
        lines.append("")
        if item.related_links:
            lines.append("相关链接：")
            lines.append("")
            for link in item.related_links:
                lines.append(f"- {link}")
            lines.append("")
        lines.append(item.background or "待补充")
        lines.append("")

    return "\n".join(lines)
