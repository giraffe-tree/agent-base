# Agent Loop（codex）

本文基于 `./codex`（重点 `codex/codex-rs/core`）源码，解释 codex 如何把「用户输入 -> 模型流式输出 -> 工具执行 -> 再采样」组织成可中断、可恢复、可控的 Agent Loop。  
为适配“先看全貌再看细节”的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图

```text
+------------------------+
| submit(Op::UserInput)  |
+-----------+------------+
            |
            v
+------------------------+
| submission_loop        |
| - new_turn_with_sub_id |
| - spawn_task(Regular)  |
+-----------+------------+
            |
            v
+------------------------+
| RegularTask::run       |
| -> run_turn            |
+-----------+------------+
            |
            v
+------------------------------+
| run_turn 初始化               |
| - TurnStarted                |
| - run_pre_sampling_compact   |
| - 记录 user/skill 到 history |
+--------------+---------------+
               |
               v
      +--------+---------+
      | run_turn loop    |
      +--------+---------+
               |
               v
+------------------------------+
| run_sampling_request         |
| - built_tools                |
| - try_run_sampling_request   |
| - stream events              |
+--------------+---------------+
               |
               v
     +---------+-----------------------------+
     | Completed.needs_follow_up ?           |
     +---------+-----------------------------+
               |Yes                          |No
               v                             v
+-----------------------------+    +----------------------+
| 继续下一轮 sampling         |    | break run_turn loop  |
| (已含工具输出/待处理输入)   |    +----------+-----------+
+--------------+--------------+               |
               |                              v
               +--------------------> +-------------------+
                                     | on_task_finished   |
                                     | -> TurnComplete    |
                                     +--------------------+
```

### 1.2 关键分支流程图

```text
[A] OutputItemDone 分支

OutputItemDone
  |
  +-- 是 tool call ------------------------------+
  |                                              |
  |  ToolRouter.build_tool_call                  |
  |    -> ToolCallRuntime.handle_tool_call       |
  |    -> ToolRouter.dispatch_tool_call          |
  |    -> ToolRegistry(handler)                  |
  |    -> 写 ResponseInputItem::*Output 到历史   |
  |    -> needs_follow_up = true                 |
  |                                              |
  +-- 不是 tool call ----------------------------+
     解析为 agent/reasoning 消息，记录历史，更新 last_agent_message


[B] Token/Compaction 分支（run_turn）

sampling 完成
  |
  +-- token_limit_reached && needs_follow_up --> run_auto_compact
  |                                              |
  |                                              +-- 成功：continue loop
  |                                              +-- 失败：结束 turn
  |
  +-- 其他情况 ---------------------------------> 按 needs_follow_up 走继续/结束


[C] 中断与错误分支

Op::Interrupt / cancellation_token.cancel()
  -> abort_all_tasks
  -> (必要时) terminate unified exec processes
  -> TurnAborted

stream/tool error
  -> EventMsg::Error
  -> 当前 turn 结束（可由用户继续下一次输入）
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道 loop 何时继续/结束）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道工具、压缩、中断如何影响 loop）。
- **10 分钟版**：通读 `3~8`（能定位绝大多数执行问题）。

### 2.1 一句话定义

codex 的 Agent Loop 是“**turn 内多轮采样循环**”：模型每轮可能产出工具调用；工具结果回注历史后继续采样；直到 `needs_follow_up=false` 才结束该 turn。

---

## 3. 入口与分层

核心分层如下：

- **`submission_loop()`**（`core/src/codex.rs`）：会话级事件循环，消费 `Op::*` 并分发任务。
- **`Session::spawn_task()` + `RegularTask::run()`**（`core/src/tasks/mod.rs` / `regular.rs`）：turn 生命周期（启动、取消、完成）。
- **`run_turn()`**（`core/src/codex.rs`）：agent 主循环。
- **`run_sampling_request()` / `try_run_sampling_request()`**：单轮采样与流式事件处理。
- **`ToolCallRuntime` + `ToolRouter` + `ToolRegistry`**：工具调用解析、调度、并发控制、审批与沙箱执行。

### 3.1 从 `submission_loop` 到 `run_turn`

当收到 `Op::UserInput` / `Op::UserTurn`：

1. `new_turn_with_sub_id()` 构建 `TurnContext`（model/sandbox/approval/cwd 等）；
2. 尝试 `steer_input()` 注入当前活动 turn；
3. 若无活动 turn，则 `spawn_task(..., RegularTask)`；
4. `RegularTask::run()` 最终调用 `run_turn(...)` 进入 loop。

---

## 4. `run_turn()`：外层主循环（turn 级）

`run_turn()` 是一个 `loop { ... }`，主逻辑：

1. 发送 `TurnStarted`；
2. 预采样压缩 `run_pre_sampling_compact`；
3. 记录用户输入与技能注入到 history；
4. 每轮调用 `run_sampling_request()`；
5. 根据 `needs_follow_up` 决定继续或结束。

`needs_follow_up=true` 的典型来源：

- 本轮出现工具调用（需要把工具结果喂回模型）；
- 本轮期间有 `pending_input` 注入。

当 `needs_follow_up=false`，本 turn 自然结束并返回最后一条 assistant 文本。

---

## 5. 单轮采样：`run_sampling_request` 与 `try_run_sampling_request`

### 5.1 `run_sampling_request`（请求级）

每轮先构建 Prompt：

- 输入：`history.for_prompt(...)`；
- 工具：`built_tools()`（本地工具 + MCP + app/connectors + dynamic tools）；
- 模型能力：`parallel_tool_calls`；
- 指令：`base_instructions` + personality + 可选 output schema。

然后执行 provider 级重试：

- 调 `try_run_sampling_request()`；
- 对可重试错误退避重试（必要时 websocket 回退 HTTPS）；
- `ContextWindowExceeded` / `UsageLimitReached` 直接上抛。

### 5.2 `try_run_sampling_request`（事件级）

按 `ResponseEvent` 增量处理：

- `OutputItemAdded`：发 turn item started（plan 模式有延迟策略）；
- `OutputTextDelta` / `Reasoning*Delta`：推送 UI 增量；
- `OutputItemDone`：分工具/非工具路径处理；
- `Completed`：更新 token usage，返回 `SamplingRequestResult`。

关键点：**工具 future 与流处理并行**。  
tool call 会放入 `in_flight`，流可继续；流结束后 `drain_in_flight()` 回收，保证 history/rollout 一致。

---

## 6. 工具调用链（OutputItemDone -> ToolOutput）

当 `OutputItemDone` 包含函数调用时：

1. `ToolRouter::build_tool_call()`：把 `ResponseItem` 标准化为 `ToolCall`；
2. `ToolCallRuntime::handle_tool_call()`：依据工具并行能力决定并发/串行；
3. `ToolRouter::dispatch_tool_call()` -> `ToolRegistry` handler；
4. handler 返回 `ResponseInputItem::*Output` 并写回历史；
5. `needs_follow_up=true`，触发下一轮采样。

特性：

- 支持取消：`CancellationToken` 触发后返回 `aborted by user`；
- 支持审批/沙箱：orchestrator 执行 approval -> sandbox attempt -> 必要时升级重试；
- 支持 hook：`after_tool_use` / `after_agent` 可继续或中止流程。

---

## 7. Compaction 如何嵌入 Loop

codex 使用“前置检查 + 触发后继续”的压缩策略：

1. **预采样压缩**：`run_pre_sampling_compact()`（turn 开始时）；
2. **采样后触发**：`total_usage_tokens >= auto_compact_limit` 且 `needs_follow_up=true` 时执行 `run_auto_compact()`；
3. 压缩任务按 provider 走本地/远程 compact；
4. 压缩后替换历史为“初始上下文 + 用户关键信息 + summary”，再继续 loop。

作用：长会话仍可推进，而不是直接上下文溢出失败。

---

## 8. 中断、替换、完成语义

`Session::spawn_task()` 启动新任务前会先 `abort_all_tasks(TurnAbortReason::Replaced)`，同一 session 同时只有一个 active turn。

- `Op::Interrupt`：取消 token，优雅终止，必要时强制 abort；
- 中断后会关闭 unified exec 进程，避免后台残留；
- 正常完成由 `on_task_finished()` 统一发 `TurnComplete`；
- 异常统一为 `EventMsg::Error`，保持前端状态一致。

---

## 9. 多 Agent 协作在 loop 中的位置

codex 不把 sub-agent 写成 `run_turn()` 的硬编码分支，而是通过工具层协作：

- `spawn_agent` / `send_input` / `resume_agent` / `wait` / `close_agent`；
- 这些本质上仍是 tool call，由当前 turn 的 loop 推进；
- 协作状态通过 `Collab*` 事件回传 UI；
- 深度限制、权限策略、agent role 在 handler 内控制。

结论：**主循环只关心 `needs_follow_up`；多 agent 只是 follow-up 的一种来源。**

---

## 10. 排障速查

- **模型没继续**：看 `Completed` 后 `needs_follow_up` 是否为 false。
- **工具没执行**：看 `OutputItemDone -> build_tool_call -> registry dispatch`。
- **突然中断**：看 `abort_all_tasks` / cancellation token / `TurnAborted`。
- **上下文变短**：看 `run_pre_sampling_compact` / `run_auto_compact` 是否触发。

---

## 11. 架构特点总结

- **事件驱动**：response stream 逐事件处理，UI 可实时感知文本/推理/工具状态。
- **任务化生命周期**：turn 作为后台 task 管理，支持替换、取消、统一收尾。
- **工具中心化**：模型循环只判断 follow-up，复杂性下沉到工具层。
- **长会话韧性**：compaction 与重试内建在 loop。
- **协作可扩展**：多 agent 通过工具协议注入，不破坏主循环结构。
