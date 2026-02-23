# Kimi CLI 上下文压缩机制（校正版）

本文基于当前源码：

- `src/kimi_cli/soul/kimisoul.py`
- `src/kimi_cli/soul/compaction.py`
- `src/kimi_cli/soul/context.py`
- `src/kimi_cli/soul/slash.py`

---

## 1. 是否支持压缩

支持。

当前实现是 `SimpleCompaction`，并通过 `KimiSoul.compact_context()` 接入主循环。

---

## 2. 触发方式

### 2.1 自动触发（主循环）

在每个 step 前判断：

- `context.token_count + reserved_context_size >= llm.max_context_size`

满足则执行 `compact_context()`。

配置项来源：`config.loop_control.reserved_context_size`（默认 `50000`）。

### 2.2 手动触发（slash）

`/compact` 命令会直接调用 `soul.compact_context()`。

---

## 3. 压缩流程（真实实现）

`compact_context()` 的流程：

1. `wire_send(CompactionBegin())`
2. 调用 `SimpleCompaction.compact(messages, llm)` 生成压缩结果
3. `context.clear()` 清空并轮转旧 `context.jsonl`
4. `checkpoint()` 新建 checkpoint 基线
5. 追加压缩后的消息到 context
6. `wire_send(CompactionEnd())`

注意：

- 当前没有独立的 `CheckpointManager` 模块。
- 当前没有“压缩后 checkpoint 链路图数据库式管理”。

---

## 4. SimpleCompaction 的策略

`SimpleCompaction(max_preserved_messages=2)` 默认：

- 保留最近 2 条 `user/assistant` 消息（按角色计）。
- 更早历史拼接成 compaction 输入，交给模型总结。
- 总结结果中的 `ThinkPart` 会被丢弃。
- 压缩后写入一条 system 提示前缀 + 总结内容 + 保留消息。

对应文件：`src/kimi_cli/soul/compaction.py`。

---

## 5. 与 D-Mail 的关系

- D-Mail 逻辑在 `denwarenji.py` + `kimisoul.py`。
- 压缩不会引入额外 `checkpoint manager` 层。
- 压缩后依然可以 checkpoint / revert_to，但语义仍由 `Context` 负责。

---

## 6. 关键纠正

以下旧说法不符合当前代码：

- `src/kimi_cli/checkpoint/manager.py`（不存在）
- `compact_conversation()`、`generate_structured_summary()`（当前无这些函数）
- “token 超过 75% 固定阈值”（当前是 `token + reserved >= max_context`）

