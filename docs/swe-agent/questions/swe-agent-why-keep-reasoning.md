# SWE-agent Why Keep Reasoning

## TL;DR（结论先行）

SWE-agent 保留 `thinking_blocks` 推理内容是为了支持 **forward_with_handling 的错误恢复**和**多轮尝试的最佳选择**，使 LLM 在代码修复任务中能自我纠错并从中学习。核心取舍是**完整历史保留**（对比简单截断策略）。

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

没有保留推理内容时：
- LLM 发生格式错误后无法看到原来的思考过程
- 多轮尝试后无法比较不同策略的有效性
- Reviewer 无法评估各轮次推理的合理性

有了推理内容保留：
- 错误恢复时 LLM 能看到自己的思考过程
- 支持从多次尝试中选择最佳结果
- History Processors 可以智能过滤关键内容

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 错误恢复 | LLM 无法理解之前哪里出错 |
| 尝试比较 | 无法评估不同策略的效果 |
| 历史截断 | 重要思考内容被丢弃 |
| 调试分析 | 无法追踪模型决策过程 |

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
│ ▓▓▓ Reasoning Preservation ▓▓▓                              │
│ sweagent/types.py                                            │
│ - HistoryItem.thinking_blocks: 存储推理内容                 │
│ - StepOutput: 包含 thought 字段                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Error        │ │ Retry Loop   │ │ History      │
│ Recovery     │ │ Selection    │ │ Processors   │
│ 错误恢复      │ │ 重试选择      │ │ 历史处理器    │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `HistoryItem` | 存储 thinking_blocks | `sweagent/types.py` |
| `StepOutput` | 包含 thought 字段 | `sweagent/types.py` |
| `forward_with_handling` | 利用推理内容错误恢复 | `sweagent/agent/agents.py` |
| `ScoreRetryLoop` | 基于推理内容选择最佳结果 | `sweagent/agent/reviewer.py` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant B as forward_with_handling
    participant C as HistoryItem
    participant D as Retry Loop

    A->>B: 1. 执行模型调用
    B->>C: 2. 保存 thinking_blocks
    C-->>B: 3. 确认存储

    alt 发生错误
        B->>B: 4a. 构造错误反馈
        B->>C: 5a. 获取原推理内容
        C-->>B: 6a. 返回 thinking_blocks
        B->>B: 7a. 构造重试历史
        B->>A: 8a. 重新查询
    else 多轮尝试
        A->>D: 4b. 请求选择最佳
        D->>C: 5b. 获取各轮推理
        C-->>D: 6b. 返回 thinking_blocks
        D->>D: 7b. 比较评估
        D-->>A: 8b. 返回最佳索引
    end
```

---

## 3. 核心组件详细分析

### 3.1 错误恢复与 Requery

#### 职责定位

利用保留的推理内容帮助 LLM 在发生错误时自我纠正。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[模型调用] --> B{发生错误?}
    B -->|否| C[正常继续]
    B -->|是| D[获取原 thinking_blocks]
    D --> E[构造错误反馈]
    E --> F[包含原推理内容]
    F --> G[重新查询 LLM]
    G --> H{成功?}
    H -->|是| C
    H -->|否| I{重试次数 < 3?}
    I -->|是| D
    I -->|否| J[Autosubmit]

    style C fill:#90EE90
    style J fill:#FFB6C1
    style F fill:#87CEEB
```

---

### 3.2 Autosubmit 紧急恢复

#### 职责定位

即使环境崩溃，也尝试从 trajectory 中提取 patch。

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  attempt_autosubmission_after_error                          │
│  ├── 检查运行时存活状态                                     │
│  ├── 获取最后一个 trajectory step                           │
│  ├── 提取 diff（如果存在）                                  │
│  ├── thinking_blocks 用于分析失败原因                       │
│  └── 生成部分提交                                           │
└─────────────────────────────────────────────────────────────┘
```

---

### 3.3 多轮尝试的最佳选择

#### 职责定位

Reviewer 基于各轮次的 `thinking_blocks` 选择最佳结果。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[多次尝试完成] --> B[获取各轮次结果]
    B --> C[提取 thinking_blocks]
    C --> D[评估推理合理性]
    D --> E[比较执行结果]
    E --> F[选择最佳索引]
    F --> G[返回最优结果]

    style G fill:#90EE90
```

---

## 4. 端到端数据流转

### 4.1 正常流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as LLM
    participant C as HistoryItem
    participant D as Trajectory

    A->>B: 请求生成
    B-->>A: 返回 response + thinking_blocks
    A->>C: 创建 HistoryItem
    C->>C: 存储 thinking_blocks
    A->>D: 添加到 trajectory
```

### 4.2 错误恢复流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as forward_with_handling
    participant C as HistoryItem
    participant D as LLM

    A->>B: 调用 forward
    B->>D: 请求生成
    D-->>B: 返回错误格式
    B->>B: 抛出 FormatError
    B->>C: 获取原 thinking_blocks
    C-->>B: 返回推理内容
    B->>B: 构造错误反馈
    Note over B: 包含原推理 + 错误说明
    B->>D: 重新查询
    D-->>B: 返回修正结果
    B-->>A: 返回 StepOutput
```

### 4.3 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[LLM Response] --> I2[提取 thinking_blocks]
    end

    subgraph Process["处理阶段"]
        P1[存储到 HistoryItem] --> P2{发生错误?}
        P2 -->|是| P3[获取原推理]
        P2 -->|否| P4[正常继续]
        P3 --> P5[构造反馈]
    end

    subgraph Output["输出阶段"]
        O1[重试历史]
    end

    I2 --> P1
    P5 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# sweagent/types.py
class HistoryItem(BaseModel):
    """历史记录项，包含推理内容"""
    thought: str = ""
    action: str = ""
    observation: str = ""
    thinking_blocks: list[dict[str, Any]] | None = None
    # ... 其他字段
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `thinking_blocks` | `list[dict]` | 存储模型的推理内容 |
| `thought` | `str` | 解析后的思考文本 |

### 5.2 主链路代码

```python
# sweagent/agent/agents.py
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """Forward the model and handle errors, requerying the model if we can."""
    n_format_fails = 0
    while n_format_fails < self.max_requeries:
        try:
            return self.forward(history)
        except FormatError as e:
            # 基于原推理内容重新查询
            n_format_fails += 1
            history = handle_error_with_retry(...)
            # thinking_blocks 保留在历史中，帮助 LLM 理解错误
```

**代码要点**：

1. **保留上下文**：错误重试时原推理内容仍在历史中
2. **自我纠正**：LLM 能看到自己的思考过程并修正
3. **历史完整**：不丢弃任何推理内容

### 5.3 关键调用链

```text
Agent.step()                         [sweagent/agent/agents.py:200]
  -> forward_with_handling()         [sweagent/agent/agents.py:1062]
    -> forward()                     [sweagent/agent/agents.py:1018]
      -> 保存 thinking_blocks        [sweagent/types.py:HistoryItem]
    -> FormatError 捕获              [sweagent/agent/agents.py:1153]
      -> 利用原推理构造重试历史
  -> ScoreRetryLoop.get_best()       [sweagent/agent/reviewer.py:559]
    -> 比较各轮次 thinking_blocks
```

---

## 6. 设计意图与 Trade-off

### 6.1 SWE-agent 的选择

| 维度 | SWE-agent 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 推理保留 | 完整保留 | 截断/摘要 | 支持完整错误恢复，但增加存储 |
| 历史长度 | 无限制 | 固定窗口 | 完整上下文，但可能超长 |
| 过滤策略 | History Processors | 简单截断 | 智能过滤，但增加复杂度 |
| 使用场景 | 错误恢复 + 重试选择 | 仅调试 | 功能更全面 |

### 6.2 为什么这样设计？

**核心问题**：如何在错误恢复时帮助 LLM 自我纠正？

**SWE-agent 的解决方案**：
- 代码依据：`sweagent/types.py:HistoryItem`
- 设计意图：完整保留推理过程，支持自我纠正
- 带来的好处：
  - 错误恢复时 LLM 能看到原思考
  - 支持多轮尝试比较
  - 便于调试和分析
- 付出的代价：
  - 增加存储开销
  - 可能超出上下文限制

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "基础推理支持"
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "thinking_blocks 完整保留"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "Checkpoint 保存推理"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "分层记忆管理"
    checkout main
    branch "Codex"
    checkout "Codex"
    commit id: "AgentReasoning 事件流"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "MessagePart 推理组件"
```

| 项目 | 核心差异 | 推理内容使用方式 | 适用场景 |
|-----|---------|-----------------|---------|
| SWE-agent | 完整保留 thinking_blocks | 错误恢复 + 重试选择 | 代码修复，需要错误恢复 |
| Kimi CLI | Checkpoint 保存完整状态 | 对话回滚时恢复推理 | 交互式对话，支持回滚 |
| Gemini CLI | 分层记忆管理 | 长期任务上下文保持 | 复杂任务，多层次上下文 |
| Codex | AgentReasoning 事件流 | 实时展示 + 可配置摘要 | 企业级应用，可观测性 |
| OpenCode | MessagePart 组件化 | UI 展示 + 调试分析 | 可视化交互，开发调试 |

#### 各项目推理内容处理对比

| 维度 | SWE-agent | Kimi CLI | Gemini CLI | Codex | OpenCode |
|-----|-----------|----------|------------|-------|----------|
| **存储位置** | HistoryItem | Checkpoint 文件 | 分层内存 | 事件日志 | 消息组件 |
| **保留策略** | 完整保留 | 状态快照 | 智能压缩 | 可配置 | 结构化存储 |
| **主要用途** | 错误恢复 | 状态回滚 | 上下文保持 | 可观测性 | UI 展示 |
| **过滤机制** | History Processors | D-Mail 选择 | 分层淘汰 | 摘要配置 | 组件渲染 |

---

## 7. 边界情况与错误处理

### 7.1 存储限制

| 情况 | 处理策略 | 代码位置 |
|---------|---------|---------|
| thinking_blocks 为空 | 正常存储，值为 None | `sweagent/types.py` |
| 内容过长 | History Processors 过滤 | `sweagent/agent/history_processors.py` |
| 解析失败 | 保留原始内容 | `sweagent/tools/parsing.py` |

### 7.2 History Processors 过滤

```python
# sweagent/agent/history_processors.py
class LastNObservations:
    """Elide all but the last n observations"""
    always_keep_output_for_tags: set[str] = {"keep_output"}
    # 通过 tags 标记保留关键推理内容
```

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 数据结构 | `sweagent/types.py` | - | HistoryItem.thinking_blocks |
| 错误恢复 | `sweagent/agent/agents.py` | 1062 | forward_with_handling |
| 重试选择 | `sweagent/agent/reviewer.py` | 559 | ScoreRetryLoop 类 |
| 历史处理 | `sweagent/agent/history_processors.py` | - | 智能过滤推理内容 |

---

## 9. 延伸阅读

- 前置知识：`docs/swe-agent/04-swe-agent-agent-loop.md`（Agent 循环中的历史管理）
- 相关机制：`docs/swe-agent/questions/swe-agent-tool-error-handling.md`（错误处理详细分析）
- 对比分析：`docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`（Kimi CLI 的推理内容保存）
- 协议参考：`docs/codex/06-codex-mcp-integration.md`（Codex 的 AgentReasoning 事件）

---

*✅ Verified: 基于 sweagent/types.py、sweagent/agent/agents.py:1062 等源码分析*
*基于版本：SWE-agent (baseline 2026-02-08) | 最后更新：2026-02-25*
