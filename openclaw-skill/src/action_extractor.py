from __future__ import annotations

import re

from src.models import ActionItem, DocumentRecord

TARGET_SECTION_PREFIXES = (
    "待办",
    "后续工作计划",
    "成果沉淀要求",
    "资源领取与配置",
    "模型认领",
    "GitHub 代码仓库",
    "个人阶段成果小结",
    "赛事安排",
    "参赛要求",
)
STOP_SECTION_PREFIXES = ("智能章节", "关键决策", "金句时刻", "相关链接")
INLINE_STOP_PREFIXES = (
    "常见问题解答",
    "抽奖环节",
    "关键决策",
    "其他决策",
    "决策依据",
    "金句时刻",
    "相关链接",
)
TASK_KEYWORDS = (
    "创建",
    "提交",
    "完成",
    "填写",
    "领取",
    "配置",
    "联系",
    "发放",
    "发至",
    "认领",
    "进行项目实践",
    "设置为 public",
    "安排",
    "准备",
    "完善",
    "同步",
    "命名",
    "展现成果",
)
STRONG_TASK_MARKERS = (
    "需提交",
    "需填写",
    "需尽快领取",
    "将文档链接提交到问卷",
    "填写到问卷",
    "完成 GitHub 代码仓库创建",
    "进行项目实践",
    "完成奖品发放",
    "认领一个接入点",
    "按模板创建",
    "提交到问卷",
    "填写到问卷",
    "做准备",
)
TASK_LABEL_KEYWORDS = (
    "提交",
    "处理",
    "代码仓库",
    "成果小结",
    "模型认领",
    "工作计划",
    "文档创建",
    "项目提交",
    "结果处理",
)
OBLIGATION_HINTS = (
    "需",
    "需要",
    "请",
    "尽快",
    "于",
    "前",
    "今天",
    "明天",
    "今日",
    "明日",
    "会后",
    "按模板",
)
NON_TASK_PREFIXES = (
    "关键决策",
    "问题",
    "讨论方案",
    "决策依据",
    "其他决策",
    "本章节",
    "说话人",
    "音频围绕",
    "让多维表格",
    "可编排指",
    "项目完成度越高越好",
    "最终发放的 offer",
    "offer 直发",
    "时区不在国内",
    "以个人为单位填写",
    "会议最后进行了抽奖",
    "平台定位",
    "战略升级",
    "产品能力",
    "赛事目标",
)
NON_TASK_CONTAINS = (
    "介绍赛事",
    "介绍了赛事",
    "重点是",
    "定义",
    "为什么",
    "核心目标",
)
TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}")
DATE_PREFIX_RE = re.compile(r"^(?P<prefix>\d{1,2}\s*月\s*\d{1,2}\s*日(?:前后|前)?)[：:](?P<body>.+)$")
MONTH_DAY_OCCURRENCE_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日")


def extract_action_items(document: DocumentRecord) -> list[ActionItem]:
    lines = [line.strip() for line in document.content.splitlines() if line.strip()]
    candidates: list[ActionItem] = []

    section_buffer: list[str] = []
    in_target_section = False

    for line in lines:
        if _starts_with_any(line, TARGET_SECTION_PREFIXES):
            if section_buffer:
                candidates.extend(_items_from_lines(section_buffer))
                section_buffer = []
            in_target_section = True
            remainder = _strip_prefix(line, TARGET_SECTION_PREFIXES)
            if remainder:
                section_buffer.append(remainder)
            continue

        if in_target_section and _starts_with_any(line, STOP_SECTION_PREFIXES):
            candidates.extend(_items_from_lines(section_buffer))
            section_buffer = []
            in_target_section = False
            continue

        if in_target_section and _starts_with_any(line, INLINE_STOP_PREFIXES):
            candidates.extend(_items_from_lines(section_buffer))
            section_buffer = []
            in_target_section = False
            continue

        if in_target_section:
            section_buffer.append(line)

    if section_buffer:
        candidates.extend(_items_from_lines(section_buffer))

    candidates.extend(_fallback_scan(lines))
    return _dedupe_items(candidates)


def _items_from_lines(lines: list[str]) -> list[ActionItem]:
    items: list[ActionItem] = []
    for line in lines:
        items.extend(_extract_candidates_from_line(line))
    return items


def _fallback_scan(lines: list[str]) -> list[ActionItem]:
    items: list[ActionItem] = []
    for line in lines:
        if not _is_strong_task_line(line):
            continue
        items.extend(_extract_candidates_from_line(line))
    return items


def _extract_candidates_from_line(line: str) -> list[ActionItem]:
    text = _clean_line(line)
    if not text or TIMESTAMP_RE.match(text) or text.startswith("本章节"):
        return []

    match = DATE_PREFIX_RE.match(text)
    if match:
        prefix = match.group("prefix")
        return [
            ActionItem(task=f"{prefix}：{clause}", background=text)
            for clause in _split_task_clauses(match.group("body"))
        ]

    if "：" in text:
        head, body = text.split("：", 1)
        if _looks_task_like(body) or _looks_task_label(head):
            clauses = _split_task_clauses(body)
            if clauses:
                return [ActionItem(task=clause, background=text) for clause in clauses]

    if _looks_task_like(text):
        clauses = _split_task_clauses(text)
        if clauses:
            return [ActionItem(task=clause, background=text) for clause in clauses]

    return []


def _split_task_clauses(text: str) -> list[str]:
    expanded = (
        text.replace("一是", "")
        .replace("二是", "")
        .replace("三是", "")
        .replace("四是", "")
    )
    parts = re.split(r"[；。]", expanded)
    clauses: list[str] = []
    for part in parts:
        candidate = part.strip(" ，,")
        if not candidate:
            continue
        for piece in _split_parallel_candidate(candidate):
            cleaned = piece.strip(" ，,")
            if cleaned and _looks_task_like(cleaned):
                clauses.append(cleaned)
    return clauses


def _split_parallel_candidate(text: str) -> list[str]:
    if "，每个同学" in text:
        left, right = text.split("，每个同学", 1)
        return [left, f"每个同学{right}"]
    if "，每位同学" in text:
        left, right = text.split("，每位同学", 1)
        return [left, f"每位同学{right}"]
    if "，每个组" in text:
        left, right = text.split("，每个组", 1)
        return [left, f"每个组{right}"]
    if "，同学们需" in text:
        left, right = text.split("，同学们需", 1)
        return [left, f"同学们需{right}"]
    if "，进入决赛的同学" in text:
        left, right = text.split("，进入决赛的同学", 1)
        return [left, f"进入决赛的同学{right}"]
    if "，未领取的同学需" in text:
        left, right = text.split("，未领取的同学需", 1)
        return [left, f"未领取的同学需{right}"]
    if "，第五个周期需" in text:
        _, right = text.split("，第五个周期需", 1)
        return [f"第五个周期需{right}"]
    if "，工作人员将" in text:
        left, right = text.split("，工作人员将", 1)
        return [left, f"工作人员将{right}"]
    return [text]


def _looks_task_like(text: str) -> bool:
    if not text:
        return False
    if text.startswith(NON_TASK_PREFIXES):
        return False
    if any(marker in text for marker in NON_TASK_CONTAINS) and not any(
        strong in text for strong in STRONG_TASK_MARKERS
    ):
        return False
    if any(marker in text for marker in STRONG_TASK_MARKERS):
        return True
    if _looks_task_label(text):
        return True
    has_keyword = any(keyword in text for keyword in TASK_KEYWORDS)
    has_obligation = any(hint in text for hint in OBLIGATION_HINTS)
    date_occurrences = len(MONTH_DAY_OCCURRENCE_RE.findall(text))
    if date_occurrences >= 3 and not has_obligation:
        return False
    if has_keyword and has_obligation:
        return True
    if has_keyword and DATE_PREFIX_RE.match(text):
        return True
    if has_keyword and ("问卷" in text or "文档" in text or "代码仓库" in text):
        return True
    return False


def _is_strong_task_line(text: str) -> bool:
    return any(marker in text for marker in STRONG_TASK_MARKERS)


def _looks_task_label(text: str) -> bool:
    return any(label in text for label in TASK_LABEL_KEYWORDS)


def _clean_line(line: str) -> str:
    text = re.sub(r"\s+", " ", line).strip()
    return text.strip("：: ")


def _starts_with_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _strip_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip("：: ")
    return text


def _dedupe_items(items: list[ActionItem]) -> list[ActionItem]:
    seen: set[str] = set()
    result: list[ActionItem] = []
    for item in items:
        key = _canonical_task_key(item.task)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _canonical_task_key(task: str) -> str:
    key = re.sub(r"^\d{1,2}\s*月\s*\d{1,2}\s*日(?:前后|前)?：", "", task).strip()
    if "个人阶段成果小结" in key and "问卷" in key:
        return "个人阶段成果小结提交"
    if "GitHub" in key and "问卷" in key:
        return "github仓库提交"
    if "奖品发放" in key or "抽奖截屏" in key:
        return "抽奖结果处理"
    if "认领一个接入点" in key or "尽快领取" in key:
        return "模型接入点认领"
    if "项目实践" in key:
        return "项目实践"
    if "完整的课题或产品项目" in key:
        return "提交完整项目"
    return key
