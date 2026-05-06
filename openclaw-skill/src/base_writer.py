from __future__ import annotations

import time

from src.feishu_client import LarkCLIClient, LarkCLIError
from src.models import ActionItem, BaseContext


def ensure_base_table(client: LarkCLIClient, base_name: str, table_name: str, base_token: str | None = None) -> BaseContext:
    base_payload = _resolve_base(client, base_name, base_token)
    app_token = base_payload["app_token"]
    base_url = base_payload["url"]
    table_payload = client.ensure_table(app_token, table_name)
    return BaseContext(
        app_token=app_token,
        table_id=table_payload["table_id"],
        base_url=base_url,
        base_name=base_name,
        table_name=table_name,
    )


def write_action_items(client: LarkCLIClient, context: BaseContext, items: list[ActionItem]) -> tuple[int, int]:
    created_count = 0
    updated_count = 0
    for item in items:
        if _upsert_record_with_retry(client, context, item):
            updated_count += 1
        else:
            created_count += 1
    return created_count, updated_count


def _resolve_base(client: LarkCLIClient, base_name: str, base_token: str | None) -> dict[str, str]:
    if base_token:
        return client.get_base(base_token)

    existing = client.find_base_by_name(base_name)
    if existing:
        return existing
    return client.create_base(base_name)


def _to_base_fields(item: ActionItem) -> dict[str, object]:
    fields: dict[str, object] = {
        "任务": item.task,
        "负责人": item.owner or "",
        "来源会议": item.source_meeting,
        "背景知识": item.background,
        "相关链接": "\n".join(item.related_links),
        "状态": item.status,
        "截止说明": item.due_date_text or "",
    }
    if item.due_date_ts is not None:
        fields["截止时间"] = item.due_date_ts
    return fields


def _upsert_record_with_retry(client: LarkCLIClient, context: BaseContext, item: ActionItem) -> bool:
    fields = _to_base_fields(item)
    delays = (1.0, 2.0, 4.0)
    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0, *delays), start=1):
        if delay:
            time.sleep(delay)
        try:
            record_id = client.find_record_id_by_task(context.app_token, context.table_id, item.task)
            if record_id:
                client.update_record(context.app_token, context.table_id, record_id, fields)
                return True
            client.create_record(context.app_token, context.table_id, fields)
            return False
        except LarkCLIError as exc:
            last_error = exc
            if "limited" not in str(exc).lower():
                raise
            if attempt == len(delays) + 1:
                raise
    if last_error:
        raise last_error
