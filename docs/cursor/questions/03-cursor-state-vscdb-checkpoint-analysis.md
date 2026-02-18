# Cursor `state.vscdb` checkpoint 信息分析

本文档基于以下数据库样本：

- `/Users/giraffetree/Library/Application Support/Cursor/User/workspaceStorage/8c4450604fe9ec9e6a3fae07ff8cf523/state.vscdb`

## 目标

- 用脚本系统化分析 `state.vscdb` 内容结构。
- 识别与 checkpoint 相关的可观测信息。
- 给出后续定位“完整 checkpoint 快照”的排查方向。

## 分析脚本

脚本路径：

- `docs/cursor/questions/analyze_state_vscdb.py`

主要能力：

- 读取 `ItemTable` / `cursorDiskKV`，输出表统计。
- 统计 key 前缀分布与最大 value 条目。
- 对 key 与 JSON 叶子节点做关键词扫描（默认含 `checkpoint`）。
- 解析 `composer.composerData`，提取 composer 会话索引元数据。
- 解析 `workbench.panel.composerChatViewPane.*`，建立 pane -> composer 映射。
- 可选输出 Markdown / JSON 报告。

## 使用方式

在仓库根目录执行：

```bash
python3 docs/cursor/questions/analyze_state_vscdb.py \
  --db "/Users/giraffetree/Library/Application Support/Cursor/User/workspaceStorage/8c4450604fe9ec9e6a3fae07ff8cf523/state.vscdb" \
  --output-md "docs/cursor/questions/state-vscdb-checkpoint-analysis.md"
```

如需 JSON：

```bash
python3 docs/cursor/questions/analyze_state_vscdb.py \
  --db "/path/to/state.vscdb" \
  --output-json "docs/cursor/questions/state-vscdb-checkpoint-analysis.json"
```

## 本次样本的核心结论

### 1) 数据结构概览

- 表结构非常轻量，仅有：
  - `ItemTable`（有数据）
  - `cursorDiskKV`（本样本为空）
- `ItemTable` 记录数约 128，绝大多数是工作台 UI 状态、视图状态、历史与会话索引信息。

### 2) checkpoint 直接证据

- **未发现 key 名直接包含 `checkpoint` 的结构化记录**。
- 包含 `checkpoint` 的命中主要出现在：
  - `aiService.generations` / `aiService.prompts`（用户历史提示词文本）
  - `history.entries`（历史打开文件路径等）
- 这说明在该样本中，`state.vscdb` 更像“会话与界面索引库”，而不是“checkpoint 内容快照库”。

### 3) 与会话关联的关键字段

- `composer.composerData`：
  - 包含 `allComposers`、`selectedComposerIds`、`lastFocusedComposerIds` 等。
  - 可用于恢复“有哪些会话、哪个最近活跃、模式是什么（agent/plan/chat）”。
- `workbench.panel.composerChatViewPane.<paneId>`：
  - value 内可解析出 `workbench.panel.aichat.view.<composerId>`。
  - 可建立 pane 与 composer 的映射关系，帮助理解 UI 当前绑定的会话。

## 对 checkpoint 存储位置的判断

基于本次样本，合理判断是：

- `state.vscdb` 保存的是 **checkpoint 的索引关联与上下文线索**（间接信息）；
- 但 **完整 checkpoint 快照内容**（例如可精确回滚的状态块）很可能不在该文件内，或者以其他本地存储形式存在。

## 建议的下一步排查

- 对同目录下其他 Cursor 存储文件做横向比对（尤其是 sqlite/json/日志类）。
- 在“创建 checkpoint 前后”做文件快照 diff，比较新增/变化 key。
- 用脚本扩展关键词和时间窗口过滤（例如只看最近 10 分钟写入相关条目），提高定位效率。

