# 代码审查报告 v1.001

## 审查时间
2026-05-06

## 一、发现的问题与修复

### 1. 日历事件扫描失败（根本原因）
**问题**：`scheduled_runner.py --pre-brief` 运行后没有任何输出，proactive 链路无法触发。

**根因分析**：
- **Bug A**：`feishu_client.py` 的 `list_calendar_events` 在 lark-cli 模式下要求 `item.get("event_id")` 必须存在才保留日程，但 lark-cli `calendar +agenda` 返回的日程对象**没有 `event_id` 字段**，导致所有日程被过滤为空列表。
- **Bug B**：`calendar_watcher.py` 的 `scan_upcoming_events` 使用 `datetime.fromisoformat()` 解析 `start_time` 字符串（如 `2026-05-06T15:00:00+08:00`）。Python 3.10 对该格式的支持不稳定，解析失败后 `start_ts` 为 0，事件被 `if not start_ts: continue` 过滤。
- **Bug C**：`calendar_watcher.py` 的 `scan_upcoming_events` 只兼容 HTTP API 返回的 dict 格式 `start_time: {timestamp: "..."}`，不兼容 lark-cli 返回的字符串格式 `start_time: "2026-05-06T15:00:00+08:00"`。

**修复**：
- `feishu_client.py:1563`：过滤条件从 `item.get("event_id")` 放宽为 `item.get("event_id") or item.get("summary")`
- `calendar_watcher.py:12-33`：新增 `_parse_iso_ts()` 辅助函数，使用 `strptime` 兼容解析带时区偏移的 ISO-8601 字符串（支持 `+08:00` 和 `+0800`）
- `calendar_watcher.py:68-82`：`scan_upcoming_events` 中同时处理 dict 和 str 两种 `start_time` 格式

### 2. OpenClaw 入口路径错误
**问题**：`openclaw-skill/scripts/run.py` 的 `project_root` 指向 `openclaw-skill/` 目录本身，而非项目根目录。如果 OpenClaw 通过 `run.py` 调用，实际运行的是 `openclaw-skill/` 下的**过时代码副本**，而非根目录下已修复的最新代码。

**修复**：
- `openclaw-skill/scripts/run.py:50`：`project_root = script_dir.parent` 改为 `project_root = script_dir.parent.parent`
- 打包脚本排除 `openclaw-skill/` 下所有冗余旧代码（`agent.py`, `scheduled_runner.py`, `meeting_companion.py`, `src/` 等），只保留 `scripts/run.py`、`SKILL.md` 和 `references/`

### 3. 扫描窗口过窄 + 无提示
**问题**：默认扫描窗口为 30 分钟，且扫不到事件时完全静默，用户无法区分是"没扫到"还是"出错了"。

**修复**：
- `scheduled_runner.py:74`：`window_minutes=30` 改为 `120`
- `calendar_watcher.py:46`：默认参数同步改为 `120`
- `scheduled_runner.py:77`：增加 `else: print("[calendar] No upcoming events found")`

### 4. 其他小问题
| 文件 | 问题 | 修复 |
|---|---|---|
| `calendar_watcher.py` | `push_pre_brief_for_event` 存在 4 个未使用的导入 | 删除冗余导入 |
| `calendar_watcher.py` | lark-cli 不返回 attendees，消息推送静默跳过 | 增加空 attendees 提示日志 |
| `feishu_client.py` | `import_markdown` lark-cli 路径使用 `str(Path(...) / ...)`，Windows 下产生反斜杠 | 改为 `f"tmp/{temp_path.name}"` |
| `smart_minutes_parser.py` | 使用 `__import__('datetime').timedelta(days=1)` 丑陋写法 | 改为正常 `from datetime import timedelta` |
| `SKILL.md` | 版本号 1.0.0，Python 要求 3.12+，扫描窗口描述 30 分钟 | 更新为 1.001 / 3.10+ / 120 分钟 |

---

## 二、已确认正常的模块

以下模块经审查，逻辑正确，无需修改：

- `agent.py` — 会后处理主入口，双 Tier 策略（智能纪要 + 规则 fallback）正常
- `src/action_extractor.py` — 规则提取逻辑完整
- `src/normalizer.py` — 负责人/截止时间/状态补齐逻辑正常
- `src/smart_minutes_parser.py` — checkbox 和后续工作计划解析正常
- `src/base_writer.py` — Base upsert 和重试逻辑正常
- `src/distribution_writer.py` — 分发稿 markdown 渲染正常
- `src/briefing_writer.py` — 会前简报 markdown 渲染正常
- `src/knowledge_linker.py` — 相关文档搜索和链接关联正常
- `src/owner_packet_writer.py` — 负责人执行清单生成正常
- `src/minutes_watcher.py` — 纪要文档扫描和去重正常
- `src/document_reader.py` — 文档读取和 HTML 清洗正常
- `src/models.py` — 数据模型定义完整
- `meeting_companion.py` — 会前+会后统一调度正常
- `test_connection.py` — 连接测试脚本正常
- `pre_meeting_brief.py` — 独立会前简报脚本正常
- `validate_openclaw_path.py` — OpenClaw 注入验证正常

---

## 三、打包信息

- **文件名**：`openclaw-meeting-companion-1.001.zip`
- **大小**：72,942 bytes（排除冗余旧代码后体积下降 42%）
- **文件数**：35 个
- **包含内容**：根目录全部最新代码 + `openclaw-skill/scripts/run.py` + `SKILL.md`
- **排除内容**：`openclaw-skill/` 下所有过时副本代码

---

## 四、测试建议

上传 1.001 后，按以下顺序验证：

### 步骤 1：Proactive 会前简报
```bash
python scheduled_runner.py --once --pre-brief --send-msg --history-base-token R5pMbnIKlar98msAtAUccvkTnAd
```

预期输出：
```
[calendar] Found 1 upcoming event(s)
[pre-brief] 飞书AI校园挑战赛内容分享 -> https://...
```

如果输出 `[calendar] No upcoming events found`，请确认会议开始时间在未来 120 分钟内。

### 步骤 2：会后追踪
```bash
python meeting_companion.py --topic "测试会议" --docx FIA5d3Vc3oI9QyxicMBcqBjlnAf --post-only --send-msg --attendees "ou_194f50ca30ac033a8d8d0864f7b3a8d1"
```

### 步骤 3：完整链路
```bash
python meeting_companion.py --topic "测试会议" --docx FIA5d3Vc3oI9QyxicMBcqBjlnAf --send-msg --attendees "ou_194f50ca30ac033a8d8d0864f7b3a8d1"
```
