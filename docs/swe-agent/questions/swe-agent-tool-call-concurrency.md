# SWE-agent Tool Call Concurrency

## TL;DR（结论先行）

SWE-agent **未实现单 step 多 tool call 并发执行**；其 function-calling 解析器强制"每次响应必须且只能一个 tool call"。核心取舍是**简化执行模型**（对比 OpenCode 的并发派发）。

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

Tool Call 并发涉及两个层面：
- **单响应多调用**：一次 LLM 响应中包含多个 tool call
- **任务级并行**：多个独立任务并行执行

SWE-agent 的设计选择：
- 单响应单调用：简化执行逻辑，确保确定性
- 任务级并行：支持 batch run 的多 worker

### 1.2 核心挑战

| 挑战 | 单调用策略 | 多调用并发 |
|-----|-----------|-----------|
| 执行复杂度 | 简单，顺序执行 | 复杂，需要调度 |
| 结果确定性 | 高 | 取决于执行顺序 |
| 错误处理 | 简单 | 需要聚合多个错误 |
| 适用场景 | 软件工程任务 | 需要并行探索的场景 |

---

## 2. 整体架构

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop                                                   │
│ sweagent/agent/agents.py                                     │
└───────────────────────┬─────────────────────────────────────┘
                        │ 调用
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Tool Call Handling ▓▓▓                                  │
│ sweagent/tools/parsing.py                                    │
│ - FunctionCallingParser: 强制单调用                         │
│ - ThoughtActionParser: thought-action 分离                  │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Tool Execution                                               │
│ sweagent/agent/agents.py                                     │
│ - 顺序执行单个 action                                       │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `FunctionCallingParser` | 解析并校验 tool_calls 数量为 1 | `sweagent/tools/parsing.py` |
| `StepOutput` | 存储单步的 tool_calls/tool_call_ids | `sweagent/types.py` |
| `handle_action()` | 执行单个 action | `sweagent/agent/agents.py` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant B as FunctionCallingParser
    participant C as StepOutput
    participant D as handle_action

    A->>B: 1. 传入 model_response
    B->>B: 2. 提取 tool_calls
    B->>B: 3. 校验 len(tool_calls) == 1
    alt 校验通过
        B-->>A: 4a. 返回 tool_calls[0]
        A->>D: 5a. 执行 action
        D-->>A: 6a. 返回 observation
        A->>C: 7a. 写入 StepOutput
    else 校验失败
        B--xA: 4b. 抛出 FunctionCallingFormatError
    end
```

---

## 3. 核心组件详细分析

### 3.1 FunctionCallingParser

#### 职责定位

强制每次模型响应只能包含一个 tool call，否则抛出异常。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[model_response] --> B[提取 tool_calls]
    B --> C{tool_calls is None?}
    C -->|是| D[抛出 missing 错误]
    C -->|否| E{len == 1?}
    E -->|否| F{len > 1?}
    F -->|是| G[抛出 multiple 错误]
    F -->|否| H[抛出 missing 错误]
    E -->|是| I[返回 tool_calls[0]]

    style D fill:#FFB6C1
    style G fill:#FFB6C1
    style H fill:#FFB6C1
    style I fill:#90EE90
```

---

### 3.2 StepOutput 数据结构

#### 职责定位

存储单步的输出，支持 tool_calls 列表但实际只存储一个。

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  StepOutput                                                  │
│  ├── thought: str              # 推理内容                   │
│  ├── action: str               # 执行的动作                 │
│  ├── observation: str          # 观察结果                   │
│  ├── done: bool                # 是否完成                   │
│  ├── tool_calls: list[dict]    # 工具调用列表（实际长度=1） │
│  └── tool_call_ids: list[str]  # 工具调用 ID 列表           │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 端到端数据流转

### 4.1 正常流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as FunctionCallingParser
    participant C as handle_action
    participant D as Environment

    A->>B: 传入 model_response
    B->>B: 解析 tool_calls
    B->>B: 校验 len == 1
    B-->>A: 返回 tool_call
    A->>C: 调用 handle_action
    C->>D: 执行命令
    D-->>C: 返回结果
    C-->>A: 返回 observation
    A->>A: 进入下一 step
```

### 4.2 异常流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as FunctionCallingParser
    participant C as forward_with_handling

    A->>B: 传入 model_response
    B->>B: 解析 tool_calls
    B->>B: 发现 len > 1
    B--xA: 抛出 FunctionCallingFormatError
    A->>C: 错误处理
    C->>C: 构造错误反馈
    C-->>A: 返回重试历史
    A->>A: 重新查询 LLM
```

### 4.3 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[model_response] --> I2[提取 tool_calls]
    end

    subgraph Process["处理阶段"]
        P1[校验数量] --> P2{len == 1?}
        P2 -->|是| P3[解析为 action]
        P2 -->|否| P4[抛出错误]
    end

    subgraph Output["输出阶段"]
        O1[执行 action] --> O2[StepOutput]
    end

    I2 --> P1
    P3 --> O1
    P4 --> O3[错误处理]

    style Process fill:#e1f5e1,stroke:#333
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# sweagent/types.py
class StepOutput(BaseModel):
    thought: str = ""
    action: str = ""
    observation: str = ""
    done: bool = False
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_ids: list[str] | None = None
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `tool_calls` | `list[dict]` | 工具调用列表（实际只存一个） |
| `tool_call_ids` | `list[str]` | 工具调用 ID 列表 |

### 5.2 主链路代码

```python
# sweagent/tools/parsing.py
class FunctionCallingParser(AbstractParseFunction, BaseModel):
    def __call__(self, model_response: dict[str, Any]) -> tuple[str, str]:
        """解析模型响应，强制要求恰好一个 tool call"""
        tool_calls = model_response.get("tool_calls", None)

        if tool_calls is None or len(tool_calls) != 1:
            raise FunctionCallingFormatError(
                message="Expected exactly one tool call",
                error_code="multiple" if tool_calls and len(tool_calls) > 1 else "missing"
            )

        tool_call = tool_calls[0]
        # 解析为 action...
        return thought, action
```

**代码要点**：

1. **强制单调用**：`len(tool_calls) != 1` 时抛出异常
2. **错误分类**：区分 missing 和 multiple 两种情况
3. **简单直接**：无并发调度逻辑

### 5.3 关键调用链

```text
Agent.step()                         [sweagent/agent/agents.py:200]
  -> forward_with_handling()         [sweagent/agent/agents.py:1062]
    -> forward()                     [sweagent/agent/agents.py:1018]
      -> parse_response()            [sweagent/agent/agents.py:850]
        -> FunctionCallingParser()   [sweagent/tools/parsing.py:100]
          - 校验 len(tool_calls) == 1
          - 返回 tool_calls[0]
    -> handle_action()               [sweagent/agent/agents.py:900]
      - 执行单个 action
```

---

## 6. 设计意图与 Trade-off

### 6.1 SWE-agent 的选择

| 维度 | SWE-agent 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 调用模式 | 单调用 | 多调用并发 | 简化逻辑，确保确定性 |
| 执行顺序 | 顺序执行 | 并行执行 | 易于调试，结果可预测 |
| 错误处理 | 单错误 | 错误聚合 | 简单直接 |
| 适用任务 | 软件工程 | 探索性任务 | 专注代码修复场景 |

### 6.2 为什么这样设计？

**核心问题**：软件工程任务是否需要 tool call 并发？

**SWE-agent 的解决方案**：
- 代码依据：`sweagent/tools/parsing.py`
- 设计意图：简化执行模型，专注确定性
- 带来的好处：
  - 执行逻辑简单，易于调试
  - 结果确定，便于复现
  - 错误处理直接
- 付出的代价：
  - 无法并行探索多个方案
  - 某些场景效率较低

### 6.3 与其他项目的对比

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| SWE-agent | 强制单调用 | 软件工程，确定性优先 |
| OpenCode | 并发派发、顺序收集 | 长任务，需要并行执行 |
| Kimi CLI | 并发触发、顺序收集 | 复杂任务，多工具并行 |
| Gemini CLI | 顺序执行 | 复杂状态机，精细控制 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 多 tool call | len(tool_calls) > 1 | `sweagent/tools/parsing.py` |
| 无 tool call | tool_calls is None | `sweagent/tools/parsing.py` |
| 解析失败 | 参数格式错误 | `sweagent/tools/parsing.py` |

### 7.2 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| missing | 提示模型必须提供 tool call | `sweagent/tools/parsing.py` |
| multiple | 提示模型每次只能一个 tool call | `sweagent/tools/parsing.py` |
| incorrect_args | 提示参数格式错误 | `sweagent/tools/parsing.py` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 解析器 | `sweagent/tools/parsing.py` | - | FunctionCallingParser 校验逻辑 |
| 数据结构 | `sweagent/types.py` | - | StepOutput.tool_calls/tool_call_ids |
| Agent 循环 | `sweagent/agent/agents.py` | - | query -> parse -> execute action |

---

## 9. 延伸阅读

- 前置知识：`docs/swe-agent/04-swe-agent-agent-loop.md`（Agent 循环中的 tool 调用流程）
- 相关机制：`docs/swe-agent/05-swe-agent-tools-system.md`（工具系统详细分析）
- 对比分析：`docs/opencode/questions/opencode-tool-call-concurrency.md`（OpenCode 并发实现）

---

*✅ Verified: 基于 sweagent/tools/parsing.py、sweagent/types.py 等源码分析*
*基于版本：SWE-agent (baseline 2026-02-08) | 最后更新：2026-02-24*
