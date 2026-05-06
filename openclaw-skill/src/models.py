from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DocumentInput:
    docx_id: str
    base_name: str
    table_name: str
    base_token: str | None = None
    content: str | None = None
    title: str | None = None


@dataclass(slots=True)
class DocumentRecord:
    document_id: str
    title: str
    url: str
    content: str
    raw_content: str = ""


@dataclass(slots=True)
class ActionItem:
    task: str
    owner: str | None = None
    due_date_text: str | None = None
    due_date_ts: int | None = None
    status: str = "待开始"
    source_meeting: str = ""
    source_document_url: str = ""
    background: str = ""
    needs_confirmation: bool = False
    related_links: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BaseContext:
    app_token: str
    table_id: str
    base_url: str
    base_name: str
    table_name: str


@dataclass(slots=True)
class RunResult:
    document_title: str
    action_item_count: int
    base_url: str
    distribution_doc_url: str
    needs_confirmation_count: int
    created_count: int
    updated_count: int
    owner_packet_count: int


@dataclass(slots=True)
class QueueJob:
    record_id: str
    docx_id: str
    document_url: str
    meeting_topic: str
    status: str
    last_processed_time: str
    result_base_url: str
    result_distribution_url: str
    note: str


@dataclass(slots=True)
class SearchResult:
    token: str
    title: str
    docs_type: str
    url: str


@dataclass(slots=True)
class HistoryActionItem:
    task: str
    owner: str
    due_date: str
    status: str
    source_meeting: str
    background: str
