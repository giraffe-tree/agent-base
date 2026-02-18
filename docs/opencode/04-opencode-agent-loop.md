# Agent Loop（opencode）

本文基于 `opencode` 源码实现，说明 opencode 如何将「模型流式输出 + 工具调用 + 任务编排 + 上下文压缩」组织成一个可控的 Agent Loop。
目标读者是开发者，重点在架构原理与关键控制点。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图

```text
+------------------------+
| SessionPrompt.prompt() |
+-----------+------------+
            |
            v
+------------------------+
| createUserMessage()    |
| - 写入 DB              |
| - 广播 Bus 事件        |
+-----------+------------+
            |
            v
+------------------------+
| loop() while(true)     |
+-----------+------------+
            |
            v
+------------------------+
| 从 DB 读取消息流       |
| - 定位 lastUser        |
| - 定位 lastFinished    |
| - 定位 tasks           |
+-----------+------------+
            |
            v
+------------------------+
| 退出条件检查           |
| (finish reason 检查)   |
+-----------+------------+
       Yes  |          No
            v           |
    +-------+--------+  |
    | return 结束     |<-+
    +-----------------+
            |
            v
+------------------------+
| tasks 分支判断         |
+-----------+------------+
            |
    +-------+--------+--------+
    v       v                v
 Subtask  Compaction      Normal
 (3.1)     (3.2)          (3.3)
    |       |                |
    +-------+--------+--------+
                     |
                     v
         +-----------+-----------+
         | SessionProcessor.process()
         | - LLM.stream()        |
         | - 处理工具调用         |
         +-----------+-----------+
                     |
                     v
         +-----------+-----------+
         | 处理 finish reason    |
         | - continue/stop/compact|
         +-----------------------+
                     |
                     v
         +-----------+-----------+
         | 回到 loop() 头部      |
         +-----------------------+
```

### 1.2 关键分支流程图

```text
[A] Subtask 分支（子 Agent 派发）

task.type === "subtask"
  |
  v
创建 assistant 消息 (mode: task.agent)
  |
  v
记录 tool Part (type = TaskTool)
  |
  v
TaskTool.execute()
  |
  +-- 创建独立 Session/消息链
  |
  v
记录执行结果 (completed/error)
  |
  v
有 command? --Yes--> 追加合成用户消息
  |
  v
continue (回到 loop 头部)


[B] Compaction 分支（上下文压缩）

task.type === "compaction"
  |
  v
SessionCompaction.process()
  |
  +-- compaction agent 读入历史
  |
  +-- 生成结构化摘要
      (目标/进展/相关文件)
  |
  v
写入 assistant 消息 (summary: true)
  |
  v
auto: true? --Yes--> 注入 "Continue..."
  |
  v
"continue" (回到 loop 头部)
  |
  +-- 此后 filterCompacted
      跳过被压缩的历史


[C] Doom Loop 检测分支

processor.ts tool-call 事件
  |
  v
检查最近 3 个 tool Part:
  - type === "tool"
  - tool 名相同
  - input 完全相同
  - status !== "pending"
  |
  +-- 命中? --Yes--> PermissionNext.ask({ permission: "doom_loop" })
       |
       v
  用户选择: 继续 / 终止


[D] 权限拒绝分支

工具执行
  |
  v
PermissionNext.RejectedError?
  |
  +-- Yes
       |
       v
  blocked = true
       |
       v
  process() returns "stop"
       |
       v
  主循环退出


[E] Normal 分支（正常推理）

无待处理任务
  |
  v
Agent 解析 + steps 上限检查
  |
  v
insertReminders() 注入提示词
  |
  v
resolveTools() 合并并过滤工具
  |
  v
SessionProcessor.process()
  |
  v
根据返回值决策:
  - "continue" -> 继续 loop
  - "stop"     -> 退出 loop
  - "compact"  -> 创建 compaction 任务
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道主循环是 while(true)，按 finish reason 退出）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道 subtask/compaction/doom loop 分支）。
- **10 分钟版**：通读 `3~11`（能定位工具不执行、不继续、context 变短等问题）。

### 2.1 一句话定义

opencode 的 Agent Loop 是"**任务驱动的多分支循环**"：主循环从消息流中定位任务，按优先级走 subtask/compaction/normal 分支；单轮流解析模型输出并处理工具；根据 finish reason 和状态决定继续、停止或压缩上下文。

---

## 3. Agent Loop 的核心定义

在 opencode 中，**一次用户请求并不等于一次模型调用**。  
只要模型持续发出工具调用（finish reason 为 `tool-calls`），系统就会：

1. 执行工具；
2. 把工具结果回注给模型；
3. 继续下一轮模型推理；
4. 直到不再有工具调用，或命中终止条件。

这个循环由三层协作完成：

- **`SessionPrompt.loop`**：主循环编排器（`session/prompt.ts`），驱动整条 Agent Loop
- **`SessionProcessor`**：单轮流解析器（`session/processor.ts`），将原始 SDK 事件转成结构化 Part
- **`LLM.stream`**：底层 LLM 流封装（`session/llm.ts`），统一不同 Provider 的调用方式


---

## 4. 从哪里进入循环

### 4.1 外部入口

用户输入经过 CLI/UI 后，调用 `SessionPrompt.prompt()`：

1. **创建用户消息**（`createUserMessage`），写入 DB 并广播 Bus 事件；
2. 若有文件附件、MCP 资源等，在此阶段展开并转为 `Part`；
3. 若 `noReply === true` 则直接返回，否则调用 `loop({ sessionID })`。

### 4.2 Loop 本体

`loop()` 是一个无限 `while (true)` 循环，每次迭代：

1. 从 DB 流式读取所有消息（`MessageV2.stream`），过滤已压缩的历史；
2. 定位 `lastUser`（最新用户消息）、`lastFinished`（最近已完成的 assistant 消息）、`tasks`（待执行的 subtask/compaction 任务）；
3. 判断是否退出、分支处理，或正常走模型推理。

**退出条件**（位于迭代开头）：
```
lastAssistant.finish 存在
  AND finish !== "tool-calls" AND finish !== "unknown"
  AND lastUser.id < lastAssistant.id
```
满足时，代表模型已自然停止，循环退出。

---

## 5. Loop 内部的三条分支路径

每次迭代从 `tasks` 中取出待处理任务，按优先级依次判断：

### 5.1 Subtask 分支（子 Agent 派发）

若取出 `task.type === "subtask"`，表示有待执行的子 Agent 任务：

1. 创建一个 assistant 消息（`mode: task.agent`），在其中记录一个 `tool` Part（type = `TaskTool`）；
2. 调用 `TaskTool.execute()`，内部会为子 Agent 拉起一个独立的 Session 或消息链；
3. 记录执行结果（completed/error）；
4. 若任务附带 `command`，追加一条合成用户消息，供思维链推理模型（如 Gemini）使用；
5. `continue` 回到循环头部，进入下一轮。

子 Agent 的类型由 `task.agent`（如 `"explore"`、`"general"`）决定，其权限由该 agent 的 `PermissionNext.Ruleset` 控制。

### 5.2 Compaction 分支（上下文压缩）

若取出 `task.type === "compaction"`，调用 `SessionCompaction.process()`：

1. 用 `compaction` agent（隐藏 agent，无工具调用权限）读入当前所有历史消息；
2. 用预设 prompt 要求模型输出一份结构化摘要（包含目标、进展、相关文件等）；
3. 把摘要写入 assistant 消息（`summary: true`），标记此消息为"已压缩轮次"；
4. 自动压缩（`auto: true`）场景下，注入一条合成用户消息："Continue if you have next steps..."；
5. 返回 `"continue"` 后，主循环继续——此后历史消息的过滤器（`filterCompacted`）会跳过被压缩轮次之前的消息，实现滑动窗口效果。

### 5.3 Normal 分支（正常模型推理）

无待处理任务时，进入正常推理流程：

1. **Agent 解析**：从 `lastUser.agent` 获取当前 agent（如 `"build"` 或 `"plan"`），读取其 `steps` 上限；
2. **Reminder 注入**（`insertReminders`）：按 plan/build 模式切换，注入提示词或计划文件引用；
3. **消息包装**：若当前 step > 1 且有未完成的 queued 用户消息，对其文本包裹 `<system-reminder>` 标签；
4. **工具解析**（`resolveTools`）：合并 ToolRegistry 工具 + MCP 工具，按 agent 权限过滤；
5. **发起推理**：调用 `processor.process()`，驱动 `LLM.stream()`；
6. **结果处理**：根据返回值决定 `continue` / `break` / 触发 compaction。

---

## 6. SessionProcessor：单轮流解析

`SessionProcessor.create()` 返回一个带状态的对象，其 `process()` 方法内有一个内层 `while (true)` 循环，逐一处理 `LLM.stream()` 的全量事件流（`fullStream`）。

### 6.1 事件处理映射

| 事件类型 | 动作 |
|---|---|
| `start` | 设置 Session 状态为 `busy` |
| `reasoning-start/delta/end` | 创建/更新/关闭 `reasoning` Part，实时广播 delta |
| `tool-input-start` | 创建 `tool` Part，状态设为 `pending` |
| `tool-call` | 更新 Part 状态为 `running`，触发 Doom Loop 检测 |
| `tool-result` | 更新 Part 状态为 `completed`，记录输出与耗时 |
| `tool-error` | 更新 Part 状态为 `error`，若为 `RejectedError` 则标记 `blocked` |
| `start-step` | 打快照（`Snapshot.track()`），写入 `step-start` Part |
| `finish-step` | 计算 token 用量与费用，写入 `step-finish` Part，检测是否需要 compaction |
| `text-start/delta/end` | 维护 `text` Part，实时广播 delta，完成后触发 plugin hook |
| `error` | 抛出异常，进入 catch 分支 |

### 6.2 返回值含义

`process()` 返回三个字符串之一，交由 `loop()` 决策：

- `"continue"`：本轮正常结束，继续下一轮；
- `"stop"`：因为 `blocked`、错误或正常完成而停止整个循环；
- `"compact"`：触发 compaction（token 溢出），主循环创建 compaction 任务后 continue。

---

## 7. LLM 流封装

`LLM.stream()` 负责把 opencode 的逻辑层参数转成 AI SDK 的 `streamText()` 调用：

- **系统提示组合**：按优先级合并 agent prompt > provider-specific prompt（Anthropic / Gemini / OpenAI / ...）> environment prompt > 用户自定义 system；
- **Provider Options 合并**：`base options` → `model.options` → `agent.options` → `variant` 逐层 merge；
- **工具过滤**：`resolveTools` 依据 `PermissionNext.disabled()` 移除被禁用的工具；
- **LiteLLM 兼容**：消息历史含工具调用但当前无工具时，注入哑 `_noop` tool 以通过代理校验；
- **Tool Call 修复**：`experimental_repairToolCall` 会尝试大小写修正，失败时路由到内置 `invalid` tool 防止崩溃；
- **Prompt 缓存**：系统提示保持"2 段"结构（静态头 + 动态尾），便于 Anthropic 侧 prompt cache 命中。

---

## 8. Agent 系统

opencode 内置了若干预设 agent，通过配置文件可自定义扩展。

### 8.1 内置 Agent

| Agent | 模式 | 核心用途 |
|---|---|---|
| `build` | primary | 默认 agent，允许执行大部分操作 |
| `plan` | primary | 计划模式，禁止所有写入工具（仅可写计划文件） |
| `general` | subagent | 通用子 agent，禁用 todo 工具 |
| `explore` | subagent | 只读探索 agent（grep/glob/read/bash/websearch） |
| `compaction` | primary（hidden） | 上下文压缩专用，禁止所有工具 |
| `title` | primary（hidden） | 自动生成会话标题，轻量模型 |
| `summary` | primary（hidden） | 生成会话摘要 |

### 8.2 Plan 模式工作流（实验性）

当 `OPENCODE_EXPERIMENTAL_PLAN_MODE` 开启时，plan agent 的 `insertReminders` 会注入一套五阶段工作流 prompt：

1. **Phase 1 - 探索**：最多并行 3 个 `explore` 子 agent，理解代码库；
2. **Phase 2 - 设计**：1 个 `general` agent 设计实现方案；
3. **Phase 3 - 审查**：读取关键文件，确认计划与用户意图对齐；
4. **Phase 4 - 写计划**：将最终方案写入 plan file（唯一允许写入的文件）；
5. **Phase 5 - 退出**：调用 `plan_exit` 工具，通知用户计划完成，等待确认后切换 build agent 执行。

---

## 9. 终止与保护机制

### 9.1 正常终止

- 模型 finish reason 为 `stop` / `length` / `content-filter`（非 `tool-calls`/`unknown`）；
- agent 设置了 `steps` 上限（如 `steps: 5`），达到上限时注入 `MAX_STEPS` 提示并强制不再提供工具；
- `StructuredOutput` 工具被成功调用，立即退出并存储结构化结果。

### 9.2 Doom Loop 检测

`processor.ts` 中，每次 `tool-call` 事件触发时，检查当前 assistant 消息的最近 3 个 tool Part：

```
最近 3 个 Part 均满足：
  - type === "tool"
  - tool 名相同
  - input 完全相同（JSON stringify 对比）
  - status !== "pending"
```

命中时调用 `PermissionNext.ask({ permission: "doom_loop", ... })`，用户可选择继续或终止。

### 9.3 权限拒绝处理

工具执行被拒绝（`PermissionNext.RejectedError` 或 `Question.RejectedError`）时：
- `blocked = true`（除非配置了 `experimental.continue_loop_on_deny`）；
- 本轮结束后 `process()` 返回 `"stop"`，主循环退出。

### 9.4 用户中断

`AbortController.signal` 贯穿整条链路。主循环每次迭代开头检查 `abort.aborted`，`LLM.stream` 每次 `fullStream` 迭代也调用 `abort.throwIfAborted()`。调用 `SessionPrompt.cancel()` 即可随时中止。

---

## 10. 重试策略

`SessionRetry` 负责处理 API 错误的退避重试：

- **重试判定**：`isRetryable` 为 true 的 `APIError`，或 `rate_limit` / `overload` / `exhausted` 类型的错误；
- **退避计算**：优先读取响应头 `retry-after-ms` / `retry-after`，否则指数退避（初始 2s，系数 2，无 header 时上限 30s）；
- **重试状态**：重试期间 `SessionStatus` 设为 `{ type: "retry", attempt, next }`，UI 可展示倒计时；
- **上下文溢出不重试**：`ContextOverflowError` 直接交由 compaction 分支处理。

---

## 11. 上下文压缩策略（Compaction）

`SessionCompaction` 提供两种机制控制 token 消耗：

**Compaction（摘要压缩）**

当 `finish-step` 检测到 `isOverflow()` 返回 true 时触发：

```
已用 token ≥ (模型上下文窗口 - reserved buffer)
```

默认 `reserved = min(20000, maxOutputTokens)`。compaction 会生成一条带有 `summary: true` 标记的 assistant 消息，此后 `filterCompacted` 会跳过被压缩的历史，实现滑动窗口。

**Prune（输出裁剪）**

每次主循环结束时调用 `SessionCompaction.prune()`，从后往前扫描 tool Part，对超出最近 40,000 token 窗口的旧工具输出（设置 `time.compacted`），减少 token 重复传入。受保护的工具（如 `skill`）不会被裁剪。

---

## 12. 一个简化时序

一次典型"会调用多个工具"的请求路径：

```
用户输入
  └─> SessionPrompt.prompt()
        └─> createUserMessage()  ← 写入 DB
        └─> loop()
              ├─ [step 1] 构建 tools, 创建 assistant 消息
              │    └─> SessionProcessor.process()
              │          └─> LLM.stream() ── streamText (AI SDK)
              │                ├─ text-start/delta/end → TextPart 实时写 DB
              │                ├─ tool-call → ToolPart running, 执行工具
              │                ├─ tool-result → ToolPart completed
              │                └─ finish-step → 记录 token 用量, 检测 overflow
              │    → result: "continue"
              ├─ [step 2] 同上，继续下一轮工具推理
              │    → result: "continue"
              ├─ [step N] 模型不再调用工具
              │    → finish reason: "stop"
              │    → 退出条件命中
              └─ loop 退出, 返回最终 assistant 消息
```

---

## 13. 架构特点总结

opencode 的 Agent Loop 设计目标是「可持续推进 + 多 Agent 协作 + 安全可控 + 上下文韧性」：

- **可持续推进**：主循环 `while(true)` + 多轮工具执行，直到模型自然停止
- **多 Agent 协作**：subagent 派发机制（`subtask` Part）实现并行探索、设计与执行分离
- **安全可控**：权限系统（PermissionNext）+ doom loop 检测 + 用户中断 + plan 模式只读约束
- **上下文韧性**：compaction 摘要 + prune 裁剪，主动应对长对话的 token 溢出
- **可扩展**：Plugin hook 贯穿工具执行前后、消息变换、系统提示注入等关键节点

对开发者而言，理解这条主线就能快速定位问题：

- **为什么没有继续**：看 loop 退出条件（finish reason、blocked、error）
- **为什么工具没执行**：看 PermissionNext 规则（agent 权限 / session 权限 / doom loop）
- **为什么上下文变短了**：看 compaction 和 prune 触发逻辑（token 计算与 reserved buffer）
- **子 Agent 在哪里**：看 `subtask` Part 的创建路径（`command()` → `prompt()` → `loop()` 中的 subtask 分支）
