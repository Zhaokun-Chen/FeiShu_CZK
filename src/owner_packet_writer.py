from __future__ import annotations

import re
from collections import defaultdict

from src.feishu_client import LarkCLIClient
from src.models import ActionItem, DocumentRecord


def create_owner_packets(
    client: LarkCLIClient,
    document: DocumentRecord,
    items: list[ActionItem],
) -> dict[str, str]:
    grouped: dict[str, list[ActionItem]] = defaultdict(list)
    for item in items:
        owner = item.owner or "待确认负责人"
        grouped[owner].append(item)

    result: dict[str, str] = {}
    for owner, owner_items in grouped.items():
        markdown = render_owner_packet_markdown(owner, document, owner_items)
        payload = client.import_markdown(
            file_name=f"{document.title}-{_safe_owner(owner)}-执行清单.md",
            markdown=markdown,
        )
        result[owner] = payload["url"]
    return result


def render_owner_packet_markdown(owner: str, document: DocumentRecord, items: list[ActionItem]) -> str:
    lines = [
        f"# {owner} 执行清单",
        "",
        f"- 来源会议：[{document.title}]({document.url})",
        f"- 待处理任务数：{len(items)}",
        "",
        "## 任务清单",
        "",
    ]
    for item in items:
        lines.append(f"### {item.task}")
        lines.append("")
        lines.append(f"- 状态：{item.status}")
        lines.append(f"- 截止时间：{item.due_date_text or '待确认'}")
        if item.related_links:
            lines.append("- 相关链接：")
            for link in item.related_links:
                lines.append(f"  - {link}")
        lines.append("- 背景说明：")
        lines.append(f"  - {item.background or '待补充'}")
        lines.append("")
    return "\n".join(lines)


def _safe_owner(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", value).strip("-") or "owner"
