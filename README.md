# OpenClaw 会议全链路伴侣

> 飞书 AI 校园挑战赛 · OpenClaw 赛道 · 方向 B：会议与项目的全链路伴侣

围绕"开会"这一企业最高频协作场景，构建**会前-会后-跟进**完整闭环的 OpenClaw Skill。无需人工对话，自动完成会前背景推送和会后行动项追踪。

---

## 功能特性

### 会前：自动简报推送
- **定时扫描日历**：每小时自动读取主日历，发现未来 2 小时内即将开始的会议
- **智能检索背景**：自动搜索相关历史文档、未完成的 action items
- **生成会前简报**：自动创建飞书文档，包含会议概览、背景资料、历史待办联动
- **主动消息推送**：通过飞书 IM 将简报卡片推送给参会人

### 会后：行动项追踪与分发
- **双 Tier 解析策略**：
  - **P0 - 智能纪要优先**：直接解析飞书 AI 生成的 `<checkbox>` 标签，零幻觉提取待办
  - **P1 - 规则兜底**：无 checkbox 时，基于规则+NLP 提取正文中的行动项
- **结构化写入 Base**：自动创建/复用多维表格，补齐负责人、截止时间、状态
- **分发稿自动生成**：生成可直接转发到群聊的飞书文档
- **去重与更新**：重复运行时自动匹配更新，避免重复插入

---

## 技术架构

### Triple-backend 鉴权
```
user_token (OpenClaw JWT)
    ↓ 优先
lark-cli (本地开发，浏览器授权)
    ↓ fallback
tenant_token (app_id + app_secret，HTTP API)
```

`feishu_client.py` 统一封装三种鉴权模式，自动探测、自动 fallback，确保本地与线上环境零配置切换。

### 核心模块

| 模块 | 文件 | 职责 |
|---|---|---|
| 统一调度 | `scheduled_runner.py` | 定时轮询，串联会前简报 + 纪要扫描 + 队列处理 |
| 日历扫描 | `src/calendar_watcher.py` | 扫描未来 120 分钟内即将开始的会议 |
| 会前简报 | `src/briefing_writer.py` | 基于相关文档和历史任务生成会前简报 |
| 智能纪要解析 | `src/smart_minutes_parser.py` | 解析飞书 AI 智能纪要 `<checkbox>` 标签 |
| 行动项提取 | `src/action_extractor.py` | 基于规则和 NLP 抽取 action items |
| 标准化 | `src/normalizer.py` | 补齐负责人、截止时间、状态 |
| Base 写入 | `src/base_writer.py` | 创建/复用 Base 表格，upsert 记录 |
| 分发稿生成 | `src/distribution_writer.py` | 生成可直接转发的飞书文档 |
| 消息推送 | `src/feishu_client.py` | 封装 IM 消息发送，自动处理 union_id → open_id |
| OpenClaw 入口 | `openclaw-skill/scripts/run.py` | Skill 包装器，无参自动 fallback 到 scheduler 模式 |

---

## 安装与部署

### 前置条件
- Python 3.10+
- 飞书应用凭证（`LARK_APP_ID` + `LARK_APP_SECRET`）
- OpenClaw 环境（推荐）或本地 lark-cli

### 本地开发

```bash
# 1. 克隆仓库
git clone https://github.com/Zhaokun-Chen/FeiShu_CZK.git
cd FeiShu_CZK

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量（本地测试用）
export LARK_APP_ID="your_app_id"
export LARK_APP_SECRET="your_app_secret"

# 4. 测试连接
python test_connection.py
```

### OpenClaw 部署

1. 将 `openclaw-meeting-companion-1.012.zip` 上传至 OpenClaw Skill 管理后台
2. 配置环境变量：`LARK_APP_ID`、`LARK_APP_SECRET`
3. 创建定时任务（建议 Cron：`0 * * * *`，每小时执行一次）
4. 触发词：Agent 识别"会议提醒"、"生成会前简报"、"处理会议纪要"等意图时自动调用

---

## 使用方式

### 工作流 A：定时自动触发（全自动模式）

OpenClaw 定时任务自动运行，无需用户对话：

```bash
python scheduled_runner.py --once --auto --send-msg
```

- 自动扫描日历 → 生成简报 → 推送消息
- 自动扫描会议纪要 → 提取行动项 → 写入 Base

### 工作流 B：手动触发会后追踪

用户向 Agent 发送指令：

```
处理会议纪要：https://xxx.feishu.cn/docx/ABC123
```

或命令行：

```bash
python meeting_companion.py \
  --topic "项目周会" \
  --docx "ABC123" \
  --send-msg
```

### 工作流 C：仅会前简报

```bash
python meeting_companion.py \
  --topic "评审会" \
  --pre-only
```

---

## 项目结构

```
.
├── agent.py                      # 会后处理主入口
├── meeting_companion.py          # 会前+会后统一调度（手动模式）
├── scheduled_runner.py           # 定时主动触发（全自动模式）
├── pre_meeting_brief.py        # 独立会前简报脚本
├── src/
│   ├── feishu_client.py          # 飞书 API 客户端（Triple-backend）
│   ├── calendar_watcher.py       # 日历扫描与会前简报推送
│   ├── briefing_writer.py        # 会前简报文档生成
│   ├── smart_minutes_parser.py   # 智能纪要 checkbox 解析
│   ├── action_extractor.py       # 规则提取行动项
│   ├── normalizer.py             # 信息补齐与标准化
│   ├── base_writer.py            # Base 表格读写
│   ├── distribution_writer.py    # 分发稿生成
│   ├── minutes_watcher.py        # 纪要文档扫描
│   ├── document_reader.py        # 文档读取与清洗
│   ├── knowledge_linker.py       # 相关文档关联
│   ├── owner_packet_writer.py    # 负责人执行清单
│   └── models.py                 # 数据模型
├── openclaw-skill/
│   ├── scripts/run.py            # OpenClaw Skill 入口
│   ├── SKILL.md                  # Skill 定义与触发条件
│   └── references/               # 参考文档
├── docs/                         # 项目设计文档
├── requirements.txt
└── README.md
```

---

## 效果验证

| 指标 | 人工方式 | Agent 方式 | 提升 |
|---|---|---|---|
| 单篇纪要 action items 整理 | ~15-20 分钟 | ~1-2 分钟 | **~90%** |
| 会前背景资料准备 | ~10-15 分钟 | ~1 分钟 | **~90%** |
| 会后分发稿准备 | ~10 分钟 | ~30 秒 | **~95%** |

- **智能纪要（checkbox）解析准确率**：~100%
- **规则提取准确率**：~80%（受自然语言模糊性影响，需人工复核）

---

## 版本记录

| 版本 | 日期 | 关键更新 |
|---|---|---|
| 1.012 | 2026-05-06 | 消息发送者身份修复（bot token）、union_id fallback、调试日志 |
| 1.009 | 2026-05-06 | 恢复全量扫描、OpenClaw 无参 fallback |
| 1.007 | 2026-05-06 | lark-cli 发消息 flag 修复 |
| 1.001 | 2026-05-06 | 日历扫描修复、ISO 时间解析、窗口扩至 120 分钟 |
| 1.0.0 | 2026-04-28 | 初始版本 |

---

## 作者

**陈兆坤**
- 飞书 AI 校园挑战赛 · OpenClaw 赛道 · 方向 B
- 项目仓库：[https://github.com/Zhaokun-Chen/FeiShu_CZK](https://github.com/Zhaokun-Chen/FeiShu_CZK)

---

## 许可

MIT License
