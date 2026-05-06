from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.feishu_client import LarkCLIClient, LarkCLIError
from src.briefing_writer import create_pre_meeting_brief


def _parse_iso_ts(val: str) -> int:
    """Parse ISO-8601 datetime string to Unix timestamp (seconds).

    Works around Python <3.11 where fromisoformat does not support +HH:MM.
    """
    if not val:
        return 0
    # Normalize timezone offset: +08:00 -> +0800
    val = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", val)
    try:
        dt = datetime.strptime(val, "%Y-%m-%dT%H:%M:%S%z")
        return int(dt.timestamp())
    except ValueError:
        pass
    try:
        dt = datetime.strptime(val, "%Y-%m-%dT%H:%M:%S")
        tz = timezone(timedelta(hours=8))
        dt = dt.replace(tzinfo=tz)
        return int(dt.timestamp())
    except ValueError:
        pass
    return 0


@dataclass(slots=True)
class CalendarEvent:
    event_id: str
    summary: str
    description: str
    start_ts: int
    end_ts: int
    attendees: list[dict[str, str]]


def scan_upcoming_events(client: LarkCLIClient, window_minutes: int = 120) -> list[CalendarEvent]:
    """Scan primary calendar for events starting within the next window_minutes."""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    end = now + timedelta(minutes=window_minutes)

    start_iso = now.isoformat()
    end_iso = end.isoformat()

    raw_items = client.list_calendar_events(start_iso, end_iso, page_size=50)
    events: list[CalendarEvent] = []
    now_ts = time.time()
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        # lark-cli returns ISO strings; HTTP API returns dicts with timestamp
        start_val = item.get("start_time")
        end_val = item.get("end_time")

        start_ts = 0
        end_ts = 0
        if isinstance(start_val, dict):
            # HTTP API format: {timestamp: "...", timezone: "..."}
            ts = start_val.get("timestamp")
            if ts is not None:
                try:
                    start_ts = int(ts)
                except (TypeError, ValueError):
                    pass
            else:
                # lark-cli --format json format: {datetime: "...", timezone: "..."}
                dt_str = start_val.get("datetime")
                if isinstance(dt_str, str):
                    start_ts = _parse_iso_ts(dt_str)
        elif isinstance(start_val, str):
            start_ts = _parse_iso_ts(start_val)

        if isinstance(end_val, dict):
            ts = end_val.get("timestamp")
            if ts is not None:
                try:
                    end_ts = int(ts)
                except (TypeError, ValueError):
                    pass
            else:
                dt_str = end_val.get("datetime")
                if isinstance(dt_str, str):
                    end_ts = _parse_iso_ts(dt_str)
        elif isinstance(end_val, str):
            end_ts = _parse_iso_ts(end_val)

        if not start_ts:
            continue
        # Only events that haven't started yet or just started (< 5 min ago)
        if start_ts < now_ts - 300:
            continue
        attendees: list[dict[str, str]] = []
        for att in (item.get("attendees") or []):
            if isinstance(att, dict):
                attendees.append(
                    {
                        "type": str(att.get("type", "")),
                        "user_id": str(att.get("user_id", "")),
                        "name": str(att.get("name", "")),
                    }
                )
        events.append(
            CalendarEvent(
                event_id=str(item.get("event_id", "")),
                summary=str(item.get("summary", "")),
                description=str(item.get("description", "")),
                start_ts=start_ts,
                end_ts=end_ts,
                attendees=attendees,
            )
        )
    return events


def push_pre_brief_for_event(
    client: LarkCLIClient,
    event: CalendarEvent,
    max_related: int = 5,
    history_base_token: str | None = None,
    history_table_name: str = "行动项追踪",
    send_msg: bool = False,
) -> str:
    """Generate and optionally push a pre-meeting brief for a calendar event."""
    from src.briefing_writer import build_snippet
    from src.document_reader import read_document
    from src.models import SearchResult

    topic = event.summary
    query = topic

    # Search related docs
    related_docs = client.search_documents(query, page_size=max(10, max_related + 3))
    filtered: list[SearchResult] = []
    for item in related_docs:
        filtered.append(item)
        if len(filtered) >= max_related:
            break

    snippets: dict[str, str] = {}
    for item in filtered:
        if item.docs_type != "docx":
            continue
        try:
            document = read_document(client, item.token)
        except (LarkCLIError, ValueError):
            continue
        snippets[item.token] = build_snippet(document.content, query)

    # Source highlights / action preview are empty because we only have calendar event
    source_highlights: list[str] = []
    action_preview: list[str] = []
    history_items: list = []

    if history_base_token:
        try:
            table = client.ensure_table(history_base_token, history_table_name)
            history_items = client.search_action_history(
                history_base_token, table["table_id"], query, limit=5
            )
        except LarkCLIError:
            pass

    brief_url = create_pre_meeting_brief(
        client,
        title=topic,
        query=query,
        source_document=None,
        related_docs=filtered,
        snippets=snippets,
        source_highlights=source_highlights,
        action_preview=action_preview,
        history_items=history_items,
    )

    if send_msg and brief_url:
        _send_brief_to_attendees(client, event, brief_url)

    return brief_url


def _send_brief_to_attendees(client: LarkCLIClient, event: CalendarEvent, brief_url: str) -> None:
    text = (
        f"📢 会议即将开始\n"
        f"主题：{event.summary}\n"
        f"开始时间：{_ts_to_str(event.start_ts)}\n"
        f"会前简报已生成，请提前阅读相关背景资料。\n"
        f"📎 {brief_url}"
    )
    if not event.attendees:
        print(f"[pre-brief] No attendees found for '{event.summary}', skipping message push.")
        return
    for att in event.attendees:
        uid = att.get("user_id")
        if not uid:
            continue
        try:
            # Calendar attendee user_id may be union_id (ou_*) or open_id (oc_*).
            # Pass explicit receive_type so the client doesn't guess wrong.
            explicit_type = "union_id" if uid.startswith("ou_") else "open_id"
            client.send_text_message(uid, text, receive_type=explicit_type)
        except LarkCLIError:
            pass


def _ts_to_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
