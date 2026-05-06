---
name: openclaw-meeting-companion
description: "飞书会议全链路伴侣（自动触发版）：围绕'开会'高频场景，自动完成会前背景知识推送和会后行动项追踪。会前基于日历事件自动检索相关文档/历史任务，生成会前简报并主动推送给参会人；会后自动从会议纪要（支持飞书智能纪要的AI待办）提取行动项，写入飞书多维表格追踪，生成分发稿，并可主动推送到执行人。支持定时扫描、事件监听和队列处理三种主动触发模式，无需用户手动对话即可自动运行。"
version: 1.012
user-invocable: true
disable-model-invocation: false
metadata:
  openclaw:
    requires:
      bins: ["python"]
      env:
        - LARK_CLI_AUTH_PROXY
        - LARK_APP_ID
        - LARK_APP_SECRET
---

# OpenClaw 会议全链路伴侣（自动触发版）

## 能力概述

本 Skill 连接会前准备和会后闭环，把"开会"从信息黑洞变成可追踪、可分发的协作资产。核心设计理念是**主动触发、无需对话**：

- **会前主动**：基于日历事件自动检索飞书知识库，在会议开始前主动推送高密度会前简报
- **会后自动**：自动提取行动项（优先读取飞书AI生成的智能纪要待办），写入多维表格，生成分发稿
- **主动推送**：通过飞书消息将简报和追踪结果自动推送给相关人员
- **定时守护**：`scheduled_runner.py` 作为统一调度器，周期性扫描日历+纪要+队列，自动完成全链路

## 触发条件

当用户表达以下任意意图时，优先调用本 Skill：

- "帮我整理这次会议" / "处理会议纪要"
- "生成会前简报" / "会议前要准备什么"
- "把会议待办写成任务" / "提取行动项"
- "会议提醒" / "推送会议背景"
- 提供了飞书文档链接或妙记链接，并提到"整理""追踪""分发"
- 配置了定时任务后，Skill 可在**无需用户对话**的情况下自动运行

## 前置条件

1. **Python 3.10+** 可用
2. **lark-cli**（推荐）：本地开发/演示时安装并完成飞书用户登录授权
3. **飞书应用凭证**（沙箱/OpenClaw 环境）：配置 `LARK_APP_ID` 和 `LARK_APP_SECRET` 环境变量，启用 HTTP API fallback
4. 当前工作目录为项目根目录（包含 `meeting_companion.py`）

> **双后端说明**：`feishu_client.py` 优先使用 `lark-cli`（本地功能最全）；当 `lark-cli` 不可用时（如 OpenClaw 沙箱），自动降级为直接调用飞书 Open API，仅需应用凭证即可运行。

## 工作流

### 工作流 A：完整链路（会前 + 会后）

当用户提供了会议主题和会议纪要文档时，同时执行会前简报和会后追踪：

```bash
python {baseDir}/meeting_companion.py \
  --topic "<会议主题>" \
  --docx "<飞书文档ID或URL>" \
  --attendees "<参会人1>,<参会人2>" \
  --send-msg
```

参数说明：
- `--topic`：会议主题，用于检索相关文档和生成简报标题
- `--docx`：会议纪要文档的 docx_token 或完整 URL
- `--attendees`（可选）：参会人姓名，逗号分隔，用于飞书消息推送
- `--send-msg`（可选）：是否通过飞书消息推送结果

### 工作流 B：仅会前简报

当用户只有会议主题，还没有会议纪要时：

```bash
python {baseDir}/meeting_companion.py \
  --topic "<会议主题>" \
  --docx "<如已有会前文档>" \
  --pre-only
```

### 工作流 C：仅会后追踪

当用户只想处理已有的会议纪要：

```bash
python {baseDir}/meeting_companion.py \
  --topic "<会议主题>" \
  --docx "<会议纪要文档ID>" \
  --post-only
```

### 工作流 D：定时主动触发（全自动模式）

当需要**无需用户对话**自动完成会前推送和会后追踪时，启动统一调度器：

```bash
# 单次扫描（适合放在 cron / OpenClaw 定时任务中）
python {baseDir}/scheduled_runner.py --once --auto --send-msg

# 持续守护（本地服务器常驻）
python {baseDir}/scheduled_runner.py --loop --interval 300 --auto --send-msg
```

`--auto` 会同时开启以下三项扫描：

1. **会前简报扫描**：读取用户主日历，发现未来 120 分钟内即将开始的会议，自动生成会前简报并推送给参会人
2. **纪要自动发现**：搜索飞书文档中标题含"纪要""Minutes""会议"的文档，自动提取行动项并追踪
3. **队列处理**：读取队列 Base 中的"待处理会议纪要"表，逐条处理后回填状态与结果链接

额外参数：
- `--pre-brief`：仅开启会前简报扫描
- `--scan-minutes`：仅开启纪要自动发现
- `--queue-base-token`：指定队列 Base（如不指定则跳过队列处理）
- `--history-base-token`：指定历史行动项 Base，用于会前简报关联历史任务

### 工作流 E：定时主动触发（仅队列）

当需要周期性自动处理待办会议纪要时，使用定时扫描：

```bash
python {baseDir}/scheduled_runner.py \
  --queue-base-token "<队列BaseToken>" \
  --result-base-token "<结果BaseToken>" \
  --once
```

队列 Base 中需要维护一张"待处理会议纪要"表，字段包括：文档链接、文档ID、会议主题、处理状态等。

## 输出产物

执行完成后会生成以下产物：

1. **会前简报文档**（飞书文档）
   - 包含相关历史资料、当前会议速览、历史行动项
2. **行动项追踪 Base**（飞书多维表格）
   - 任务、负责人、截止时间、状态、来源会议、背景知识
3. **行动项分发稿**（飞书文档）
   - 可一键分发给团队的任务汇总文档
4. **本地报告**（JSON）
   - 便于调试和效果验证

## 智能纪要优先策略

本 Skill 对会议纪要的处理采用三层降级：

1. **P0 - 飞书智能纪要解析**：如果文档是飞书 AI 生成的智能纪要（含 `<checkbox>` 标签），直接读取 AI 已生成的待办，准确率最高
2. **P1 - 规则抽取**：对普通会议纪要，扫描"待办""后续工作计划"等标题块提取任务
3. **P2 - LLM 兜底**（可选）：规则覆盖不足时，可接入豆包/智谱等 LLM API 增强抽取

## 双后端运行说明

| 环境 | 使用方式 | 所需配置 |
|------|---------|---------|
| 本地开发 | `lark-cli` 优先 | 安装 lark-cli 并 `lark-cli login` |
| OpenClaw 沙箱 | HTTP API 自动降级 | `LARK_APP_ID` + `LARK_APP_SECRET` |

`feishu_client.py` 会在初始化时自动探测 `lark-cli` 是否存在：
- 若存在 → 调用 CLI 子进程（支持所有功能，包括 Markdown 导入为精美文档）
- 若不存在 → 读取环境变量，使用 `urllib` 直接调用飞书 Open API（功能等效，文档导入为纯文本块）

## 注意事项

- `--docx` 参数支持传入完整的飞书文档 URL（如 `https://xxx.feishu.cn/docx/ABC123`），会自动提取 docx_token
- 如果输入的是妙记（Minutes）链接，需要先转换为对应的纪要文档 docx_token
- 写入 Base 时需要飞书 `base:app` 相关权限
- 发送飞书消息时需要 `im:message:send` 权限
- 读取日历事件时需要 `calendar:calendar:readonly` 权限
- 自动触发模式建议在服务器或定时任务（cron）中运行 `scheduled_runner.py --once --auto`

## 参考

- [references/workflow.md](references/workflow.md) — 详细工作流与参数说明

