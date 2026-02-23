# UI Interaction（kimi-cli）

> 面向新手：把 UI 理解成“可替换外壳”，Soul 才是核心执行器，二者靠 Wire 协议解耦。

本文基于：
- `kimi-cli/src/kimi_cli/soul/__init__.py`
- `kimi-cli/src/kimi_cli/wire/__init__.py`
- `kimi-cli/src/kimi_cli/ui/shell/__init__.py`
- `kimi-cli/src/kimi_cli/ui/print/__init__.py`
- `kimi-cli/src/kimi_cli/wire/server.py`

---

## 1. 全局交互图

```text
UI (shell / print / wire server)
  -> run_soul(soul, user_input, ui_loop_fn, cancel_event)
  -> Wire(raw + merged)
  -> Soul.run(...)
  -> UI 持续消费 Wire 消息并可回包请求
```

代码锚点：
- `run_soul()`：`kimi-cli/src/kimi_cli/soul/__init__.py:121`
- 创建 `Wire`：`kimi-cli/src/kimi_cli/soul/__init__.py:141`
- Soul 发消息入口 `wire_send()`：`kimi-cli/src/kimi_cli/soul/__init__.py:197`
- Wire 双通道（raw/merged）：`kimi-cli/src/kimi_cli/wire/__init__.py:23`

---

## 2. 完整时序（含审批/外部工具请求）

```text
1) UI 提交 prompt
2) Soul 进入 turn，发送事件消息（TurnBegin/StepBegin/...）
3) 若需审批/外部工具：发送 ApprovalRequest / ToolCallRequest
4) UI 返回 response（approve/reject 或 tool result）
5) Soul 继续执行并发送 ToolResult / TurnEnd
6) run_soul 收尾：wire.shutdown()，UI 退出
```

代码锚点：
- Soul 任务与 UI 任务并行：`kimi-cli/src/kimi_cli/soul/__init__.py:145`
- 取消事件监听：`kimi-cli/src/kimi_cli/soul/__init__.py:150`
- 收尾 `wire.shutdown()`：`kimi-cli/src/kimi_cli/soul/__init__.py:173`
- Wire 请求在 server 中转发：`kimi-cli/src/kimi_cli/wire/server.py:631`
- 请求响应映射（approval/tool）：`kimi-cli/src/kimi_cli/wire/server.py:572`

---

## 3. Wire 数据流（为什么有 merge）

```text
SoulSide.send(msg)
  -> raw_queue: 全量消息（精确）
  -> merged_queue: 可合并消息做 merge（更平滑）
  -> 可选 recorder: merged 消息写 wire.jsonl
```

设计意图：
- raw 用于协议精确语义，merged 用于 UI 体验（减少闪烁/噪声）。

工程 trade-off：
- 优点：同一执行流同时满足“可回放”和“可读性”。
- 代价：要维护两路消息一致性与 flush 时机。

代码锚点：
- raw/merged 发布：`kimi-cli/src/kimi_cli/wire/__init__.py:76`
- merge buffer 与 flush：`kimi-cli/src/kimi_cli/wire/__init__.py:100`
- recorder 落盘：`kimi-cli/src/kimi_cli/wire/__init__.py:130`

---

## 4. 各 UI 形态的职责边界

- Shell UI：交互式回路 + slash 命令 + Ctrl-C 中断。
- Print UI：批处理/管道模式，支持 `final_only`。
- Wire Server：JSON-RPC 桥接，负责请求转发与 pending 清理。

代码锚点：
- Shell 主循环：`kimi-cli/src/kimi_cli/ui/shell/__init__.py:51`
- Shell 触发 `run_soul()`：`kimi-cli/src/kimi_cli/ui/shell/__init__.py:233`
- Print 触发 `run_soul()`：`kimi-cli/src/kimi_cli/ui/print/__init__.py:84`
- Wire server prompt 处理：`kimi-cli/src/kimi_cli/wire/server.py:395`
- turn 结束后清理 stale pending：`kimi-cli/src/kimi_cli/wire/server.py:446`

---

## 5. 关键结论

- UI 是可替换层，Soul + Wire 是稳定内核。
- `run_soul()` 统一处理并发、取消、收尾，避免不同 UI 行为分叉。
- Wire 的 request/response 机制是审批与外部工具集成的关键接缝。
