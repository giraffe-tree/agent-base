# Agent Loop 机制对比

## 1. 概念定义

**Agent Loop** 是 Agent CLI 的核心机制，负责协调 "模型推理 → 工具调用 → 结果回注 → 继续推理" 的循环过程，直到任务完成或达到终止条件。

### 核心要素

- **循环驱动**：什么条件触发下一轮推理
- **工具执行**：如何调用工具并获取结果
- **上下文更新**：如何将工具结果反馈给模型
- **终止判断**：何时停止循环

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 使用基于步骤（Step）的循环，每次循环包含一次模型调用和可能的工具执行。循环在 `DefaultAgent` 类中实现。

**核心流程**

```python
# sweagent/agent/agents.py
class DefaultAgent(BaseAgent):
    def run(self, env: SWEEnv, problem_statement: str):
        # 1. 初始化历史
        self.history = []

        # 2. 添加系统提示
        self.history.append({"role": "system", "content": self.system_prompt})

        # 3. 添加问题描述
        self.history.append({"role": "user", "content": problem_statement})

        # 4. Agent Loop
        while True:
            # 4.1 检查终止条件
            if self._check_stop_condition():
                break

            # 4.2 调用模型
            response = self.model.query(self.history)

            # 4.3 解析响应
            thought, action = self.tools.parse_actions(response)

            # 4.4 执行工具
            if action:
                observation = env.execute(action)
            else:
                observation = ""

            # 4.5 更新历史
            self.history.append({"role": "assistant", "content": response})
            self.history.append({"role": "user", "content": observation})

            # 4.6 检查提交命令
            if self.tools.is_submit_command(action):
                break
```

**关键代码位置**

| 文件 | 行号 | 说明 |
|------|------|------|
| `sweagent/agent/agents.py` | 250 | `run()` 方法 |
| `sweagent/agent/agents.py` | 300 | `step()` 方法 |
| `sweagent/tools/tools.py` | 450 | `parse_actions()` |

**终止条件**
- 执行 `submit` 命令
- 达到最大步骤数
- 达到成本上限
- 检测到重复动作

### 2.2 Codex

**实现概述**

Codex 使用基于 Turn 的循环，采用 Actor 模型设计。`AgentLoop` 是核心 actor，`Turn` 表示单次交互上下文。

**核心流程**

```rust
// codex-rs/core/src/agent_loop.rs
impl AgentLoop {
    pub async fn run(&self, session: Arc<Session>) -> Result<()> {
        // 1. 初始化 Turn
        let turn = TurnContext::new(session);

        // 2. 发送初始事件
        self.event_tx.send(Event::TurnStarted).await?;

        // 3. Agent Loop
        loop {
            // 3.1 调用模型
            let response = self.send_prompt(&turn).await?;

            // 3.2 处理响应项
            for item in response.items {
                match item {
                    ResponseItem::FunctionCall { .. } => {
                        // 3.3 解析工具调用
                        let tool_call = self.router.build_tool_call(item).await?;

                        // 3.4 分发执行
                        let result = self.router.dispatch_tool_call(
                            tool_call, turn.clone()
                        ).await?;

                        // 3.5 将结果加入输入
                        turn.add_input_item(result).await;
                    }
                    ResponseItem::Message { content } => {
                        // 3.6 流式输出内容
                        self.event_tx.send(Event::Content { content }).await?;
                    }
                    _ => {}
                }
            }

            // 3.7 检查完成条件
            if response.status == "completed" {
                break;
            }
        }

        // 4. 结束 Turn
        self.event_tx.send(Event::TurnFinished).await?;
        Ok(())
    }
}
```

**关键代码位置**

| 文件 | 行号 | 说明 |
|------|------|------|
| `codex-rs/core/src/agent_loop.rs` | 150 | `AgentLoop::run()` |
| `codex-rs/core/src/agent_loop.rs` | 200 | `send_prompt()` |
| `codex-rs/core/src/tools/router.rs` | 100 | `dispatch_tool_call()` |

**终止条件**
- 模型返回 `completed` 状态
- 用户中断（AbortSignal）
- Hook 触发中止
- 达到最大轮次

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 使用递归 continuation 驱动的循环。`GeminiClient` 管理会话级状态，通过 `isContinuation` 标志控制循环继续。

**核心流程**

```typescript
// packages/core/src/core/client.ts
class GeminiClient {
    async *sendMessageStream(query: Query): AsyncGenerator<Event> {
        // 1. 重置 prompt 级状态
        this.loopDetector.reset();

        // 2. 调用 processTurn
        yield* this.processTurn(query);
    }

    private async *processTurn(query: Query): AsyncGenerator<Event> {
        // 2.1 检查终止条件
        if (this.sessionTurnCount >= this.maxSessionTurns) {
            yield { type: 'Finished', reason: 'MAX_TURNS' };
            return;
        }

        // 2.2 执行 Turn
        const turn = new Turn(query);
        for await (const event of turn.run()) {
            yield event;

            // 2.3 处理工具调用请求
            if (event.type === 'ToolCallRequest') {
                // 2.4 调度工具执行
                const result = await this.scheduler.schedule(event.toolCall);

                // 2.5 生成 functionResponse
                const response = this.buildFunctionResponse(result);

                // 2.6 递归继续（continuation）
                yield* this.sendMessageStream({
                    ...query,
                    context: [...query.context, response],
                    isContinuation: true
                });
                return;
            }
        }

        // 2.7 检查是否需要继续
        if (turn.hasPendingToolCalls()) {
            // 有 pending 工具调用，继续
            const pendingResults = await this.waitForPendingTools();
            yield* this.sendMessageStream({
                ...query,
                context: [...query.context, ...pendingResults],
                isContinuation: true
            });
        } else {
            // 无 pending 工具，结束
            yield { type: 'Finished', reason: 'NATURAL_STOP' };
        }
    }
}
```

**关键代码位置**

| 文件 | 行号 | 说明 |
|------|------|------|
| `packages/core/src/core/client.ts` | 100 | `sendMessageStream()` |
| `packages/core/src/core/client.ts` | 200 | `processTurn()` |
| `packages/core/src/core/turn.ts` | 50 | `Turn.run()` |

**终止条件**
- 无 pending tool calls
- 达到 MAX_TURNS
- Loop detection 触发
- Hook 触发 stop/block
- 上下文窗口溢出

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 使用基于 step 的循环，支持 checkpoint 回滚。`KimiSoul` 是核心类，`_agent_loop` 实现主循环。

**核心流程**

```python
# kimi-cli/src/kimi_cli/agent/soul.py
class KimiSoul:
    async def run(self, user_input: str):
        # 1. 初始化
        await self._refresh_token()
        await self._wire.send(TurnBegin())

        # 2. 判断模式
        if self.max_ralph_iterations != 0:
            # Ralph 自动迭代模式
            await self._ralph_loop(user_input)
        else:
            # 标准单轮模式
            await self._turn(user_input)

        await self._wire.send(TurnEnd())

    async def _turn(self, user_message: str):
        # 2.1 创建 checkpoint
        checkpoint_id = self.context.checkpoint()

        # 2.2 添加用户消息
        self.context.append(UserMessage(content=user_message))

        # 2.3 进入 Agent Loop
        await self._agent_loop()

    async def _agent_loop(self):
        step_no = 0

        while True:
            step_no += 1

            # 3.1 检查步骤上限
            if step_no > self.max_steps_per_turn:
                break

            await self._wire.send(StepBegin(step_no=step_no))

            try:
                # 3.2 执行 step
                outcome = await self._step()

                if outcome:
                    # 3.3 有结果，结束 turn
                    await self._wire.send(StepEnd(outcome=outcome))
                    break

                # 3.4 无结果（有 tool calls），继续循环
                await self._wire.send(StepContinue())

            except BackToTheFuture as e:
                # 3.5 D-Mail 回滚
                self.context.revert_to(e.checkpoint_id)
                self.context.checkpoint()
                self.context.append(SystemMessage(content=e.messages))

    async def _step(self) -> Optional[StepOutcome]:
        # 4.1 检查 token 预算，触发压缩
        if self.context.token_count + self.reserved >= self.max_context:
            await self._compact_context()

        # 4.2 调用 LLM
        result = await self.kosong.step(
            chat_provider=self.provider,
            context=self.context.history,
            toolset=self.toolset,
        )

        # 4.3 收集工具结果
        tool_results = await result.tool_results()

        # 4.4 增长上下文（受 shield 保护）
        await self._grow_context(result, tool_results)

        # 4.5 返回结果
        if not result.tool_calls:
            return StepOutcome.no_tool_calls()

        return None  # 继续循环
```

**关键代码位置**

| 文件 | 行号 | 说明 |
|------|------|------|
| `kimi-cli/src/kimi_cli/agent/soul.py` | 200 | `run()` 方法 |
| `kimi-cli/src/kimi_cli/agent/soul.py` | 300 | `_agent_loop()` |
| `kimi-cli/src/kimi_cli/agent/soul.py` | 400 | `_step()` |

**终止条件**
- 模型无 tool calls 输出
- 达到 `max_steps_per_turn`
- Step 执行异常
- D-Mail 主动回滚

### 2.5 OpenCode

**实现概述**

OpenCode 使用 `while(true)` 循环，支持任务分支（subtask/compaction/normal）。`SessionPrompt.loop` 是核心方法。

**核心流程**

```typescript
// packages/opencode/src/session/prompt.ts
class SessionPrompt {
    async loop({ sessionID }: { sessionID: string }): Promise<void> {
        while (true) {
            // 1. 读取消息流
            const messages = await MessageV2.stream({ sessionID });

            // 2. 定位关键消息
            const lastUser = this.findLastUser(messages);
            const lastAssistant = this.findLastFinished(messages);
            const tasks = this.findPendingTasks(messages);

            // 3. 退出条件检查
            if (lastAssistant?.finish &&
                lastAssistant.finish !== 'tool-calls' &&
                lastUser.id < lastAssistant.id) {
                return;  // 自然停止
            }

            // 4. 任务分支处理
            if (tasks.length > 0) {
                const task = tasks[0];

                if (task.type === 'subtask') {
                    // 4.1 子 Agent 任务
                    await this.handleSubtask(task);
                    continue;
                }

                if (task.type === 'compaction') {
                    // 4.2 上下文压缩
                    await this.handleCompaction(task);
                    continue;
                }
            }

            // 4.3 正常模型推理
            const result = await this.processNormal(sessionID, messages);

            // 5. 根据结果决策
            switch (result) {
                case 'continue':
                    continue;  // 继续循环
                case 'stop':
                    return;    // 结束循环
                case 'compact':
                    // 创建 compaction 任务，下次循环处理
                    await this.createCompactionTask(sessionID);
                    continue;
            }
        }
    }

    async processNormal(sessionID: string, messages: Message[]): Promise<string> {
        // 1. 获取当前 agent
        const agent = this.getCurrentAgent(messages);

        // 2. 检查 steps 上限
        if (this.stepCount >= agent.steps) {
            return 'stop';
        }

        // 3. 注入 reminders
        await this.insertReminders(messages, agent);

        // 4. 解析工具
        const tools = await this.resolveTools(agent);

        // 5. 调用 LLM
        const result = await SessionProcessor.process({
            sessionID,
            messages,
            tools,
        });

        // 6. 处理 finish reason
        if (result.finish === 'stop') {
            return 'stop';
        }
        if (result.finish === 'tool-calls') {
            return 'continue';
        }

        return 'continue';
    }
}
```

**关键代码位置**

| 文件 | 行号 | 说明 |
|------|------|------|
| `packages/opencode/src/session/prompt.ts` | 200 | `loop()` 方法 |
| `packages/opencode/src/session/processor.ts` | 100 | `process()` 方法 |
| `packages/opencode/src/session/llm.ts` | 50 | `LLM.stream()` |

**终止条件**
- `finish` 为 `stop`/`length`/`content-filter`
- 达到 agent `steps` 上限
- `blocked` 为 true（权限拒绝）
- 用户中断（AbortSignal）

---

## 3. 相同点总结

### 3.1 循环结构共性

| 特征 | 说明 |
|------|------|
| **模型调用** | 都通过某种方式调用 LLM 获取响应 |
| **工具解析** | 都解析模型输出中的工具调用请求 |
| **工具执行** | 都执行工具并将结果反馈给模型 |
| **循环继续** | 都根据条件决定是否继续下一轮 |

### 3.2 通用终止条件

所有 Agent 都支持以下终止条件：

- **自然停止**：模型不再请求工具调用
- **用户中断**：用户主动取消操作
- **上限触发**：达到最大轮次/步骤限制

### 3.3 事件/状态通知

| Agent | 通知机制 | 特点 |
|-------|----------|------|
| SWE-agent | 回调函数 | 简单直接 |
| Codex | Event channel | Actor 模型 |
| Gemini CLI | Event generator | 流式事件 |
| Kimi CLI | Wire 事件 | 结构化事件 |
| OpenCode | Bus 事件 | 发布订阅 |

---

## 4. 不同点对比

### 4.1 循环驱动方式

| Agent | 驱动方式 | 核心机制 | 特点 |
|-------|----------|----------|------|
| SWE-agent | Step 迭代 | `while` 循环 | 简单直接 |
| Codex | Turn 迭代 | Actor 消息 | 并发安全 |
| Gemini CLI | 递归 continuation | `yield*` 递归 | 状态清晰 |
| Kimi CLI | Step + checkpoint | `while` + 回滚 | 可恢复 |
| OpenCode | while(true) + 分支 | 条件分支 | 灵活扩展 |

### 4.2 工具调用处理

| Agent | 执行方式 | 并发支持 | 确认机制 |
|-------|----------|----------|----------|
| SWE-agent | 同步 | 否 | 无 |
| Codex | 异步 | 是 | tool_call_gate |
| Gemini CLI | 异步 | 是 | Scheduler 状态机 |
| Kimi CLI | 异步 | 是 | 审批管道 |
| OpenCode | 异步 | 是 | PermissionNext |

### 4.3 上下文更新时机

| Agent | 更新时机 | 保护机制 | 特点 |
|-------|----------|----------|------|
| SWE-agent | 每 step 后 | 无 | 简单 |
| Codex | 每个 tool 后 | 无 | 细粒度 |
| Gemini CLI | 每个 tool 后 | 无 | 递归传递 |
| Kimi CLI | 每 step 后 | `asyncio.shield` | 中断保护 |
| OpenCode | 每个 event 后 | 无 | 实时更新 |

### 4.4 特殊机制

| Agent | 特殊机制 | 用途 |
|-------|----------|------|
| SWE-agent | 成本上限 | 控制开销 |
| Codex | Hook 系统 | 扩展点 |
| Gemini CLI | Loop Detection | 防止循环 |
| Kimi CLI | Checkpoint + D-Mail | 状态回滚 |
| OpenCode | Compaction | 上下文压缩 |

---

## 5. 源码索引

### 5.1 主循环入口

| Agent | 文件路径 | 行号 | 函数/方法 |
|-------|----------|------|-----------|
| SWE-agent | `sweagent/agent/agents.py` | 250 | `run()` |
| Codex | `codex-rs/core/src/agent_loop.rs` | 150 | `AgentLoop::run()` |
| Gemini CLI | `packages/core/src/core/client.ts` | 100 | `sendMessageStream()` |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py` | 300 | `_agent_loop()` |
| OpenCode | `packages/opencode/src/session/prompt.ts` | 200 | `loop()` |

### 5.2 工具调用处理

| Agent | 文件路径 | 行号 | 函数/方法 |
|-------|----------|------|-----------|
| SWE-agent | `sweagent/agent/agents.py` | 300 | `step()` |
| Codex | `codex-rs/core/src/tools/router.rs` | 100 | `dispatch_tool_call()` |
| Gemini CLI | `packages/core/src/scheduler/scheduler.ts` | 100 | `schedule()` |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py` | 400 | `_step()` |
| OpenCode | `packages/opencode/src/session/processor.ts` | 200 | `process()` |

### 5.3 终止条件判断

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/agent/agents.py` | 280 | `is_submit_command` |
| Codex | `codex-rs/core/src/agent_loop.rs` | 200 | `response.status` |
| Gemini CLI | `packages/core/src/core/client.ts` | 150 | `hasPendingToolCalls` |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py` | 320 | `step_no` 检查 |
| OpenCode | `packages/opencode/src/session/prompt.ts` | 220 | `finish` 检查 |

---

## 6. 流程图对比

### 6.1 SWE-agent 循环

```text
┌─────────────┐
│   Start     │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Check stop │
└──────┬──────┘
       │
   ┌───┴───┐
   │ Yes   │ No
   ▼       ▼
┌──────┐ ┌─────────┐
│ Done │ │ LLM call│
└──────┘ └────┬────┘
              │
              ▼
       ┌────────────┐
       │ Parse action│
       └──────┬─────┘
              │
         ┌────┴────┐
         │ Has     │
         │ action? │
         └────┬────┘
            ┌─┴─┐
        Yes │   │ No
            ▼   ▼
      ┌────────┐ ┌─────────┐
      │ Execute│ │ Skip    │
      │ tool   │ │         │
      └───┬────┘ └────┬────┘
          │           │
          └─────┬─────┘
                │
                ▼
         ┌────────────┐
         │ Update     │
         │ history    │
         └──────┬─────┘
                │
                ▼
         ┌────────────┐
         │ Check      │
         │ submit?    │
         └──────┬─────┘
            ┌───┴───┐
        Yes │       │ No
            ▼       ▼
      ┌────────┐ ┌─────────┐
      │ Done   │ │ Loop    │
      └────────┘ └─────────┘
```

### 6.2 Gemini CLI 递归循环

```text
┌─────────────────────────────┐
│ sendMessageStream(query)    │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ processTurn()               │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ Turn.run()                  │
└───────────┬─────────────────┘
            │
            ▼
    ┌───────────────┐
    │ ToolCall?     │
    └───────┬───────┘
        ┌───┴───┐
    Yes │       │ No
        ▼       ▼
┌───────────┐ ┌──────────────┐
│ schedule()│ │ Finished     │
│ execute   │ └──────────────┘
└─────┬─────┘
      │
      ▼
┌─────────────────────────────┐
│ build functionResponse      │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ sendMessageStream(          │◄───┐
│   isContinuation=true       │    │ 递归
│ )                           │────┘
└─────────────────────────────┘
```

### 6.3 OpenCode 分支循环

```text
┌─────────────────────────────┐
│ loop()                      │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ Check exit condition        │
└───────────┬─────────────────┘
            │
        ┌───┴───┐
    Yes │       │ No
        ▼       ▼
   ┌────────┐ ┌───────────────┐
   │ Return │ │ Check tasks   │
   └────────┘ └───────┬───────┘
                      │
                 ┌────┴────┐
           Empty │         │ Has tasks
                 ▼         ▼
           ┌────────┐ ┌───────────────┐
           │ Normal │ │ Handle task   │
           │ branch │ │ - subtask     │
           └────┬───┘ │ - compaction  │
                │     └───────┬───────┘
                │             │
                └──────┬──────┘
                       │
                       ▼
               ┌───────────────┐
               │ process()     │
               └───────┬───────┘
                       │
                       ▼
               ┌───────────────┐
               │ Check result  │
               └───────┬───────┘
                  ┌────┼────┐
             stop│cont│comp│
                  ▼    ▼    ▼
             ┌────────┐  ┌────────┐
             │ Return │  │ Loop   │◄─────┐
             └────────┘  └────────┘      │
                          │              │
                          ▼              │
                   ┌──────────────┐      │
                   │ create       │      │
                   │ compaction   │──────┘
                   │ task         │
                   └──────────────┘
```
