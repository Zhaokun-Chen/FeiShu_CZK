from __future__ import annotations

import html
import re

from src.models import ActionItem, DocumentRecord


def is_smart_minutes(document: DocumentRecord) -> bool:
    """Detect whether a document is Feishu AI-generated smart minutes."""
    content = document.raw_content or document.content
    markers = (
        '<h1>待办</h1>',
        '<h1>智能章节</h1>',
        '<h1>关键决策</h1>',
        '<h1>总结</h1>',
        '<checkbox',
        '智能纪要由 AI 生成',
    )
    return sum(1 for m in markers if m in content) >= 2


def parse_smart_minutes(document: DocumentRecord) -> list[ActionItem]:
    """Extract structured action items from Feishu smart-minutes HTML.

    Falls back to empty list if no checkboxes are found, letting the caller
    decide whether to try rule-based extraction next.
    """
    content = document.raw_content or document.content
    items: list[ActionItem] = []

    # 1. Parse AI-generated todos (<checkbox> tags)
    checkbox_items = _parse_checkboxes(content)
    for text, done in checkbox_items:
        owner = _infer_owner_from_text(text)
        due_text, due_ts = _infer_due_date_from_text(text)
        status = "已完成" if done else "待开始"
        items.append(
            ActionItem(
                task=text,
                owner=owner,
                due_date_text=due_text,
                due_date_ts=due_ts,
                status=status,
                source_meeting=document.title,
                source_document_url=document.url,
                background=text,
                needs_confirmation=owner is None or due_text is None,
            )
        )

    # 2. Parse "后续工作计划" section if it exists and we got few items
    if len(items) < 3:
        plan_items = _parse_follow_up_plans(content)
        for text in plan_items:
            owner = _infer_owner_from_text(text)
            due_text, due_ts = _infer_due_date_from_text(text)
            items.append(
                ActionItem(
                    task=text,
                    owner=owner,
                    due_date_text=due_text,
                    due_date_ts=due_ts,
                    status="待开始",
                    source_meeting=document.title,
                    source_document_url=document.url,
                    background=text,
                    needs_confirmation=owner is None or due_text is None,
                )
            )

    return _dedupe_action_items(items)


def extract_smart_sections(document: DocumentRecord) -> dict[str, list[str]]:
    """Extract smart sections for pre-meeting brief enrichment."""
    content = document.raw_content or document.content
    return {
        "todos": [text for text, _ in _parse_checkboxes(content)],
        "chapters": _parse_chapters(content),
        "decisions": _parse_decisions(content),
        "follow_up": _parse_follow_up_plans(content),
    }


def _parse_checkboxes(content: str) -> list[tuple[str, bool]]:
    """Extract <checkbox done="true/false">text</checkbox> items."""
    pattern = re.compile(r'<checkbox\s+done="(true|false)"\s*>(.*?)</checkbox>', re.IGNORECASE | re.DOTALL)
    results: list[tuple[str, bool]] = []
    for match in pattern.finditer(content):
        done = match.group(1).lower() == "true"
        text = _clean_html_content(match.group(2))
        if text and len(text) > 3:
            results.append((text, done))
    return results


def _parse_chapters(content: str) -> list[str]:
    """Extract chapter titles from <h1>智能章节</h1> section."""
    section = _extract_section(content, "智能章节", ["关键决策", "金句时刻", "相关链接", "待办", "总结"])
    if not section:
        return []
    titles: list[str] = []
    for match in re.finditer(r'<a\s+[^>]*>\d{2}:\d{2}</a>\s*<b>\s*(.*?)\s*</b>', section):
        title = _clean_html_content(match.group(1))
        if title:
            titles.append(title)
    return titles


def _parse_decisions(content: str) -> list[str]:
    """Extract key decisions from <h1>关键决策</h1> section."""
    section = _extract_section(content, "关键决策", ["其他决策", "金句时刻", "相关链接", "待办", "总结", "智能章节"])
    if not section:
        return []
    decisions: list[str] = []
    for item in re.findall(r'<li>\s*<b>(.*?)</b>\s*[:：]\s*(.*?)</li>', section, re.DOTALL):
        label = _clean_html_content(item[0])
        body = _clean_html_content(item[1])
        if label and body:
            decisions.append(f"{label}：{body}")
    return decisions


def _parse_follow_up_plans(content: str) -> list[str]:
    """Extract action items from <h1>后续工作计划</h1> section."""
    section = _extract_section(content, "后续工作计划", ["待办", "关键决策", "智能章节", "金句时刻", "相关链接", "常见问题解答"])
    if not section:
        return []
    items: list[str] = []
    for match in re.finditer(r'<li>\s*(?:<b>)?\s*(.*?)\s*(?:</b>)?\s*[:：]\s*(.*?)</li>', section, re.DOTALL):
        label = _clean_html_content(match.group(1))
        body = _clean_html_content(match.group(2))
        if body:
            text = f"{label}：{body}" if label else body
            if len(text) > 5:
                items.append(text)
    return items


def _extract_section(content: str, heading: str, stop_headings: list[str]) -> str | None:
    """Extract HTML between <h1>heading</h1> and the next <h1>stop</h1> or end."""
    start_pattern = re.compile(rf'<h1>\s*{re.escape(heading)}\s*</h1>', re.IGNORECASE)
    start_match = start_pattern.search(content)
    if not start_match:
        return None
    start_pos = start_match.end()
    stop_positions: list[int] = []
    for stop in stop_headings:
        stop_match = re.search(rf'<h1>\s*{re.escape(stop)}\s*</h1>', content[start_pos:], re.IGNORECASE)
        if stop_match:
            stop_positions.append(start_pos + stop_match.start())
    end_pos = min(stop_positions) if stop_positions else len(content)
    return content[start_pos:end_pos]


def _clean_html_content(raw: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r'<[^>]+>', '', raw)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' ：:')


def _infer_owner_from_text(text: str) -> str | None:
    """Lightweight owner inference from checkbox text."""
    rules = (
        ("每个同学", "每位参赛同学"),
        ("每位同学", "每位参赛同学"),
        ("同学们", "参赛小组 / 个人"),
        ("每个组", "每个参赛组"),
        ("工作人员", "赛事工作人员"),
        ("联系人", None),
        ("负责人", None),
    )
    for keyword, owner in rules:
        if keyword in text:
            return owner
    match = re.search(r'由\s*([一-鿿A-Za-z0-9 /]+?)\s*(?:负责|处理|联系)', text)
    if match:
        return match.group(1).strip(' ，,')
    return None


def _infer_due_date_from_text(text: str) -> tuple[str | None, int | None]:
    """Lightweight due-date extraction from checkbox text."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    SHANGHAI = ZoneInfo("Asia/Shanghai")

    month_day = re.search(r'(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号]', text)
    if month_day:
        year = datetime.now(SHANGHAI).year
        dt = datetime(year=year, month=int(month_day.group("month")), day=int(month_day.group("day")), tzinfo=SHANGHAI)
        return month_day.group(0), int(dt.timestamp() * 1000)

    if "今天" in text or "今日" in text:
        dt = datetime.now(SHANGHAI)
        return "今天", int(dt.timestamp() * 1000)
    if "明天" in text or "明日" in text:
        from datetime import timedelta
        dt = datetime.now(SHANGHAI) + timedelta(days=1)
        return "明天", int(dt.timestamp() * 1000)
    if "尽快" in text:
        return "尽快", None
    if "会后" in text:
        return "会后", None

    return None, None


def _dedupe_action_items(items: list[ActionItem]) -> list[ActionItem]:
    seen: set[str] = set()
    result: list[ActionItem] = []
    for item in items:
        key = re.sub(r'\s+', ' ', item.task).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result
