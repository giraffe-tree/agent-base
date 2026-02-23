# Session Runtime（kimi-cli）

> 面向新手：这一章只回答三件事——会话何时创建/恢复、运行中写了什么、结束后如何收尾。

本文基于：
- `kimi-cli/src/kimi_cli/cli/__init__.py`
- `kimi-cli/src/kimi_cli/app.py`
- `kimi-cli/src/kimi_cli/session.py`
- `kimi-cli/src/kimi_cli/soul/context.py`
- `kimi-cli/src/kimi_cli/metadata.py`

---

## 1. 全局流程图（创建 / 继续 / 指定会话）

```text
CLI _run(session_id)
  -> if --session: Session.find() / Session.create(session_id)
  -> elif --continue: Session.continue_()
  -> else: Session.create()
  -> KimiCLI.create(session)
     -> Context.restore()
  -> run shell/print/acp/wire
  -> _post_run()
     - failed: 不更新 last_session_id
     - empty session: 删除目录
     - normal: 更新 last_session_id
```

代码锚点：
- `_run()` 主入口：`kimi-cli/src/kimi_cli/cli/__init__.py:457`
- `Session.find/create/continue_` 分支：`kimi-cli/src/kimi_cli/cli/__init__.py:464`
- `KimiCLI.create(session, ...)`：`kimi-cli/src/kimi_cli/cli/__init__.py:484`
- `_post_run()`：`kimi-cli/src/kimi_cli/cli/__init__.py:528`
- 空会话清理与 last_session_id 更新：`kimi-cli/src/kimi_cli/cli/__init__.py:544`

---

## 2. 数据流图（会话落盘）

```text
Session.create/find
  -> session_dir = ~/.kimi/sessions/<workdir_hash>/<session_id>/
  -> context.jsonl  (Context 历史)
  -> wire.jsonl     (Wire 事件流)

运行中
  -> Context.append_message / checkpoint / usage 持续追加 context.jsonl

收尾
  -> metadata(kimi.json).work_dirs[*].last_session_id 更新
```

代码锚点：
- `sessions_dir` 由 work_dir md5 派生：`kimi-cli/src/kimi_cli/metadata.py:33`
- `last_session_id` 字段：`kimi-cli/src/kimi_cli/metadata.py:29`
- `context_file` 与 `wire_file` 绑定：`kimi-cli/src/kimi_cli/session.py:131`
- `Context.restore()`：`kimi-cli/src/kimi_cli/soul/context.py:24`
- `Context.checkpoint()`：`kimi-cli/src/kimi_cli/soul/context.py:68`
- `Context.append_message()`：`kimi-cli/src/kimi_cli/soul/context.py:162`

---

## 3. 运行期关键行为

### 3.1 Session 标题怎么来

优先从 `wire.jsonl` 第一条 `TurnBegin` 推导标题；失败回退 `Untitled`。

代码锚点：
- `refresh()`：`kimi-cli/src/kimi_cli/session.py:65`
- 从 `TurnBegin` 抽首条用户文本：`kimi-cli/src/kimi_cli/session.py:72`

### 3.2 旧会话布局兼容

如果发现旧格式 `<session_id>.jsonl`，会迁移到新目录格式 `<session_id>/context.jsonl`。

代码锚点：
- 迁移函数：`kimi-cli/src/kimi_cli/session.py:252`

---

## 4. 设计意图与 trade-off

### 4.1 为什么 `last_session_id` 只做索引

设计意图：
- 把真实对话状态放在会话目录文件（`context.jsonl`/`wire.jsonl`），把 metadata 保持轻量索引。

trade-off：
- 优点：metadata 损坏时，对话数据仍可恢复。
- 代价：读取“最近会话详情”需要再访问会话目录。

### 4.2 为什么要删空会话

设计意图：
- 防止误触启动造成大量空目录污染列表。

trade-off：
- 优点：会话列表更干净。
- 代价：某些“只初始化不发消息”的调试现场不会保留。

代码锚点：
- 空会话判断：`kimi-cli/src/kimi_cli/session.py:49`
- 删除空会话：`kimi-cli/src/kimi_cli/cli/__init__.py:549`

---

## 5. 新手排障最短路径

1. 先看 `context.jsonl` 是否持续追加：`kimi-cli/src/kimi_cli/soul/context.py:167`
2. 再看 `wire.jsonl` 是否有 `TurnBegin/TurnEnd`：`kimi-cli/src/kimi_cli/session.py:131`
3. 最后看 `kimi.json` 的 `last_session_id` 是否更新：`kimi-cli/src/kimi_cli/cli/__init__.py:553`

---

## 6. 关键结论

- 会话事实来源是会话目录，不是 metadata。
- runtime 入口统一在 CLI `_run()`，收尾统一在 `_post_run()`。
- 新旧布局兼容靠运行时迁移，不需要手工脚本。
