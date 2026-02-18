# Agent Loop（Kimi CLI）

本文基于 `kimi-cli` 源码，说明一次用户请求进入后，Agent loop 如何驱动 LLM、工具调用、审批、上下文演进与结束判定。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图

```text
┌─────────────────────────────────────────────────────────────────────┐
│  START: KimiSoul.run(user_input)                                     │
│  ┌─────────────────────────────────────────┐                        │
│  │ 初始化与准备                             │                        │
│  │  ├── 刷新 OAuth token                   │ ──► 防过期             │
│  │  ├── 发送 TurnBegin 事件                │ ──► 通知 UI            │
│  │  └── slash command 检查                 │                        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
       ┌────┴────┐
       ▼         ▼
┌──────────┐  ┌─────────────────────────────────────────────────────────┐
│ 是 command│  │ 普通输入: 模式选择                                       │
│   处理    │  │ ┌─────────────────────────────────────────────────────┐ │
└──────────┘  │ │ max_ralph_iterations?                               │ │
              │ │  ├── Yes: FlowRunner.ralph_loop()  ◄── 自动迭代模式   │ │
              │ │  └── No:  _turn() 标准流程        ◄── 单轮对话模式    │ │
              │ └────────┬────────────────────────────────────────────┘ │
              └──────────┼──────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Turn 层: _turn() 与 _agent_loop()                                   │
│  ┌─────────────────────────────────────────┐                        │
│  │ _turn() 初始化                           │                        │
│  │  ├── 校验 LLM 配置                      │                        │
│  │  ├── 创建 checkpoint (可回滚点)         │ ──► BackToTheFuture    │
│  │  └── append 用户消息到 context          │                        │
│  │                                         │                        │
│  │  _agent_loop() ◄───────────────────────┼──┐ ◄── 核心 while 循环  │
│  │   ├── [前置]                            │  │                      │
│  │   │   ├── 等待 MCP tools 就绪           │  │                      │
│  │   │   └── 启动审批转发协程              │  │                      │
│  │   │                                     │  │                      │
│  │   ├── [step 框架]                       │  │                      │
│  │   │   ├── step_no += 1                  │  │                      │
│  │   │   ├── 检查 max_steps_per_turn       │  │                      │
│  │   │   └── 发送 StepBegin 事件           │  │                      │
│  │   │                                     │  │                      │
│  │   ├── [执行]                            │  │                      │
│  │   │   └── _step()                       │  │                      │
│  │   │       ├── kosong.step()             │  │ ──► 调用 LLM         │
│  │   │       ├── 流式输出处理              │  │                      │
│  │   │       ├── 工具执行 (并行)           │  │                      │
│  │   │       └── _grow_context()           │  │ ──► 更新上下文       │
│  │   │                                     │  │    (shield 保护)     │
│  │   └── [判断]                            │  │                      │
│  │       └── 有 tool_calls?                │  │                      │
│  │           ├── Yes ──────────────────────┘  │                      │
│  │           │   (返回 None -> 继续 loop)       │                      │
│  │           └── No                           │                      │
│  │               └── 返回 StepOutcome        │                      │
│  │                   (结束 turn)              │                      │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  结束: TurnEnd 与清理                                                │
│  ┌─────────────────────────────────────────┐                        │
│  │ 发送 TurnEnd 事件                       │ ──► 通知 UI            │
│  │ 清理审批转发 task                       │                        │
│  │ 返回 turn 结果                          │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流  ◄── 模式分支
```

### 1.2 关键分支流程图

```text
┌──────────────────────────────────────────────────────────────────────┐
│ [A] Context Compaction 分支 —— 上下文压缩                             │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────┐
                    │   _agent_loop()         │
                    │   step 执行前            │
                    └──────────┬──────────────┘
                               │
                               ▼
                    ┌─────────────────────────┐
                    │ 检查 token 预算          │
                    │ context.token_count +   │
                    │ reserved >= max?        │
                    └──────────┬──────────────┘
                               │
              ┌────────────────┴────────────────┐
              │Yes                              │No
              ▼                                 ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ 📦 compact_context()│       │ 正常执行 step       │
    │ ─────────────────── │       │                     │
    │ • CompactionBegin   │       │                     │
    │ • 生成压缩消息      │       │                     │
    │   (带重试)          │       │                     │
    │ • 清空 context      │       │                     │
    │ • 新建 checkpoint   │       │                     │
    │ • append 压缩消息   │       │                     │
    │ • CompactionEnd     │       │                     │
    └──────────┬──────────┘       └─────────────────────┘
               │
               ▼
    ┌─────────────────────┐
    │ 继续 step (压缩后)   │
    └─────────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [B] BackToTheFuture 分支 —— D-Mail 回到未来                          │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────┐
                    │   _step() 执行中         │
                    └──────────┬──────────────┘
                               │
                               ▼
                    ┌─────────────────────────┐
                    │ DenwaRenji 有 pending   │
                    │ D-Mail?                 │
                    └──────────┬──────────────┘
                               │
              ┌────────────────┴────────────────┐
              │Yes                              │No
              ▼                                 ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ 🕒 抛出异常:        │       │ 正常继续            │
    │ BackToTheFuture     │       │                     │
    │ (checkpoint_id,     │       │                     │
    │  messages)          │       │                     │
    └──────────┬──────────┘       └─────────────────────┘
               │
               ▼
    ┌─────────────────────┐
    │ _agent_loop() 捕获   │
    │                     │
    │ 1. revert_to(       │ ──► ⏪ 回滚到历史点
    │      checkpoint_id) │
    │ 2. 新建 checkpoint  │ ──► 🆕 新的开始
    │ 3. append D-Mail    │ ──► 📨 注入"未来信息"
    │    + 系统提示       │
    │ 4. 继续 loop        │ ──► 🔄 改道执行
    └─────────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [C] Ralph 模式分支 —— 自动迭代决策                                    │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────┐
                    │    KimiSoul.run()       │
                    └──────────┬──────────────┘
                               │
                               ▼
                    ┌─────────────────────────┐
                    │ max_ralph_iterations    │
                    │ != 0?                   │
                    └──────────┬──────────────┘
                               │
              ┌────────────────┴────────────────┐
              │Yes                              │No
              ▼                                 ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ 🤖 Ralph 自动循环   │       │ 标准 _turn() 单轮   │
    │                     │       │                     │
    │ FlowRunner.         │       │                     │
    │   ralph_loop()      │       │                     │
    │                     │       │                     │
    │ 动态构造流程:       │       │                     │
    │ BEGIN               │       │                     │
    │   ↓                 │       │                     │
    │ R1: 执行任务        │       │                     │
    │   ↓                 │       │                     │
    │ R2: 决策节点        │       │                     │
    │   ├── CONTINUE ─────┼──► (继续迭代)              │
    │   └── STOP ─────────┼──► (结束)                  │
    │                     │       │                     │
    │ max_ralph=-1 时     │       │                     │
    │ 近似无限循环        │       │                     │
    └─────────────────────┘       └─────────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│ [D] 错误处理分支 —— 重试与中断                                        │
└──────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────┐
                    │   _step() 调用          │
                    │   kosong.step()         │
                    └──────────┬──────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
            ▼                  ▼                  ▼
   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
   │  网络/超时     │  │  HTTP 错误码   │  │  step 异常     │
   │  /空响应?      │  │  429/500/502/503│  │  (不可恢复)    │
   └───────┬────────┘  └───────┬────────┘  └───────┬────────┘
           │                   │                   │
           ▼                   ▼                   ▼
   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
   │ tenacity 重试  │  │ tenacity 重试  │  │ 发送           │
   │ 指数退避       │  │ 指数退避       │  │ StepInterrupted│
   │                │  │                │  │        ↓       │
   │ 成功后继续     │  │ 成功后继续     │  │ 终止当前 turn  │
   │ 正常 step      │  │ 正常 step      │  │ (异常上抛)     │
   └────────────────┘  └────────────────┘  └────────────────┘


图例: 📦 压缩  ⏪ 回滚  🕒 时间穿越  🤖 自动模式  📨 消息注入
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道 turn 内多 step 循环，无 tool calls 时结束）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道 compaction、D-Mail 回滚、Ralph 模式）。
- **10 分钟版**：通读 `3~9`（能定位 step 不继续、context 不增长等问题）。

### 2.1 一句话定义

Kimi CLI 的 Agent Loop 是“**可回滚的 step 循环**”：每次 step 包含 LLM 推理和工具执行；工具结果回注后继续 step；通过 checkpoint + revert 实现上下文回滚；直到无 tool calls 或达到 step 上限。

---

## 3. 在哪里开始

`KimiCLI.create()` 完成 runtime、agent、context 初始化后，创建 `KimiSoul` 实例。  
真正的“每次用户输入执行”从 `KimiSoul.run(user_input)` 开始。

`run()` 的入口行为：

- 刷新 OAuth token（避免长时间 idle 过期）
- 发送 `TurnBegin` 事件到 wire
- 识别是否为 slash command（例如 `/skill:*`、`/flow:*`）
- 如果启用了 `max_ralph_iterations`，走 Ralph 自动循环；否则走普通 `_turn()`
- 结束时发送 `TurnEnd`

---

## 4. Turn 与 Step 的关系

在 Kimi CLI 里：

- **Turn**：一次用户输入触发的一整轮执行（从 `TurnBegin` 到 `TurnEnd`）
- **Step**：Turn 内部的单次“LLM 推理 + 工具执行 + 上下文增长”

`_turn()` 的关键动作：

1. 校验 LLM 是否已配置、消息能力是否被模型支持
2. 创建 checkpoint（第一次会形成 checkpoint 0）
3. 把用户消息 append 到 context
4. 进入 `_agent_loop()`

---

## 5. `_agent_loop()` 主循环

`_agent_loop()` 是每个 turn 的核心 while-loop。它会不断执行 step，直到满足停止条件。

### 5.1 循环前置

- 若 toolset 是 `KimiToolset`，先等待 MCP tools 就绪（`wait_for_mcp_tools`）
- 启动审批转发协程 `_pipe_approval_to_wire()`：
  - 从 `Approval` 拉取请求
  - 转成 wire 层 `ApprovalRequest`
  - 等待 UI/调用方回复后写回 `Approval`
  - 再发送 `ApprovalResponse`

这层转发把“tool 执行审批”与“UI 展示审批”解耦。

### 5.2 每次 step 的执行框架

每轮循环都会：

1. `step_no += 1`，并检查是否超过 `max_steps_per_turn`
2. 发送 `StepBegin`
3. 启动审批转发 task
4. 在 try/finally 中调用 `_step()`
5. finally 内取消审批 task，避免泄漏

如果 `_step()` 抛异常：

- 发 `StepInterrupted`
- 终止当前 turn（异常上抛）

### 5.3 context 压缩触发

step 前会判断 token 预算：

- 条件：`context.token_count + reserved_context_size >= model.max_context_size`
- 满足时触发 `compact_context()`

`compact_context()` 行为：

1. 发 `CompactionBegin`
2. 调用 compaction 策略生成压缩后的消息（带重试）
3. 清空 context，建立新 checkpoint
4. append 压缩消息
5. 发 `CompactionEnd`

---

## 6. `_step()` 内部：一次完整 agent step

`_step()` 负责“向 LLM 发起一步 + 执行工具 + 更新上下文 + 决定是否继续”。

### 6.1 调用 kosong.step（含重试）

使用 tenacity 包装 `kosong.step(...)`，可重试错误包括：

- 网络/超时/空响应
- HTTP `429/500/502/503`

调用时会传入：

- `chat_provider`
- `system_prompt`
- `toolset`
- `context.history`
- `on_message_part=wire_send`
- `on_tool_result=wire_send`

因此 UI 可以流式看到模型输出和工具结果。

### 6.2 token 与状态更新

拿到 `StepResult` 后会：

- 发送 `StatusUpdate(token_usage, message_id)`
- 用 `usage.input` 更新 step 前 token 估算
- 计算并回填 context usage（占模型窗口比例）

### 6.3 收集工具结果并增长上下文

`await result.tool_results()` 会等待本 step 所有工具调用完成。  
随后 `_grow_context(result, results)`（受 `asyncio.shield` 保护）：

1. 将 assistant 消息 append 到 context
2. 用 `usage.total` 更新 token 计数
3. 将每个 tool result 转成 tool message 并 append 到 context

`shield` 的意义是：即使外层被打断，也尽量保证“上下文演进”不被中途中断，避免状态不一致。

### 6.4 step 停止条件

`_step()` 有 3 种结果：

- **返回 `None`**：本次有 tool calls，loop 继续下一 step
- **返回 `StepOutcome(no_tool_calls)`**：模型没有再发 tool call，turn 可结束
- **返回 `StepOutcome(tool_rejected)`**：工具被拒绝（审批拒绝等），turn 结束

---

## 7. D-Mail 与"回到未来"（BackToTheFuture）

在 `_step()` 里若 `DenwaRenji` 有 pending D-Mail，会抛出 `BackToTheFuture(checkpoint_id, messages)`。

`_agent_loop()` 捕获后执行：

1. `context.revert_to(checkpoint_id)`
2. 新建 checkpoint
3. append 注入消息（系统提示 + D-Mail 内容）

这相当于把上下文回滚到某历史点，再注入“来自未来的信息”，让后续策略改道。

---

## 8. Ralph 模式（自动循环）

当 `max_ralph_iterations != 0` 时，`run()` 不走普通单次 `_turn(user_message)`，而是：

1. 用 `FlowRunner.ralph_loop(user_message, max_ralph_iterations)` 动态构造流程
2. 运行 flow：`BEGIN -> R1(任务) -> R2(决策) -> CONTINUE/STOP`

其中 R2 会提示模型：

- 任务未完全完成就选 `CONTINUE`
- 仅在 100% 完成时选 `STOP`

`max_ralph_iterations = -1` 表示近似无限循环（实现上用超大上限），否则为“首轮 + N 次额外迭代”。

---

## 9. 一个简化时序

一次普通 turn 的主路径可概括为：

1. `run()` 收到用户输入并发 `TurnBegin`
2. `_turn()` 记录用户消息并进入 `_agent_loop()`
3. `_agent_loop()` 循环执行 `_step()`
4. `_step()` 执行 LLM + tool calls，更新 context
5. 若还有 tool calls，继续 step；否则结束 turn
6. `run()` 发 `TurnEnd`

---

## 10. 关键控制参数

这些参数直接影响 loop 行为：

- `max_steps_per_turn`：单个 turn 的 step 上限，防止失控循环
- `max_retries_per_step`：step 内部 LLM 调用重试次数
- `reserved_context_size`：预留上下文空间，触发 compaction 的阈值缓冲
- `max_ralph_iterations`：是否进入 Ralph 自动循环及循环次数

---

## 11. 总结

Kimi CLI 的 Agent loop 本质是一个“**可中断、可回滚、可扩展**”的 step 循环：

- 通过 checkpoint + revert 保证上下文可回退
- 通过 wire 事件把执行细节流式暴露给 UI
- 通过审批管道控制高风险工具调用
- 通过 compaction 管理长上下文
- 通过 Ralph/FlowRunner 支持自动化多轮推进

这套设计让它既能处理常规对话式 agent 任务，也能在工具链和长任务场景下保持可控性。