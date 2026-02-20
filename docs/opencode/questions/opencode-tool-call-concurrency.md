# OpenCode Tool Call 并发机制（questions）

## 结论

OpenCode 的结论分两层：

1. **主 Agent loop 没有自建“显式并发调度器”**（不像 codex 的 runtime 锁或 gemini 的 scheduler queue）。  
2. **并发能力主要来自底层 AI SDK/Provider 与 batch 工具**：  
   - 常规 tool call 由 `streamText` + provider 行为决定；框架侧可处理多 `toolCallId` 事件。  
   - 明确可控并发由 `batch` 工具提供（`Promise.all` 并发执行多工具）。

- 项目名 + 文件路径 + 关键职责：
  - `opencode` + `opencode/packages/opencode/src/session/llm.ts`：通过 `streamText` 发起工具调用流。
  - `opencode` + `opencode/packages/opencode/src/session/processor.ts`：按 `toolCallId` 跟踪 `tool-call/tool-result/tool-error`。
  - `opencode` + `opencode/packages/opencode/src/tool/batch.ts`：显式并发执行（`Promise.all`）。
  - `opencode` + `opencode/packages/opencode/src/provider/sdk/copilot/responses/openai-responses-language-model.ts`：可下发 `parallel_tool_calls` provider 选项。

## 如何实现

### A. 常规 tool call（隐式并发能力）

1. `SessionPrompt.loop()` 每 step 调用 `processor.process()`。  
2. `processor.process()` 消费 `LLM.stream(streamText)` 事件流。  
3. 收到 `tool-call` / `tool-result` / `tool-error` 时，通过 `toolcalls[value.toolCallId]` 更新对应 part。  
4. 是否真正并行执行多个工具，取决于模型/provider + AI SDK 运行时策略（框架侧未单独实现并发调度器）。

### B. batch 工具（显式并发）

1. 输入 `tool_calls[]`（最多 25）。  
2. 每个子调用走 `executeCall()`。  
3. `Promise.all(toolCalls.map(executeCall))` 并发执行。  
4. 每个子调用独立写 tool part 状态（running/completed/error）。

## 流程图

```text
常规 tool call 路径
-------------------
+--------------------+      +------------------------+      +---------------------------+
| SessionPrompt.loop | ---> | SessionProcessor.process | ---> | LLM.stream (streamText)   |
+--------------------+      +------------------------+      +-------------+-------------+
                                                                      |
                                                                      v
                                                     +-----------------------------------+
                                                     | event type                         |
                                                     +--------+----------+--------+-------+
                                                              |          |        |
                                                              |          |        |
                                                     tool-call|  tool-result| tool-error
                                                              v          v        v
                                             +--------------------+ +----------------------+ +-------------------+
                                             | toolcalls[id]=running | toolcalls[id]=completed | toolcalls[id]=error |
                                             +--------------------+ +----------------------+ +-------------------+
                                                              |
                                                              | finish-step
                                                              v
                                           +----------------------------------------------+
                                           | 根据 finish reason 决定 continue/stop/compact |
                                           +----------------------------------------------+

batch 工具显式并发路径
----------------------
+-------------------+      +--------------+      +------------------------------+      +--------------------+
| BatchTool.execute | ---> | tool_calls[] | ---> | Promise.all(executeCall) 并发 | ---> | 汇总并记录各调用结果 |
+-------------------+      +--------------+      +------------------------------+      +--------------------+
```

## 数据格式

### 1) 消息 part（tool 状态）

`MessageV2` 的 `tool` part 在运行期记录：

- `callID`
- `state.status`: `pending|running|completed|error`
- `state.input`
- `state.output` / `state.error`
- `state.time.start/end`

### 2) batch 工具输入

```ts
{
  tool_calls: Array<{
    tool: string,
    parameters: Record<string, unknown>
  }>
}
```

### 3) provider 并发相关选项

在 OpenAI Responses 适配层可映射：

```ts
parallel_tool_calls: openaiOptions?.parallelToolCalls
```

## 不确定点与验证建议

- 不确定点：常规工具调用在各 provider 下是否“实际并行”，受 AI SDK/provider 默认策略影响。  
- 建议验证：对同模型构造 2 个慢工具调用（如 sleep 类）并记录开始/结束时间，观测是否重叠执行。
