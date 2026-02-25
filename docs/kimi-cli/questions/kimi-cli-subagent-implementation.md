# Kimi CLI Subagent 实现分析

## TL;DR（结论先行）

一句话定义：Kimi CLI 的 Subagent 是一种**基于 Task 工具的上下文隔离型子代理机制**，允许主代理通过 `Task` 工具动态创建和调用子代理，实现任务并行化和上下文隔离。

Kimi CLI 的核心取舍：**同步顺序执行 + 独立上下文隔离**（对比 OpenCode 的并发子代理、Codex 的并行任务队列）

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

没有 Subagent 机制时：
- 主代理处理复杂任务时，上下文会被中间步骤（如搜索、调试、代码修复）污染
- 用户无法看到子任务的执行过程，只能看到最终结果
- 多个独立子任务只能串行执行，效率低下

有 Subagent 机制时：
- 主代理可以委托子任务给子代理，保持主上下文干净
- 子代理在独立上下文中执行，失败不影响主代理
- 多个子代理可以并行启动（通过单次响应中的多个 Task 工具调用）

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 上下文隔离 | 子任务的历史记录污染主上下文，导致 token 超限或注意力分散 |
| 状态同步 | 子代理的状态变化无法正确反映到主代理的 UI 上 |
| 生命周期管理 | 子代理创建后无法正确清理，导致资源泄漏 |
| 并行执行 | 多个子任务只能串行执行，效率低下 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI 入口 / Session Runtime                                   │
│ kimi-cli/src/kimi_cli/cli/__init__.py                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ 加载 Agent
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Agent 加载器                                                 │
│ kimi-cli/src/kimi_cli/soul/agent.py:load_agent()            │
│ - 解析 agent.yaml 中的 subagents 配置                        │
│ - 创建 LaborMarket 管理子代理                                │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ 主代理 (Main) │ │ 固定子代理    │ │ 动态子代理    │
│              │ │ (Fixed)       │ │ (Dynamic)    │
│ - Task 工具   │ │ - coder      │ │ - 运行时创建  │
│ - CreateSub  │ │ - 预定义配置  │ │ - 用户自定义  │
└──────┬───────┘ └──────────────┘ └──────────────┘
       │
       │ 调用 Task 工具
       ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Task 工具执行 ▓▓▓                                        │
│ kimi-cli/src/kimi_cli/tools/multiagent/task.py              │
│ - 查找子代理                                                 │
│ - 创建独立 Context                                          │
│ - 运行 KimiSoul                                             │
│ - 收集结果                                                   │
└───────────────────────┬─────────────────────────────────────┘
                        │ SubagentEvent
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Wire 协议层                                                  │
│ kimi-cli/src/kimi_cli/wire/types.py:SubagentEvent           │
│ - 包装子代理事件                                             │
│ - 透传到 UI 层                                               │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Web UI (React)                                              │
│ web/src/components/ai-elements/subagent-steps.tsx           │
│ - 渲染子代理执行步骤                                         │
│ - 显示工具调用状态                                           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `LaborMarket` | 管理所有子代理的注册和查找 | `kimi-cli/src/kimi_cli/soul/agent.py:168` |
| `Task` | 工具类，负责执行子代理任务 | `kimi-cli/src/kimi_cli/tools/multiagent/task.py:52` |
| `CreateSubagent` | 工具类，动态创建子代理 | `kimi-cli/src/kimi_cli/tools/multiagent/create.py:23` |
| `SubagentEvent` | Wire 事件类型，包装子代理事件 | `kimi-cli/src/kimi_cli/wire/types.py:105` |
| `KimiSoul` | 子代理的执行引擎 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:89` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant M as 主代理
    participant T as Task工具
    participant L as LaborMarket
    participant S as 子代理Soul
    participant W as Wire
    participant UI as Web UI

    M->>T: 1. 调用 Task(description, subagent_name, prompt)
    T->>L: 2. 查找子代理配置
    L-->>T: 3. 返回 Agent 实例
    T->>T: 4. 创建独立 Context 文件
    T->>S: 5. 实例化 KimiSoul
    S->>W: 6. 发送 SubagentEvent(StepBegin)
    W->>UI: 7. 透传事件到前端
    loop 子代理执行循环
        S->>S: 8. 执行 _agent_loop()
        S->>W: 9. 发送工具调用事件
        W->>UI: 10. 显示子代理工具调用
    end
    S-->>T: 11. 返回最终结果
    T-->>M: 12. 返回 ToolOk(output)
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | 主代理通过 Task 工具发起调用 | 将子代理调用统一为工具调用语义 |
| 4 | 创建独立 Context 文件 | 实现上下文隔离，子代理无法访问主上下文 |
| 6-7 | 通过 SubagentEvent 透传事件 | 保持用户体验一致性，子代理执行可见 |
| 11-12 | 仅返回最终文本结果 | 子代理的中间步骤不污染主上下文 |

---

## 3. 核心组件详细分析

### 3.1 LaborMarket 子代理管理器

#### 职责定位

`LaborMarket` 是子代理的注册中心，管理两种类型的子代理：
- **固定子代理 (Fixed)**：通过 `agent.yaml` 配置预定义
- **动态子代理 (Dynamic)**：运行时通过 `CreateSubagent` 工具创建

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  LaborMarket                                                │
│  kimi-cli/src/kimi_cli/soul/agent.py:168                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐    ┌─────────────────┐                │
│  │ fixed_subagents │    │ dynamic_subagents│               │
│  │  (预定义配置)    │    │  (运行时创建)    │               │
│  ├─────────────────┤    ├─────────────────┤                │
│  │ coder: Agent    │    │ custom_1: Agent │                │
│  │ ...             │    │ custom_2: Agent │                │
│  └────────┬────────┘    └────────┬────────┘                │
│           │                      │                          │
│           └──────────┬───────────┘                          │
│                      ▼                                      │
│              ┌───────────────┐                              │
│              │ subagents()   │                              │
│              │ (合并视图)     │                              │
│              └───────────────┘                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `add_fixed_subagent()` | name, agent, description | - | 注册固定子代理 | `agent.py:179` |
| `add_dynamic_subagent()` | name, agent | - | 注册动态子代理 | `agent.py:184` |
| `subagents` | - | Mapping[str, Agent] | 获取所有子代理 | `agent.py:175` |

---

### 3.2 Task 工具执行器

#### 职责定位

`Task` 是调用子代理的入口工具，负责：
1. 查找指定的子代理
2. 创建隔离的执行上下文
3. 运行子代理并收集结果
4. 将子代理事件透传给主 Wire

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Validating: 接收调用
    Validating --> Running: 子代理存在
    Validating --> Error: 子代理不存在
    Running --> CheckingResult: 执行完成
    CheckingResult --> Continuing: 结果过短(<200字符)
    CheckingResult --> Success: 结果完整
    Continuing --> CheckingResult: 重新生成
    Continuing --> Error: 续写失败
    Error --> [*]: 返回 ToolError
    Success --> [*]: 返回 ToolOk
```

#### 核心执行流程

```mermaid
flowchart TD
    A[开始] --> B{查找子代理}
    B -->|不存在| C[返回 ToolError]
    B -->|存在| D[创建独立 Context]
    D --> E[实例化 KimiSoul]
    E --> F[定义 _super_wire_send]
    F --> G[运行子代理]
    G --> H{执行结果?}
    H -->|MaxStepsReached| I[返回错误]
    H -->|正常完成| J{结果长度<200?}
    J -->|是| K[续写提示词]
    K --> L[再次运行]
    L --> M[返回最终结果]
    J -->|否| M
    C --> N[结束]
    I --> N
    M --> N
```

#### 关键代码实现

```python
# kimi-cli/src/kimi_cli/tools/multiagent/task.py:101-162
async def _run_subagent(self, agent: Agent, prompt: str) -> ToolReturnValue:
    """Run subagent with optional continuation for task summary."""
    super_wire = get_wire_or_none()
    current_tool_call = get_current_tool_call_or_none()
    current_tool_call_id = current_tool_call.id

    def _super_wire_send(msg: WireMessage) -> None:
        # 关键：将子代理事件包装为 SubagentEvent
        if isinstance(msg, ApprovalRequest | ApprovalResponse | ToolCallRequest):
            super_wire.soul_side.send(msg)  # 审批请求直接透传
            return
        event = SubagentEvent(
            task_tool_call_id=current_tool_call_id,
            event=msg,
        )
        super_wire.soul_side.send(event)

    # 创建独立的 Context 文件
    subagent_context_file = await self._get_subagent_context_file()
    context = Context(file_backend=subagent_context_file)
    soul = KimiSoul(agent, context=context)

    # 运行子代理
    await run_soul(soul, prompt, _ui_loop_fn, asyncio.Event())

    # 检查结果并可能续写
    final_response = context.history[-1].extract_text(sep="\n")
    if len(final_response) < 200:
        await run_soul(soul, CONTINUE_PROMPT, _ui_loop_fn, asyncio.Event())

    return ToolOk(output=final_response)
```

**代码要点**：
1. **事件包装**：所有子代理事件通过 `SubagentEvent` 包装，携带 `task_tool_call_id` 用于前端关联
2. **审批透传**：`ApprovalRequest` 等请求直接透传到主 Wire，避免嵌套审批
3. **上下文隔离**：子代理使用独立的 `Context` 文件，与主代理完全隔离
4. **自动续写**：结果过短（<200字符）时自动触发续写，确保返回完整信息

---

### 3.3 CreateSubagent 动态子代理创建器

#### 职责定位

允许主代理在运行时动态创建自定义子代理，用于：
- 定义特定角色的代理（如 "code_reviewer", "test_writer"）
- 复用相同的工具集但使用不同的 system prompt

#### 关键代码实现

```python
# kimi-cli/src/kimi_cli/tools/multiagent/create.py:33-50
async def __call__(self, params: Params) -> ToolReturnValue:
    if params.name in self._runtime.labor_market.subagents:
        return ToolError(message=f"Subagent with name '{params.name}' already exists.")

    subagent = Agent(
        name=params.name,
        system_prompt=params.system_prompt,
        toolset=self._toolset,  # 共享父代理的工具集
        runtime=self._runtime.copy_for_dynamic_subagent(),
    )
    self._runtime.labor_market.add_dynamic_subagent(params.name, subagent)
    return ToolOk(output="Available subagents: " + ", ".join(...))
```

**关键设计**：
- **工具集共享**：动态子代理共享父代理的 `toolset`，确保工具一致性
- **Runtime 复制**：使用 `copy_for_dynamic_subagent()` 创建独立的 Runtime 实例
- **LaborMarket 共享**：动态子代理共享父代理的 LaborMarket，可以访问其他动态子代理

---

### 3.4 Runtime 复制策略

```mermaid
flowchart TD
    A[父代理 Runtime] --> B{复制类型?}
    B -->|固定子代理| C[copy_for_fixed_subagent]
    B -->|动态子代理| D[copy_for_dynamic_subagent]

    C --> E[新的 DenwaRenji]
    C --> F[新的 LaborMarket]
    C --> G[共享 Approval]

    D --> H[新的 DenwaRenji]
    D --> I[共享 LaborMarket]
    D --> J[共享 Approval]

    E --> K[固定子代理 Runtime]
    F --> K
    G --> K

    H --> L[动态子代理 Runtime]
    I --> L
    J --> L
```

**设计意图**：
- **固定子代理**：完全隔离，有自己的 LaborMarket（不能访问其他子代理）
- **动态子代理**：可以访问其他动态子代理（因为共享 LaborMarket）
- **Approval 共享**：所有子代理共享父代理的审批状态（YOLO 模式等）

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant U as 用户
    participant M as 主代理
    participant T as Task工具
    participant S as 子代理
    participant W as Wire
    participant UI as Web界面

    U->>M: "分析这两个文件"
    M->>M: 决定创建两个子代理任务

    par 并行执行子代理1
        M->>T: Task(subagent="coder", prompt="分析文件A")
        T->>S: 创建并运行子代理1
        loop 子代理1执行
            S->>W: SubagentEvent(tool_call)
            W->>UI: 显示工具调用
        end
        S-->>T: 返回结果1
        T-->>M: ToolOk(结果1)
    and 并行执行子代理2
        M->>T: Task(subagent="coder", prompt="分析文件B")
        T->>S: 创建并运行子代理2
        loop 子代理2执行
            S->>W: SubagentEvent(tool_call)
            W->>UI: 显示工具调用
        end
        S-->>T: 返回结果2
        T-->>M: ToolOk(结果2)
    end

    M->>M: 整合两个结果
    M-->>U: 返回综合分析
```

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[用户请求] --> I2[主代理分析]
        I2 --> I3[生成 Task 调用]
    end

    subgraph Process["子代理执行阶段"]
        P1[查找子代理] --> P2[创建独立Context]
        P2 --> P3[实例化KimiSoul]
        P3 --> P4[执行_agent_loop]
        P4 --> P5[收集工具结果]
    end

    subgraph Output["结果返回阶段"]
        O1[提取最终消息] --> O2[可选续写]
        O2 --> O3[返回ToolOk]
    end

    I3 --> P1
    P5 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[Task工具调用] --> B{子代理存在?}
    B -->|否| C[返回 ToolError: Subagent not found]
    B -->|是| D[执行子代理]

    D --> E{执行异常?}
    E -->|MaxStepsReached| F[返回错误: 建议拆分任务]
    E -->|其他异常| G[返回错误: Failed to run subagent]
    E -->|正常| H{结果有效?}

    H -->|历史为空| I[返回错误: 子代理未正确运行]
    H -->|最后消息非assistant| I
    H -->|有效| J[返回结果]

    C --> K[结束]
    F --> K
    G --> K
    I --> K
    J --> K
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# kimi-cli/src/kimi_cli/soul/agent.py:168-186
class LaborMarket:
    def __init__(self):
        self.fixed_subagents: dict[str, Agent] = {}
        self.fixed_subagent_descs: dict[str, str] = {}
        self.dynamic_subagents: dict[str, Agent] = {}

    @property
    def subagents(self) -> Mapping[str, Agent]:
        """Get all subagents in the labor market."""
        return {**self.fixed_subagents, **self.dynamic_subagents}
```

**字段说明**：
| 字段 | 类型 | 用途 |
|-----|------|------|
| `fixed_subagents` | `dict[str, Agent]` | 预定义的固定子代理（如 coder） |
| `fixed_subagent_descs` | `dict[str, str]` | 固定子代理的描述信息，用于 Task 工具提示 |
| `dynamic_subagents` | `dict[str, Agent]` | 运行时动态创建的子代理 |

### 5.2 SubagentEvent 定义

```python
# kimi-cli/src/kimi_cli/wire/types.py:105-139
class SubagentEvent(BaseModel):
    """
    An event from a subagent.
    """
    task_tool_call_id: str
    """The ID of the task tool call associated with this subagent."""
    event: Event
    """The event from the subagent."""
```

### 5.3 关键调用链

```text
Task.__call__()                    [kimi-cli/src/kimi_cli/tools/multiagent/task.py:83]
  -> _run_subagent()               [task.py:101]
    -> get_wire_or_none()          [kimi-cli/src/kimi_cli/soul/__init__.py:189]
    -> _get_subagent_context_file() [task.py:71]
      -> next_available_rotation() [kimi-cli/src/kimi_cli/utils/path.py]
    -> Context()                   [kimi-cli/src/kimi_cli/soul/context.py]
    -> KimiSoul()                  [kimi-cli/src/kimi_cli/soul/kimisoul.py:89]
    -> run_soul()                  [kimi-cli/src/kimi_cli/soul/__init__.py:121]
      - 创建 Wire
      - 启动 UI loop
      - 执行 soul.run()
```

---

## 6. 设计意图与 Trade-off

### 6.1 Kimi CLI 的选择

| 维度 | Kimi CLI 的选择 | 替代方案 | 取舍分析 |
|-----|----------------|---------|---------|
| 执行模式 | 同步顺序执行 | 完全并行异步 | 简单可控，避免并发冲突；但无法利用真正的并行计算 |
| 上下文隔离 | 独立 Context 文件 | 共享上下文 | 彻底隔离，子代理失败不影响主代理；但无法自动共享上下文 |
| 子代理类型 | 固定 + 动态 | 仅预定义 | 灵活性高，可运行时创建；但需要管理生命周期 |
| 事件传递 | SubagentEvent 包装 | 直接透传 | 前端可区分来源，支持嵌套；但增加一层包装开销 |
| 结果处理 | 仅返回最终文本 | 返回完整历史 | 主上下文干净；但丢失中间思考过程 |

### 6.2 为什么这样设计？

**核心问题**：如何在保持主代理上下文干净的同时，让用户看到子代理的执行过程？

**Kimi CLI 的解决方案**：
- **代码依据**：`kimi-cli/src/kimi_cli/tools/multiagent/task.py:109-119`
- **设计意图**：通过 `_super_wire_send` 函数将子代理事件包装为 `SubagentEvent`，既保持了上下文隔离（主代理只收到最终结果），又通过 Wire 协议让用户看到执行过程
- **带来的好处**：
  - 主代理上下文不会被污染
  - 用户可以在 UI 上看到子代理的执行步骤
  - 支持嵌套子代理（子代理可以再调用 Task）
- **付出的代价**：
  - 子代理的中间思考过程不会传递给主代理
  - 需要额外的 Context 文件管理

### 6.3 与其他项目的对比

```mermaid
flowchart LR
    subgraph "上下文隔离机制"
        K1[Kimi CLI<br/>独立 Context 文件]:::kimi
        O1[OpenCode<br/>隔离进程 + 共享内存]:::opencode
        C1[Codex<br/>无内置子代理]:::codex
        G1[Gemini CLI<br/>无子代理机制]:::gemini
        S1[SWE-agent<br/>无子代理机制]:::swe
    end

    subgraph "并行执行策略"
        K2[顺序执行<br/>单次响应多 Task]:::kimi
        O2[并发子代理<br/>独立进程并行]:::opencode
        C2[并行任务队列<br/>异步执行]:::codex
        G2[单代理模式]:::gemini
        S2[单代理模式]:::swe
    end

    subgraph "子代理创建方式"
        K3[固定 + 动态<br/>Task 工具创建]:::kimi
        O3[动态创建<br/>配置驱动]:::opencode
        C3[外部工具调用]:::codex
        G3[无]:::gemini
        S3[无]:::swe
    end

    classDef kimi fill:#e1f5e1,stroke:#333
    classDef opencode fill:#fff2cc,stroke:#333
    classDef codex fill:#dae8fc,stroke:#333
    classDef gemini fill:#f5f5f5,stroke:#333
    classDef swe fill:#ffe6cc,stroke:#333
```

| 项目 | 核心差异 | 上下文隔离机制 | 并行执行策略 | 适用场景 |
|-----|---------|---------------|-------------|---------|
| **Kimi CLI** | Task 工具 + 独立 Context | 独立 Context 文件，完全隔离 | 同步顺序执行，单次响应可包含多个 Task 调用 | 需要上下文隔离的复杂任务分解，子代理执行过程可见 |
| **OpenCode** | 并发子代理 + 共享内存 | 隔离进程但共享内存空间 | 真正的并发执行，多个子代理同时运行 | 需要真正并行执行的高性能场景，如同时分析多个文件 |
| **Codex** | 无内置子代理机制 | 依赖外部工具实现 | 有并行任务队列，但非子代理机制 | 简单任务，不需要任务分解，依赖外部工具扩展 |
| **Gemini CLI** | 无子代理机制 | 单代理上下文 | 单代理顺序执行 | 单代理模式，依赖大上下文窗口处理复杂任务 |
| **SWE-agent** | 无子代理机制 | 单代理上下文 | 单代理顺序执行，有错误重试机制 | 专注软件工程任务，通过工具调用而非子代理实现功能分解 |

**详细对比分析**：

| 对比维度 | Kimi CLI | OpenCode | Codex | Gemini CLI | SWE-agent |
|---------|----------|----------|-------|-----------|-----------|
| **子代理实现方式** | Task 工具调用 | 内置并发子代理 | 无内置机制 | 无 | 无 |
| **上下文隔离级别** | 文件级完全隔离 | 进程级隔离 | - | - | - |
| **并行能力** | 伪并行（LLM 单次生成多个 Task） | 真并行（多进程） | 任务级并行 | 无 | 无 |
| **动态创建** | 支持（CreateSubagent 工具） | 支持 | - | - | - |
| **生命周期管理** | LaborMarket 统一管理 | 进程管理 | - | - | - |
| **事件可见性** | SubagentEvent 透传 | 独立输出流 | - | - | - |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 子代理不存在 | `params.subagent_name not in subagents` | `task.py:86-90` |
| 执行异常 | `run_soul()` 抛出异常 | `task.py:95-99` |
| 步骤超限 | `MaxStepsReached` 异常 | `task.py:133-140` |
| 结果无效 | 历史为空或最后消息非 assistant | `task.py:147-148` |
| 结果过短 | 长度 < 200 字符，触发续写 | `task.py:153-159` |

### 7.2 超时/资源限制

```python
# kimi-cli/src/kimi_cli/tools/multiagent/task.py:25
MAX_CONTINUE_ATTEMPTS = 1  # 最大续写尝试次数
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 子代理不存在 | 返回 ToolError，提示可用子代理 | `task.py:87-90` |
| MaxStepsReached | 返回详细错误，建议拆分任务 | `task.py:134-140` |
| 执行异常 | 包装为 ToolError 返回 | `task.py:95-99` |
| 结果无效 | 返回通用错误提示 | `task.py:142-148` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| LaborMarket 定义 | `kimi-cli/src/kimi_cli/soul/agent.py` | 168-186 | 子代理注册中心 |
| Task 工具 | `kimi-cli/src/kimi_cli/tools/multiagent/task.py` | 52-162 | 子代理执行入口 |
| CreateSubagent 工具 | `kimi-cli/src/kimi_cli/tools/multiagent/create.py` | 23-50 | 动态子代理创建 |
| SubagentEvent 定义 | `kimi-cli/src/kimi_cli/wire/types.py` | 105-139 | 子代理事件包装 |
| Runtime 复制 | `kimi-cli/src/kimi_cli/soul/agent.py` | 126-154 | 固定/动态子代理 Runtime 创建 |
| 子代理加载 | `kimi-cli/src/kimi_cli/soul/agent.py` | 217-224 | agent.yaml 中子代理解析 |
| SubagentSpec 定义 | `kimi-cli/src/kimi_cli/agentspec.py` | 51-55 | 子代理配置规范 |
| Web UI 渲染 | `kimi-cli/web/src/components/ai-elements/subagent-steps.tsx` | 1-223 | 子代理步骤可视化 |

---

## 9. 延伸阅读

- 前置知识：`docs/kimi-cli/04-kimi-cli-agent-loop.md` - Agent Loop 详细分析
- 相关机制：`docs/kimi-cli/07-kimi-cli-memory-context.md` - Context 和 Checkpoint 机制
- 深度分析：`docs/kimi-cli/questions/kimi-cli-wire-protocol.md` - Wire 协议详解

---

*基于 kimi-cli/src/kimi_cli/soul/agent.py、kimi-cli/src/kimi_cli/tools/multiagent/task.py 等源码分析*
*基于版本：kimi-cli 2026-02-08 | 最后更新：2026-02-25*
