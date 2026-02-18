# Agent Loop（Gemini CLI）

本文基于 `gemini-cli` 源码实现，说明 Gemini CLI 如何把「模型流式输出 + 工具调用 + 工具结果回注 + 继续推理」组织成一个可控的 Agent loop。
目标读者是开发者，重点在架构原理与关键控制点，而不是 UI 使用说明。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图

```text
+------------------------+
| submitQuery()          |
| (UI: useGeminiStream)  |
+-----------+------------+
            |
            v
+------------------------+
| GeminiClient.sendMessageStream()
| - 重置状态(loop detector等)
| - BeforeAgent Hook 检查
+-----------+------------+
            |
            v
+------------------------+
| processTurn()          |
| - 轮次/上下文保护      |
| - 模型与工具集选择     |
+-----------+------------+
            |
            v
+------------------------+
| Turn.run()             |
| - 流式产出 Content     |
| - 产出 Thought         |
| - 产出 ToolCallRequest |
+-----------+------------+
            |
            v
+------------------------+
| UI: scheduleToolCalls()|
| - 提交到 Scheduler     |
+-----------+------------+
            |
            v
+------------------------+
| Scheduler 执行工具     |
| - 审批/执行/收敛       |
+-----------+------------+
            |
            v
+------------------------+
| handleCompletedTools() |
| - 生成 functionResponse|
+-----------+------------+
            |
            v
+------------------------+
| submitQuery(isContinuation=true)
| - 递归进入下一轮       |
+-----------+------------+
            |
            v
+------------------------+
| 无 pending tool calls? |
+-----------+------------+
       Yes  |          No
            v           |
    +-------+--------+  |
    | Finished 收敛    |<-+
    +------------------+
```

### 1.2 关键分支流程图

```text
[A] ToolCallRequest 分支（Scheduler 状态机）

ToolCallRequest
  |
  +-- Scheduler.schedule()
       |
       v
  +----+----+     +----------------+
  |Validating|---->|AwaitingApproval|
  +----+----+     +--------+-------+
       |                   |
       v                   v
  +----+----+     +--------+-------+
  |Scheduled |     |  Error         |
  +----+----+     +----------------+
       |
       v
  +----+----+
  |Executing|
  +----+----+
       |
       +----+----+----+
       v    v    v    v
    +----+ +----+ +----+ +------+
    |Success| |Error| |Cancelled|
    +----+ +----+ +----+ +------+


[B] Loop Detection 分支

Turn 执行中
  |
  +-- 连续相同工具调用? --> Yes --> Loop 检测触发
  |
  +-- 内容分块重复(chanting)? --> Yes --> Loop 检测触发
  |
  +-- 长轮次后 LLM 语义判定? --> Yes --> Loop 检测触发
  |
  +-- 无异常 --------------> 继续正常流程


[C] 终止条件分支

processTurn 中检查
  |
  +-- MAX_TURNS 达到? -----------> 终止循环
  |
  +-- maxSessionTurns 达到? -----> 终止循环
  |
  +-- 用户中断(AbortSignal)? ----> 终止循环
  |
  +-- loop detection 命中? ------> 终止循环
  |
  +-- hook stop/block? ----------> 终止循环
  |
  +-- 上下文窗口溢出? -----------> 终止循环
  |
  +-- 无 pending tool calls? ----> 正常收敛结束


[D] InvalidStream 续跑分支

Turn.run()
  |
  +-- InvalidStream 事件?
       |
       +-- 允许继续? --> Yes
       |    |
       |    v
       |  注入 "Please continue."
       |    |
       |    v
       |  递归 sendMessageStream()
       |    (带防重试上限)
       |
       +-- 不允许? --> 终止
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道主路径是 continuation 递归驱动）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道 Scheduler 状态机、loop detection、终止条件）。
- **10 分钟版**：通读 `3~8`（能定位 continuation、工具执行、异常续跑等问题）。

### 2.1 一句话定义

Gemini CLI 的 Agent Loop 是“**递归 continuation 驱动的模型-工具循环**”：每轮模型流可能产出工具调用；工具经 Scheduler 状态机执行后回注；`isContinuation=true` 递归继续；直到无 pending tool calls 或命中终止条件。

---

## 3. Agent loop 的核心定义

在 Gemini CLI 中，**一次用户请求并不等于一次模型调用**。
只要模型持续发出 `functionCall`，系统就会：

1. 执行工具；
2. 把 `functionResponse` 回注给模型；
3. 继续下一轮模型推理；
4. 直到没有工具调用或命中终止条件。

这个循环由三层协作完成：

- `GeminiClient`：会话级编排器，管理"是否继续下一轮"
- `Turn`：单轮模型流解析器，把原始流转成统一事件
- `Scheduler`：工具执行状态机（校验、策略、审批、执行、收敛）


## 4. 从哪里进入循环

### 4.1 UI 入口

交互入口在 `packages/cli/src/ui/hooks/useGeminiStream.ts` 的 `submitQuery()`：

- 用户输入进入 `prepareQueryForGemini()`
- 调用 `geminiClient.sendMessageStream(...)` 发起一轮
- 在 `processGeminiStreamEvents(...)` 里消费事件流
- 收集到 `ToolCallRequest` 后调用 `scheduleToolCalls(...)`
- 工具完成后由 `handleCompletedTools(...)` 把 `functionResponse` 再次 `submitQuery(..., { isContinuation: true })`

这就形成了 UI 侧的“请求 -> 工具 -> 续跑”闭环。

### 4.2 Core 入口

核心入口在 `packages/core/src/core/client.ts` 的 `GeminiClient.sendMessageStream()`：

- 重置/维护 prompt 级状态（loop detector、hook state、model stickiness）
- 触发 BeforeAgent Hook（可 stop / block / 注入额外上下文）
- 调用 `processTurn(...)` 执行当前轮
- 若满足条件，递归 `yield* sendMessageStream(...)` 继续下一轮（这就是 agent loop 的核心）


## 5. 单轮（Turn）内部在做什么

`processTurn(...)` 是每一轮的主流程，关键步骤如下。

### 5.1 轮次与上下文保护

在真正请求模型前，会先做一批防护：

- `sessionTurnCount` 与 `maxSessionTurns` 检查（会话总轮次上限）
- `tryCompressChat(...)`：必要时压缩历史上下文
- `tryMaskToolOutputs(...)`：遮罩大体积工具输出，减少 token 消耗
- 估算当前请求 token，若超剩余窗口，发 `ContextWindowWillOverflow` 并停止
- IDE 模式下追加 editor context（全量或增量 JSON），但若存在 pending tool call 则不注入，避免破坏 functionCall/functionResponse 邻接约束

### 5.2 模型与工具集选择

同一条执行链路中，模型有“粘性”（`currentSequenceModel`）：

- 首轮可由路由器决定模型
- 后续 continuation 默认沿用同一模型，减少行为抖动
- 然后再经 availability/fallback 策略得到最终模型
- 依据最终模型重新设置工具声明（`setTools(modelToUse)`）

### 5.3 Turn 事件流解析

`Turn.run(...)`（`packages/core/src/core/turn.ts`）负责把 `GeminiChat.sendMessageStream(...)` 的底层流转成标准事件：

- `Content`：普通文本增量
- `Thought`：思考摘要
- `ToolCallRequest`：从 `functionCalls` 解析出的工具请求
- `Finished`：带 finishReason 的结束事件
- 以及 `Retry` / `InvalidStream` / `AgentExecutionStopped` / `AgentExecutionBlocked` 等控制事件

也就是说，`Turn` 是“模型响应协议”到“Agent 运行事件协议”的转换层。


## 6. 工具执行子循环（Scheduler）

工具执行由 `packages/core/src/scheduler/scheduler.ts` 的 `Scheduler` 驱动，是一个事件驱动状态机。

### 6.1 状态流转

典型状态路径：

`Validating -> (AwaitingApproval | Scheduled | Error) -> Executing -> (Success | Error | Cancelled)`

### 6.2 关键步骤

1. **入队与串行批处理**：`schedule()` 支持请求排队，避免并发冲突  
2. **参数构建与校验**：`tool.build(args)` 失败即 `INVALID_TOOL_PARAMS`  
3. **策略检查**：`checkPolicy(...)` 可直接拒绝（DENY）  
4. **用户审批**：`ASK_USER` 走 `resolveConfirmation(...)`，支持一次批准/总是批准/取消/修改  
5. **执行**：`ToolExecutor.execute(...)` 运行工具并回传 live output  
6. **结果标准化**：统一封装成 `functionResponse` parts，供模型下一轮消费

### 6.3 取消与收敛

- 用户取消会触发 `cancelAll()`，取消当前与排队项
- 批次结束后统一返回 `CompletedToolCall[]`
- UI 层收到后决定是否继续回注给模型


## 7. 循环如何"继续"

继续条件不在 Scheduler，而在 UI + Client 协作层：

1. `Turn` 产出 `ToolCallRequest` 事件；
2. UI 调 `scheduleToolCalls(...)`；
3. 工具完成后，`handleCompletedTools(...)` 抽取 `responseParts`；
4. 调用 `submitQuery(responsesToSend, { isContinuation: true }, prompt_id)`；
5. 再次进入 `geminiClient.sendMessageStream(...)`。

因此，Gemini CLI 的 agent loop 是“**模型流 + 工具状态机 + continuation 递归**”共同完成的，而不是单一 `while`。


## 8. 终止条件与保护机制

以下任一命中都可让循环停止或中断：

- 当前轮无 `pendingToolCalls` 且 next-speaker 判定无需模型继续
- `MAX_TURNS`（单次 sendMessageStream 最大递归深度，默认 100）
- `maxSessionTurns`（会话级总轮次上限）
- 用户中断（AbortSignal / ESC）
- loop detection 命中（工具重复、内容循环、LLM 判定循环）
- hook stop/block
- 上下文窗口溢出预警

其中 loop detection 有三层：

- 连续相同工具调用阈值检测
- 内容分块重复（chanting）检测
- 长轮次后的 LLM 语义判定（含双模型复核）


## 9. 异常与重试策略

### 9.1 模型流重试

`GeminiChat.sendMessageStream(...)` 内置网络/内容重试逻辑：

- 连接期可重试错误（网络瞬断等）
- 部分 InvalidStream 场景可重试
- 重试时发 `Retry` 事件，UI 可清理半成品输出

### 9.2 InvalidStream 续跑

在 `GeminiClient.processTurn(...)` 中，若收到 `InvalidStream` 且允许继续，会注入 `System: Please continue.` 再递归一轮（带防重试上限），避免单次坏流直接终止任务。


## 10. 一个简化时序

一次典型“会调用工具”的请求路径：

1. 用户输入 -> `submitQuery()`  
2. `GeminiClient.sendMessageStream()` -> `processTurn()`  
3. `Turn.run()` 流式产出 `Content` + `ToolCallRequest`  
4. UI 收集 tool calls -> `Scheduler.schedule()`  
5. Scheduler 完成审批/执行 -> 返回 `functionResponse`  
6. UI `submitQuery(functionResponse, isContinuation=true)`  
7. 进入下一轮模型推理，直到不再发 tool call  
8. 触发最终 `Finished`，循环收敛


## 11. 架构特点总结

Gemini CLI 的 Agent loop 设计目标是“可持续推进 + 可控风险 + 可观测”：

- **可持续推进**：通过 continuation 递归驱动多轮工具-推理链
- **可控风险**：策略引擎 + 审批流程 + 取消机制共同约束执行
- **可观测**：统一事件流（content/thought/tool/retry/error）贯穿 core 到 UI
- **可恢复与可扩展**：支持压缩、fallback、hooks、loop detection、模型路由等扩展点

对开发者而言，理解这条主线就能快速定位问题：

- 回答为什么没有继续：看 continuation 与 pending tool calls
- 回答为什么停了：看 stop 条件（loop/abort/hook/token/turn limit）
- 回答为什么工具没执行：看 Scheduler 状态（policy/approval/execution）