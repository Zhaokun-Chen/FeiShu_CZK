from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.models import ActionItem, DocumentRecord

FULL_DATE_RE = re.compile(r"(?P<year>\d{4})[-/年]\s*(?P<month>\d{1,2})[-/月]\s*(?P<day>\d{1,2})")
MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号](?P<suffix>前后|前)?")
DATE_RANGE_RE = re.compile(
    r"(?P<start_month>\d{1,2})\s*月\s*(?P<start_day>\d{1,2})\s*日\s*[-至到]+\s*(?P<end_month>\d{1,2})\s*月\s*(?P<end_day>\d{1,2})\s*日"
)
OWNER_RE = re.compile(r"(?P<owner>[\u4e00-\u9fffA-Za-z0-9 /]+?)负责")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def normalize_action_items(items: list[ActionItem], document: DocumentRecord) -> list[ActionItem]:
    source_meeting = document.title
    meeting_date = _infer_meeting_date(document)
    normalized: list[ActionItem] = []

    for item in items:
        task = _trim_task(item.task)
        owner = (
            item.owner
            or _extract_owner(task)
            or _infer_owner(task)
            or _extract_owner(item.background)
            or _infer_owner(item.background)
        )
        due_date_text, due_date_ts = _extract_due_date(task, meeting_date)
        if due_date_text is None:
            due_date_text, due_date_ts = _extract_due_date(item.background, meeting_date)
        status = _infer_status(task)
        needs_confirmation = owner is None or due_date_text is None
        if needs_confirmation and status == "待开始":
            status = "需确认"

        normalized.append(
            ActionItem(
                task=task,
                owner=owner,
                due_date_text=due_date_text,
                due_date_ts=due_date_ts,
                status=status,
                source_meeting=source_meeting,
                source_document_url=document.url,
                background=item.background or task,
                needs_confirmation=needs_confirmation,
            )
        )
    return normalized


def _trim_task(task: str) -> str:
    return re.sub(r"\s+", " ", task).strip("：: ")


def _extract_owner(text: str) -> str | None:
    match = OWNER_RE.search(text)
    if not match:
        return None
    return match.group("owner").strip(" ，,")


def _infer_owner(text: str) -> str | None:
    owner_rules = (
        ("进入决赛的同学", "进入决赛的参赛同学"),
        ("个人阶段成果小结", "每位参赛同学"),
        ("GitHub 代码仓库", "每个参赛组"),
        ("每个同学", "每位参赛同学"),
        ("每位同学", "每位参赛同学"),
        ("每位参赛同学", "每位参赛同学"),
        ("每个组", "每个参赛组"),
        ("同学们", "参赛小组 / 个人"),
        ("未领取的同学", "未领取模型资源的参赛同学"),
        ("工作人员", "赛事工作人员"),
        ("中奖同学", "赛事工作人员"),
        ("抽奖截屏", "赛事工作人员"),
        ("奖品发放", "赛事工作人员"),
    )
    for keyword, owner in owner_rules:
        if keyword in text:
            return owner
    if "项目实践" in text or "完整的课题或产品项目" in text:
        return "参赛小组 / 个人"
    if "项目介绍" in text or "demo" in text or "录屏演示" in text:
        return "参赛小组 / 个人"
    return None


def _extract_due_date(text: str, meeting_date: datetime | None) -> tuple[str | None, int | None]:
    date_range = DATE_RANGE_RE.search(text)
    if date_range:
        year = meeting_date.year if meeting_date else datetime.now(SHANGHAI).year
        end_dt = datetime(
            year=year,
            month=int(date_range.group("end_month")),
            day=int(date_range.group("end_day")),
            tzinfo=SHANGHAI,
        )
        return date_range.group(0), int(end_dt.timestamp() * 1000)

    explicit = FULL_DATE_RE.search(text)
    if explicit:
        dt = datetime(
            year=int(explicit.group("year")),
            month=int(explicit.group("month")),
            day=int(explicit.group("day")),
            tzinfo=SHANGHAI,
        )
        return explicit.group(0), int(dt.timestamp() * 1000)

    month_day = MONTH_DAY_RE.search(text)
    if month_day:
        year = meeting_date.year if meeting_date else datetime.now(SHANGHAI).year
        dt = datetime(
            year=year,
            month=int(month_day.group("month")),
            day=int(month_day.group("day")),
            tzinfo=SHANGHAI,
        )
        return month_day.group(0), int(dt.timestamp() * 1000)

    if meeting_date is None:
        return None, None

    if "第五个周期" in text:
        year = meeting_date.year
        dt = datetime(year=year, month=5, day=7, tzinfo=SHANGHAI)
        return "5 月 7 日", int(dt.timestamp() * 1000)

    if "抽奖" in text or "奖品发放" in text:
        return "会后", None

    if "今天到明天" in text or "今日或明日" in text or "今天或明天" in text:
        dt = meeting_date + timedelta(days=1)
        return "今天到明天", int(dt.timestamp() * 1000)
    if "今天" in text or "今日" in text:
        return "今天", int(meeting_date.timestamp() * 1000)
    if "明天" in text or "明日" in text:
        dt = meeting_date + timedelta(days=1)
        return "明天", int(dt.timestamp() * 1000)
    if "尽快" in text:
        return "尽快", None
    if "会后" in text:
        return "会后", None

    return None, None


def _infer_status(text: str) -> str:
    if "进行项目实践" in text:
        return "进行中"
    return "待开始"


def _infer_meeting_date(document: DocumentRecord) -> datetime | None:
    match = FULL_DATE_RE.search(document.title) or FULL_DATE_RE.search(document.content)
    if not match:
        return None
    return datetime(
        year=int(match.group("year")),
        month=int(match.group("month")),
        day=int(match.group("day")),
        tzinfo=SHANGHAI,
    )
