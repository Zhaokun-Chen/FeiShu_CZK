# 字段结构与模块设计

## 1. 设计目标

本文件定义第一版 Demo 的统一数据结构，确保：

- 文档读取结果有固定格式
- 行动项抽取结果可标准化
- Base 写入字段稳定
- 分发稿生成可以直接复用同一份数据

## 2. 核心数据模型

### DocumentInput

表示一次 Agent 处理任务的输入参数。

字段：

- `docx_id: str`
- `base_name: str`
- `table_name: str`

### DocumentRecord

表示从飞书读取到的一篇会议纪要文档。

字段：

- `document_id: str`
- `title: str`
- `url: str`
- `content: str`

### ActionItem

表示一条结构化行动项。

字段：

- `task: str`
- `owner: str | None`
- `due_date_text: str | None`
- `due_date_ts: int | None`
- `status: str`
- `source_meeting: str`
- `source_document_url: str`
- `background: str`
- `needs_confirmation: bool`

说明：

- `due_date_text` 保存原始日期文本，便于排查解析错误。
- `due_date_ts` 用于写入 Base 日期字段。
- `needs_confirmation` 用于标记负责人或日期缺失的任务。

### BaseContext

表示一次写入 Base 时所需的上下文。

字段：

- `app_token: str`
- `table_id: str`
- `base_url: str`
- `base_name: str`
- `table_name: str`

### RunResult

表示一次 Agent 运行后的最终输出。

字段：

- `document_title: str`
- `action_item_count: int`
- `base_url: str`
- `distribution_doc_url: str`
- `needs_confirmation_count: int`

## 3. Base 字段定义

第一版 Base 表名建议固定为 `行动项追踪`。

字段如下：

- `任务`
  - 类型：多行文本
  - 来源：`ActionItem.task`
- `负责人`
  - 类型：多行文本
  - 来源：`ActionItem.owner`
- `截止时间`
  - 类型：日期时间
  - 来源：`ActionItem.due_date_ts`
- `截止说明`
  - 类型：多行文本
  - 来源：`ActionItem.due_date_text`
- `来源会议`
  - 类型：多行文本
  - 来源：`ActionItem.source_meeting`
- `背景知识`
  - 类型：多行文本
  - 来源：`ActionItem.background`
- `状态`
  - 类型：单选
  - 值域：`待开始`、`进行中`、`已完成`、`需确认`

第一版不加复杂字段，例如优先级、阻塞原因、关联任务、消息卡片 ID。

## 4. 模块职责

### `src/models.py`

职责：

- 定义所有数据结构。
- 作为模块间传递的数据契约。

不负责：

- 调用飞书接口
- 业务规则

### `src/feishu_client.py`

职责：

- 封装所有飞书 MCP / OpenAPI 调用。
- 对外提供清晰的方法接口。

建议方法：

- `get_document_content(document_id)`
- `create_base(name)`
- `create_table(app_token, table_name)`
- `create_record(app_token, table_id, fields)`
- `search_records(app_token, table_id)`
- `import_markdown(file_name, markdown)`

### `src/document_reader.py`

职责：

- 调用 `feishu_client` 读取文档。
- 将返回结果转换为 `DocumentRecord`。

### `src/action_extractor.py`

职责：

- 从 `DocumentRecord.content` 中提取行动项。
- 输出 `list[ActionItem]` 的草稿结构。

第一版实现建议：

- 基于标题块和行文本规则抽取。
- 不把模型调用耦合进主流程。

### `src/normalizer.py`

职责：

- 清洗抽取结果。
- 解析日期。
- 填充默认状态和来源信息。
- 标记 `needs_confirmation`。

### `src/base_writer.py`

职责：

- 准备 Base 和表结构。
- 将 `ActionItem` 映射成 Base 记录字段。
- 批量写入记录。

### `src/distribution_writer.py`

职责：

- 根据行动项列表渲染 Markdown。
- 创建分发稿文档。

### `agent.py`

职责：

- 解析命令行参数。
- 串联所有模块。
- 输出最终运行结果。

## 5. 模块调用顺序

建议调用顺序如下：

1. `agent.py` 解析输入
2. `document_reader.py` 读取文档
3. `action_extractor.py` 提取行动项
4. `normalizer.py` 标准化字段
5. `base_writer.py` 准备 Base 并写入记录
6. `distribution_writer.py` 生成分发稿
7. `agent.py` 输出结果

## 6. 关键设计取舍

### 先规则抽取，再考虑模型增强

原因：

- 第一版目标是稳定跑通闭环，而不是追求最高抽取精度。
- 妙记文档本身经常包含 `待办`、`后续工作计划` 等结构化区块，规则法已足够起步。

### Base 字段保持少而稳

原因：

- 字段越多，抽取和写入失败率越高。
- 先把任务、负责人、截止时间、状态、来源依据跑通，再扩优先级和阻塞原因更合理。

### 分发稿由 Agent 生成，不直接群发

原因：

- 当前群权限受限。
- 先生成文档，便于用户确认和比赛演示。

## 7. 后续代码目录建议

```text
openclaw-meeting-agent/
  agent.py
  docs/
    mvp-scenario.md
    demo-flow.md
    schema-and-modules.md
  src/
    __init__.py
    models.py
    feishu_client.py
    document_reader.py
    action_extractor.py
    normalizer.py
    base_writer.py
    distribution_writer.py
```
