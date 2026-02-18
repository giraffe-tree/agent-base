# Cursor Checkpoint 说明与 `state.vscdb` 对照

## Cursor 官方说明（原文）

> Checkpoint 是 Agent 针对你的代码库所做更改的自动快照，让你在需要时可以撤销修改。你可以在之前的请求中通过 Restore Checkpoint 按钮恢复，或在鼠标悬停到某条消息上时点击 + 按钮来恢复。

## 这段说明意味着什么

- Checkpoint 面向的是“代码库改动”这一类可回滚状态。
- Checkpoint 和会话消息绑定，恢复入口位于消息交互区域（`Restore Checkpoint`、消息悬停 `+`）。
- 用户视角是“按会话/消息回到某个改动前状态”，不是手动管理底层快照文件。

## 与 `state.vscdb` 分析结果的对照

基于前面的样本分析（`workspaceStorage/*/state.vscdb`），可观察到以下事实：

- `state.vscdb` 表结构非常轻量，仅有 `ItemTable` 与 `cursorDiskKV` 两张 KV 表。
- 在样本中，`ItemTable` 主要是工作台状态、会话索引、历史记录，以及部分用户输入/系统生成文本：
  - `composer.composerData`
  - `aiService.prompts`
  - `aiService.generations`
  - `workbench.*`
  - `memento/*`
- 对 `checkpoint` 关键词做 key/value 扫描时，命中主要落在文本上下文（prompt/history）而非明确的结构化快照 key。

## 结论：产品能力与本地存储的关系

- Cursor 的 Checkpoint 能力在产品上是明确存在的（可恢复改动）。
- 但在 `state.vscdb` 这一单一文件里，更像是“会话与状态索引层”，不是完整快照实体本身。
- 因此更合理的理解是：
  - `state.vscdb` 记录了与恢复操作有关的上下文、会话关系或索引线索；
  - 实际用于回滚的完整快照数据，可能位于其他本地存储位置或以其他机制管理。

## 为什么会出现这种设计

- KV 状态库适合快速保存 UI 状态和会话元数据。
- 快照/回滚数据体量更大，通常会与 UI 状态解耦，避免 `state.vscdb` 过度膨胀。
- 从工程实现上，将“索引与展示状态”与“实际快照内容”分层，是常见架构选择。

## 排查建议（用于继续定位快照实体）

- 在同一个 `workspaceStorage/<id>/` 目录内，结合时间戳对 sqlite/json/log 文件做创建前后 diff。
- 重点观察触发 Checkpoint（发起有文件改动的 Agent 请求）前后，哪些文件增长或新增。
- 将 `composerId`、消息时间戳与本地文件修改时间进行关联，建立“消息 -> 快照文件”的映射线索。

