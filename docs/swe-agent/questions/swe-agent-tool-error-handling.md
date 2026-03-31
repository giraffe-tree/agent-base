# SWE-agent Tool Error Handling

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 20-25 分钟 |
> | 前置文档 | `docs/swe-agent/04-swe-agent-agent-loop.md`、`docs/swe-agent/questions/swe-agent-tool-call-concurrency.md` |
> | 文档结构 | TL;DR → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

SWE-agent 采用**模板化错误反馈 + forward_with_handling() 集中处理 + Autosubmit 自动提交**的架构，通过 `max_requeries=3` 限制格式错误重试次数，并实现 `attempt_autosubmission_after_error()` 在异常情况下自动提取 patch，确保在 CI/CD 场景下的高完成率。

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 错误处理 | 集中式 forward_with_handling() | `sweagent/agent/agents.py:1062` |
| 重试策略 | max_requeries=3 限制 | `sweagent/agent/agents.py:339` |
| 异常恢复 | Autosubmit 自动提交 patch | `sweagent/agent/agents.py:823` |
| 错误反馈 | Jinja2 模板化反馈 | `sweagent/agent/agents.py:TemplateConfig` |

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

没有统一错误处理时：
- LLM 输出格式错误导致整个任务失败
- 环境异常时丢失已做的工作
- 错误信息不清晰，难以自我纠正

有了集中错误处理：
- 格式错误自动重试，提高成功率
- 异常时自动提交 patch，保留工作成果
- 模板化反馈帮助 LLM 理解并修正错误

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 格式错误恢复 | 单次格式错误导致任务失败 |
| 环境异常处理 | 环境崩溃时丢失所有进度 |
| 成本控制 | 无限重试导致成本失控 |
| 错误信息质量 | LLM 无法理解错误原因 |

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
│ ▓▓▓ Error Handling ▓▓▓                                      │
│ sweagent/agent/agents.py:1062                                │
│ - forward_with_handling(): 集中错误处理                     │
│ - handle_error_with_retry(): 重试逻辑                       │
│ - attempt_autosubmission_after_error(): 自动提交            │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Exception    │ │ Template     │ │ Submission   │
│ Types        │ │ Rendering    │ │ Handler      │
│ 异常类型定义  │ │ 模板渲染     │ │ 提交处理     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `forward_with_handling()` | 集中错误处理和重试 | `sweagent/agent/agents.py:1062` |
| `handle_error_with_retry()` | 构造重试历史记录 | `sweagent/agent/agents.py:1129` |
| `attempt_autosubmission_after_error()` | 异常时自动提交 | `sweagent/agent/agents.py:823` |
| `TemplateConfig` | 错误模板配置 | `sweagent/agent/agents.py:TemplateConfig` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant B as forward_with_handling
    participant C as Error Classifier
    participant D as Retry Handler
    participant E as Autosubmit

    A->>B: 1. 调用 forward
    B->>B: 2. 执行模型调用
    B->>C: 3. 捕获异常
    C-->>B: 4. 返回错误类型

    alt 可重试错误
        B->>D: 5a. 请求重试
        D->>D: 6a. 构造错误历史
        D-->>B: 7a. 返回新历史
        B->>B: 8a. 递归重试
    else 致命错误
        B->>E: 5b. 请求自动提交
        E->>E: 6b. 提取 patch
        E-->>B: 7b. 返回提交结果
    end

    B-->>A: 9. 返回最终结果
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent Loop 调用 forward | 统一入口，便于错误拦截 |
| 2-3 | 执行并捕获异常 | 集中处理所有错误 |
| 4 | 分类错误类型 | 区分可重试与致命错误 |
| 5a-8a | 可重试错误处理 | 模板反馈 + 递归重试 |
| 5b-7b | 致命错误处理 | 自动提交保留成果 |

---

## 3. 核心组件详细分析

### 3.1 错误类型体系

#### 职责定位

SWE-agent 将错误分为三类：业务异常、控制流异常、环境异常。

#### 错误类型层级图

```text
┌─────────────────────────────────────────────────────────────────┐
│                    SWE-agent 错误类型体系                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  业务异常（外部可见）                                              │
│  ├─ FormatError                                                 │
│  │   └─ FunctionCallingFormatError                              │
│  ├─ ContextWindowExceededError                                  │
│  ├─ CostLimitExceededError                                      │
│  │   ├─ InstanceCostLimitExceededError                          │
│  │   ├─ TotalCostLimitExceededError                             │
│  │   └─ InstanceCallLimitExceededError                          │
│  └─ ContentPolicyViolationError                                 │
│                                                                 │
│  控制流异常（内部使用）                                            │
│  ├─ _BlockedActionError                                         │
│  ├─ _RetryWithOutput                                            │
│  ├─ _RetryWithoutOutput                                         │
│  ├─ _ExitForfeit                                                │
│  └─ _TotalExecutionTimeExceeded                                 │
│                                                                 │
│  环境异常（来自 swerex）                                           │
│  ├─ BashIncorrectSyntaxError                                    │
│  ├─ CommandTimeoutError                                         │
│  └─ SwerexException                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Executing: 开始执行
    Executing --> Success: 无异常
    Executing --> RetryableError: FormatError/BlockedAction/BashSyntax
    Executing --> FatalError: ContextWindow/CostLimit/Timeout

    RetryableError --> CheckRetryCount: 增加计数
    CheckRetryCount --> Executing: 计数 < 3
    CheckRetryCount --> FatalError: 计数 >= 3

    FatalError --> Autosubmit: 尝试自动提交
    Autosubmit --> [*]: 返回结果
    Success --> [*]: 返回 StepOutput
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Executing | 执行模型调用 | 开始 forward | 完成或异常 |
| Success | 执行成功 | 无异常 | 返回结果 |
| RetryableError | 可重试错误 | FormatError 等 | 检查重试次数 |
| CheckRetryCount | 检查重试次数 | 可重试错误 | 决定重试或终止 |
| FatalError | 致命错误 | 上下文溢出等 | 触发 Autosubmit |
| Autosubmit | 自动提交 | 致命错误 | 返回提交结果 |

#### 关键算法逻辑

```mermaid
flowchart TD
    A[捕获异常] --> B{错误类型判断}
    B -->|FormatError| C[增加重试计数]
    B -->|BlockedAction| C
    B -->|BashSyntax| C
    B -->|RetryWithOutput| D[重试不计数]
    B -->|RetryWithoutOutput| D
    B -->|ContextWindow| E[Autosubmit]
    B -->|CostLimit| E
    B -->|Timeout| E

    C --> F{计数 < 3?}
    F -->|是| G[构造错误历史]
    F -->|否| E
    G --> H[递归重试]

    style C fill:#90EE90
    style E fill:#FFB6C1
    style G fill:#87CEEB
```

---

### 3.2 forward_with_handling 内部结构

#### 职责定位

集中处理所有模型调用错误，实现统一的重试和恢复策略。

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  输入层                                                      │
│  ├── history: 对话历史                                       │
│  └── max_requeries: 最大重试次数                             │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  处理层                                                      │
│  ├── 调用 forward()                                         │
│  ├── 捕获异常                                               │
│  │   └── 分类处理                                           │
│  ├── 可重试错误                                             │
│  │   └── handle_error_with_retry()                          │
│  └── 致命错误                                               │
│      └── attempt_autosubmission_after_error()               │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层                                                      │
│  ├── StepOutput: 步骤结果                                   │
│  └── trajectory: 更新执行轨迹                               │
└─────────────────────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `forward_with_handling()` | `history: list[dict]` | `StepOutput` | 集中错误处理 | `sweagent/agent/agents.py:1062` |
| `handle_error_with_retry()` | `exception, template, n_requeries` | `list[dict]` | 构造重试历史 | `sweagent/agent/agents.py:1129` |
| `attempt_autosubmission_after_error()` | `error_type, message` | `StepOutput` | 自动提交 | `sweagent/agent/agents.py:823` |

---

### 3.3 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始 history] --> I2[forward 调用]
    end

    subgraph Process["处理阶段"]
        P1[模型调用] --> P2[解析响应]
        P2 --> P3[执行工具]
    end

    subgraph Output["输出阶段"]
        O1[构造 StepOutput] --> O2[更新 trajectory]
    end

    I2 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[执行失败] --> E1{错误类型}
    E1 -->|可重试| R1[增加计数]
    R1 --> R2{计数 < 3?}
    R2 -->|是| R3[构造错误模板]
    R3 --> R4[递归重试]
    R2 -->|否| F1[触发 Autosubmit]
    E1 -->|致命| F1

    F1 --> F2[提取 patch]
    F2 --> F3[生成提交]

    R4 --> End[返回结果]
    F3 --> End

    style R3 fill:#90EE90
    style F1 fill:#FFB6C1
    style F3 fill:#FFD700
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as forward_with_handling
    participant C as forward
    participant D as LLM
    participant E as Tool Execute

    A->>B: 调用 with history
    B->>C: 转发调用
    C->>D: 请求模型输出
    D-->>C: 返回 response
    C->>C: 解析 thought/action
    C->>E: 执行工具
    E-->>C: 返回 observation
    C-->>B: 返回 StepOutput
    B-->>A: 返回结果
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | `history: list[dict]` | 转发调用 | 同上 | `sweagent/agent/agents.py:1062` |
| 调用 | `history` | 请求 LLM | `response` | `sweagent/agent/agents.py:1018` |
| 解析 | `response` | 提取 thought/action | `tuple[str, str]` | `sweagent/agent/agents.py:850` |
| 执行 | `action` | 执行工具 | `observation` | `sweagent/agent/agents.py:900` |
| 组装 | `thought, action, observation` | 构造 StepOutput | `StepOutput` | `sweagent/agent/agents.py` |

### 4.2 异常流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as forward_with_handling
    participant C as forward
    participant D as LLM
    participant E as Error Handler

    A->>B: 调用 with history
    B->>C: 转发调用
    C->>D: 请求模型输出
    D-->>C: 返回格式错误 response
    C->>C: 抛出 FormatError
    C--xB: 异常
    B->>E: handle_error_with_retry
    E->>E: 构造错误模板
    E-->>B: 返回新 history
    B->>B: 递归调用（n_requeries=1）
    B->>C: 再次转发
    C->>D: 请求修正输出
    D-->>C: 返回正确 response
    C-->>B: 返回 StepOutput
    B-->>A: 返回结果
```

### 4.3 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始 history] --> I2[forward 调用]
    end

    subgraph Process["处理阶段"]
        P1[模型调用] --> P2{是否异常}
        P2 -->|否| P3[正常返回]
        P2 -->|是| P4[错误分类]
        P4 --> P5[重试/提交]
    end

    subgraph Output["输出阶段"]
        O1[StepOutput] --> O2[更新 trajectory]
    end

    I2 --> P1
    P3 --> O1
    P5 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# sweagent/exceptions.py
class FormatError(Exception):
    """模型响应无法正确解析为 thought 和 action 时抛出"""

class FunctionCallingFormatError(FormatError):
    """Function calling 解析器使用的格式错误异常"""
    def __init__(
        self,
        message: str,
        error_code: Literal[
            "missing", "multiple", "incorrect_args", "invalid_json",
            "invalid_command", "missing_arg", "unexpected_arg"
        ],
        **extra_info: Any,
    ):
        super().__init__(message + f" [error_code={error_code}]")
        self.message = message
        self.extra_info = {"error_code": error_code, **extra_info}
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `message` | `str` | 错误描述 |
| `error_code` | `Literal` | 错误类型标识 |
| `extra_info` | `dict` | 额外上下文信息 |

### 5.2 主链路代码

**关键代码**（核心逻辑）：

```python
# sweagent/agent/agents.py:1062-1100
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """转发模型并处理错误，如果可以则重新查询模型。"""

    def handle_error_with_retry(
        exception: Exception, template: str, n_requeries: int
    ) -> list[dict[str, str]]:
        """如果是格式/阻止列表/bash语法错误，则重新查询模型。"""
        self.logger.warning(
            "Requerying model after %s (%dth requery)",
            type(exception).__name__, n_requeries
        )
        step: StepOutput = getattr(exception, "step", StepOutput())
        self.add_step_to_trajectory(step)
        return self.get_model_requery_history(
            error_template=template,
            **step.to_template_format_dict(),
            exception_message=str(exception),
        )

    n_format_fails = 0
    while n_format_fails < self.max_requeries:  # max_requeries = 3
        try:
            return self.forward(history)

        # 可重试错误（增加计数）
        except FormatError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.format_error_template,
                n_requeries=n_format_fails
            )
        except _BlockedActionError as e:
            n_format_fails += 1
            history = handle_error_with_retry(...)
        except BashIncorrectSyntaxError as e:
            n_format_fails += 1
            history = handle_error_with_retry(...)

        # 致命错误 → Autosubmit
        except ContextWindowExceededError:
            return handle_error_with_autosubmission("exit_context", "...")
        except CostLimitExceededError:
            return handle_error_with_autosubmission("exit_cost", "...")
```

**设计意图**：
1. **分类处理**：区分可重试错误和致命错误，避免无效重试
2. **计数限制**：max_requeries 防止无限重试导致成本失控
3. **模板反馈**：使用 Jinja2 模板给 LLM 清晰的修正指导

<details>
<summary>查看完整实现</summary>

```python
# sweagent/agent/agents.py:1062-1200
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """Forward the model and handle errors, requerying the model if we can."""

    def handle_error_with_retry(
        exception: Exception, template: str, n_requeries: int
    ) -> list[dict[str, str]]:
        """Requery the model if this is a format/blocklist/bash syntax error."""
        self.logger.warning(
            "Requerying model after %s (%dth requery)",
            type(exception).__name__, n_requeries
        )
        step: StepOutput = getattr(exception, "step", StepOutput())
        self.add_step_to_trajectory(step)
        return self.get_model_requery_history(
            error_template=template,
            **step.to_template_format_dict(),
            exception_message=str(exception),
        )

    n_format_fails = 0
    while n_format_fails < self.max_requeries:
        try:
            return self.forward(history)
        except FormatError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.format_error_template,
                n_requeries=n_format_fails
            )
        except _BlockedActionError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.blocklist_error_template,
                n_requeries=n_format_fails
            )
        except BashIncorrectSyntaxError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.shell_check_error_template,
                n_requeries=n_format_fails
            )
        except _RetryWithOutput as e:
            history = handle_error_with_retry(
                exception=e,
                template=str(e),
                n_requeries=n_format_fails
            )
        except _RetryWithoutOutput as e:
            history = self.get_model_requery_history(
                error_template=str(e),
            )
        except ContextWindowExceededError:
            return self.handle_error_with_autosubmission(
                "exit_context",
                "Context window exceeded. Please try to finish the task with the submit command.",
            )
        except CostLimitExceededError as e:
            return self.handle_error_with_autosubmission(
                "exit_cost",
                f"Cost limit exceeded: {e}",
            )
        except SwerexException as e:
            return self.handle_error_with_autosubmission(
                "exit_error",
                f"Swerex exception: {e}",
            )
        except CommandTimeoutError as e:
            return self.handle_error_with_autosubmission(
                "exit_timeout",
                f"Command timeout: {e}",
            )

    # 重试次数耗尽
    return self.handle_error_with_autosubmission(
        "exit_format",
        f"Exceeded max requeries ({self.max_requeries}).",
    )
```

</details>

### 5.3 关键调用链

```text
Agent.step()                         [sweagent/agent/agents.py:200]
  -> forward_with_handling()         [sweagent/agent/agents.py:1062]
    -> forward()                     [sweagent/agent/agents.py:1018]
      - 模型调用和解析
    -> handle_error_with_retry()     [sweagent/agent/agents.py:1129]
      - 构造错误历史
    -> attempt_autosubmission_after_error() [sweagent/agent/agents.py:823]
      - 提取 patch 并提交
```

---

## 6. 设计意图与 Trade-off

### 6.1 SWE-agent 的选择

| 维度 | SWE-agent 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 错误处理 | 集中式 forward_with_handling | 分散式 try-catch | 统一策略，便于维护 |
| 重试策略 | 计数限制 + 模板反馈 | 指数退避 | 简单可控，适合 LLM 场景 |
| 异常恢复 | Autosubmit | Checkpoint 回滚 | 保留工作成果，适合 CI/CD |
| 错误反馈 | Jinja2 模板 | 固定字符串 | 灵活可配置，但增加复杂度 |

### 6.2 为什么这样设计？

**核心问题**：如何在保证任务完成率的同时控制成本？

**SWE-agent 的解决方案**：
- 代码依据：`sweagent/agent/agents.py:1062`
- 设计意图："优雅完成"而非"完美完成"
- 带来的好处：
  - 格式错误自动恢复，提高成功率
  - 异常时提交 patch，不浪费已做的工作
  - 模板化反馈帮助 LLM 自我纠正
- 付出的代价：
  - 重试增加成本
  - 自动提交可能包含不完整修复

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "基础错误处理"
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "Autosubmit + 模板反馈"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "Checkpoint + D-Mail"
    checkout main
    branch "Codex"
    checkout "Codex"
    commit id: "简单重试 + 安全优先"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "状态机错误处理"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "resetTimeoutOnProgress"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| SWE-agent | Autosubmit + 模板反馈 | CI/CD 自动化，追求完成率 |
| Kimi CLI | Checkpoint + D-Mail 回滚 | 交互式对话，支持用户撤销 |
| Codex | 简单重试，无自动提交 | 企业环境，强调安全性 |
| Gemini CLI | 状态机驱动错误处理 | 复杂任务，需要精细控制 |
| OpenCode | resetTimeoutOnProgress | 长任务，需要超时保护 |

#### 各项目错误处理策略对比

| 维度 | SWE-agent | Kimi CLI | Codex | Gemini CLI | OpenCode |
|-----|-----------|----------|-------|------------|----------|
| **错误恢复** | Autosubmit | Checkpoint 回滚 | 简单重试 | 状态机恢复 | 超时重置 |
| **重试策略** | 计数限制 (3次) | 无限制 | 简单计数 | 状态驱动 | 进度超时重置 |
| **异常处理** | 模板化反馈 | D-Mail 选择 | 安全优先 | 分层处理 | 自动重试 |
| **完成保障** | 自动提交 patch | 状态回滚 | 无 | 无 | 无 |
| **适用场景** | CI/CD 自动化 | 交互式对话 | 企业安全 | 复杂任务 | 长任务 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 重试耗尽 | n_format_fails >= max_requeries | `sweagent/agent/agents.py:1195` |
| 上下文溢出 | ContextWindowExceededError | `sweagent/agent/agents.py:1176` |
| 成本超限 | CostLimitExceededError | `sweagent/agent/agents.py:1178` |
| 连续超时 | _n_consecutive_timeouts >= 3 | `sweagent/agent/agents.py:971` |

### 7.2 超时/资源限制

```python
# sweagent/agent/agents.py:1018
def forward(self, history: list[dict[str, str]]) -> StepOutput:
    # 检查总执行时间
    if self._total_execution_time > self.tools.config.total_execution_timeout:
        raise _TotalExecutionTimeExceeded()
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| FormatError | 模板化反馈 + 重试 | `sweagent/agent/agents.py:1153` |
| BashIncorrectSyntaxError | shell 检查反馈 + 重试 | `sweagent/agent/agents.py:1167` |
| CommandTimeoutError | Autosubmit | `sweagent/agent/agents.py:1180` |
| SwerexException | Autosubmit | `sweagent/agent/agents.py:1182` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 错误类型定义 | `sweagent/exceptions.py` | - | FormatError、CostLimitExceededError 等 |
| 集中错误处理 | `sweagent/agent/agents.py` | 1062 | forward_with_handling() |
| 重试逻辑 | `sweagent/agent/agents.py` | 1129 | handle_error_with_retry() |
| 自动提交 | `sweagent/agent/agents.py` | 823 | attempt_autosubmission_after_error() |
| 错误模板配置 | `sweagent/agent/agents.py` | TemplateConfig | shell_check_error_template 等 |
| 超时配置 | `sweagent/tools/tools.py` | ToolConfig | execution_timeout、total_execution_timeout |

---

## 9. 延伸阅读

- 前置知识：`docs/swe-agent/04-swe-agent-agent-loop.md`（Agent 循环中的错误处理调用点）
- 相关机制：`docs/swe-agent/questions/swe-agent-infinite-loop-prevention.md`（防循环机制）
- 深度分析：`docs/swe-agent/questions/swe-agent-skill-execution-timeout.md`（超时处理详细分析）
- 对比分析：`docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`（Kimi CLI 的 Checkpoint 回滚）

---

*✅ Verified: 基于 sweagent/exceptions.py、sweagent/agent/agents.py:1062 等源码分析*
*基于版本：SWE-agent (baseline 2026-02-08) | 最后更新：2026-03-03*
