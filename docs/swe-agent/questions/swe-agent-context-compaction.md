# SWE-agent Context Compaction

## TL;DR（结论先行）

SWE-agent **不支持 LLM 压缩**，仅使用简单的滑动窗口和历史处理器来管理上下文长度。

SWE-agent 的核心取舍：**简单滑动窗口**（对比 Codex/Gemini CLI 的 LLM 驱动压缩）

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

上下文长度管理涉及：
- Token 超限导致模型调用失败
- 长历史导致成本增加
- 重要信息被截断

SWE-agent 的设计选择：
- 研究场景通常任务较短（< 20 轮）
- 确定性行为比智能压缩更重要
- 简单滑动窗口足够应对大多数场景

### 1.2 核心挑战

| 挑战 | LLM 压缩 | 滑动窗口 |
|-----|---------|---------|
| 智能程度 | 高，生成语义摘要 | 低，直接截断 |
| 成本 | 需要额外 LLM 调用 | 零成本 |
| 确定性 | 有随机性 | 完全确定 |
| 实现复杂度 | 复杂 | 简单 |
| 适用场景 | 长任务 | 短任务 |

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
│ ▓▓▓ Context Management ▓▓▓                                  │
│ SWE-agent/sweagent/agent/history_processors.py               │
│ - LastNObservations: 滑动窗口                               │
│ - CacheControlHistoryProcessor: Claude 缓存控制             │
│ - ClosedWindowHistoryProcessor: 关闭窗口替换                │
│ - RemoveRegex: 正则清理                                     │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Final History (无 LLM 压缩)                                  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `LastNObservations` | 滑动窗口截断 | `SWE-agent/sweagent/agent/history_processors.py:85` |
| `CacheControlHistoryProcessor` | Claude 缓存控制 | `SWE-agent/sweagent/agent/history_processors.py:261` |
| `ClosedWindowHistoryProcessor` | 替换已关闭窗口 | `SWE-agent/sweagent/agent/history_processors.py:215` |
| `TagToolCallObservations` | 标签工具调用 | `SWE-agent/sweagent/agent/history_processors.py:179` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant B as HistoryProcessorPipeline
    participant C as RemoveRegex
    participant D as ClosedWindow
    participant E as LastNObservations
    participant F as CacheControl

    A->>B: 1. 请求处理历史
    B->>C: 2. 应用 RemoveRegex
    C-->>B: 3. 返回清理后历史
    B->>D: 4. 应用 ClosedWindow
    D-->>B: 5. 返回替换后历史
    B->>E: 6. 应用 LastNObservations
    E-->>B: 7. 返回截断后历史
    B->>F: 8. 应用 CacheControl
    F-->>B: 9. 返回最终历史
    B-->>A: 10. 返回处理结果
```

---

## 3. 核心组件详细分析

### 3.1 LastNObservations（滑动窗口）

#### 职责定位

只保留最近 N 条观察记录的简单滑动窗口。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[输入历史] --> B{长度 <= N?}
    B -->|是| C[返回全部]
    B -->|否| D[截取最后 N 条]
    C --> E[输出]
    D --> E

    style C fill:#90EE90
    style D fill:#87CEEB
```

---

### 3.2 CacheControlHistoryProcessor

#### 职责定位

为 Claude 模型添加缓存控制标记，优化 token 使用效率。

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  CacheControlHistoryProcessor                                │
│  ├── cache_breakpoint_every: int = 5                        │
│  ├── 遍历历史                                               │
│  ├── 每 5 条设置 cache_control                              │
│  └── 返回处理后的历史                                       │
└─────────────────────────────────────────────────────────────┘
```

---

### 3.3 ClosedWindowHistoryProcessor

#### 职责定位

替换已关闭文件的窗口内容为简单标记。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[输入历史] --> B[第一遍: 追踪打开文件]
    B --> C[第二遍: 替换已关闭内容]
    C --> D{文件在打开集合?}
    D -->|是| E[保留内容]
    D -->|否| F[替换为标记]
    E --> G[输出]
    F --> G

    style E fill:#90EE90
    style F fill:#87CEEB
```

---

## 4. 端到端数据流转

### 4.1 正常流程

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as HistoryProcessorPipeline
    participant C as RemoveRegex
    participant D as ClosedWindow
    participant E as LastNObservations
    participant F as CacheControl

    A->>B: 传入原始历史
    B->>C: 清理格式
    C-->>B: 返回
    B->>D: 替换关闭窗口
    D-->>B: 返回
    B->>E: 滑动窗口截断
    E-->>B: 返回
    B->>F: 添加缓存控制
    F-->>B: 返回
    B-->>A: 最终历史
```

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始历史]
    end

    subgraph Process["处理阶段"]
        P1[RemoveRegex] --> P2[ClosedWindow]
        P2 --> P3[LastNObservations]
        P3 --> P4[CacheControl]
    end

    subgraph Output["输出阶段"]
        O1[最终历史]
    end

    I1 --> P1
    P4 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# SWE-agent/sweagent/agent/history_processors.py:85-170
class LastNObservations(BaseModel):
    """Elide all but the last n observations or remove tagged observations.

    This is our most classic history processor, used in the original paper
    to elide but the last 5 observations.
    Elided observations are replaced by "Old environment output: (n lines omitted)".
    """
    n: int = 5
    polling: int = 1
    always_remove_output_for_tags: set[str] = {"remove_output"}
    always_keep_output_for_tags: set[str] = {"keep_output"}
    type: Literal["last_n_observations"] = "last_n_observations"

    def __call__(self, history: History) -> History:
        """只保留最近 N 条观察记录的简单滑动窗口"""
        new_history = []
        omit_content_idxs = self._get_omit_indices(history)

        for idx, entry in enumerate(history):
            tags = set(entry.get("tags", []))

            if (idx not in omit_content_idxs or
                tags & self.always_keep_output_for_tags) and not (
                tags & self.always_remove_output_for_tags
            ):
                new_history.append(entry)
            else:
                # 替换为摘要
                num_text_lines, num_images = _get_content_stats(entry)
                entry["content"] = f"Old environment output: ({num_text_lines} lines omitted)"
                new_history.append(entry)

        return new_history
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `n` | `int` | 保留的历史记录数量 |

### 5.2 主链路代码

```python
# SWE-agent/sweagent/agent/agents.py:155
history_processors: list[HistoryProcessor] = Field(default_factory=lambda: [DefaultHistoryProcessor()])

# 配置示例 (config/default.yaml):
# agent:
#   history_processors:
#     - type: last_n_observations
#       n: 5
#     - type: cache_control
#       last_n_messages: 2
```

**代码要点**：

1. **Pydantic 模型**：使用 Pydantic BaseModel 定义处理器
2. **简单处理**：无 LLM 调用，纯本地处理
3. **可配置**：通过 YAML 配置处理器列表

### 5.3 关键调用链

```text
Agent.step()                         [sweagent/agent/agents.py:200]
  -> build_history()                 [sweagent/agent/agents.py:400]
    -> history_processors           [SWE-agent/sweagent/agent/agents.py:155]
      -> LastNObservations()          [SWE-agent/sweagent/agent/history_processors.py:85]
      -> ClosedWindowHistoryProcessor() [SWE-agent/sweagent/agent/history_processors.py:215]
      -> CacheControlHistoryProcessor() [SWE-agent/sweagent/agent/history_processors.py:261]
```

---

## 6. 设计意图与 Trade-off

### 6.1 SWE-agent 的选择

| 维度 | SWE-agent 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 压缩机制 | 滑动窗口 + 处理器 | LLM 压缩 | 简单可靠，零成本 |
| 智能程度 | 无 | 高 | 确定性行为，可复现 |
| 成本 | 零 | 中/高 | 不消耗额外 token |
| 适用任务 | 短任务 | 长任务 | 专注研究场景 |

### 6.2 为什么这样设计？

**核心问题**：软件工程研究任务是否需要智能上下文压缩？

**SWE-agent 的解决方案**：
- 代码依据：`SWE-agent/sweagent/agent/history_processors.py:85`
- 设计意图：简单可靠，专注研究场景
- 带来的好处：
  - 确定性行为，便于实验复现
  - 零额外成本
  - 实现简单，易于维护
- 付出的代价：
  - 无智能压缩，可能丢失重要信息
  - 不适用于长任务

### 6.3 与其他项目的对比

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| SWE-agent | 滑动窗口，无 LLM 压缩 | 短任务，研究场景 |
| Codex | LLM 摘要压缩 | 长任务，企业环境 |
| Gemini CLI | 两阶段验证压缩 | 复杂任务，高精度要求 |
| OpenCode | 双重机制 | 长任务，需要智能压缩 |

---

## 7. 边界情况与错误处理

### 7.1 截断情况

| 情况 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 历史过长 | LastNObservations 截断 | `SWE-agent/sweagent/agent/history_processors.py:85` |
| 窗口关闭 | ClosedWindow 替换为标记 | `SWE-agent/sweagent/agent/history_processors.py:215` |

### 7.2 配置示例

```yaml
# config/default.yaml
history:
  # 滑动窗口配置
  last_n_observations:
    enabled: true
    n: 15  # 保留最近 15 条

  # 缓存控制（仅 Claude 模型）
  cache_control:
    enabled: true
    breakpoint_every: 5

  # 已关闭窗口处理
  closed_window:
    enabled: true

  # 正则清理
  remove_regex:
    enabled: true
    patterns:
      - "\\x1b\\[[0-9;]*m"  # ANSI 颜色
      - "\\n{3,}"          # 多余空行
```

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 滑动窗口 | `SWE-agent/sweagent/agent/history_processors.py` | 85 | LastNObservations |
| 缓存控制 | `SWE-agent/sweagent/agent/history_processors.py` | 261 | CacheControlHistoryProcessor |
| 窗口替换 | `SWE-agent/sweagent/agent/history_processors.py` | 215 | ClosedWindowHistoryProcessor |
| 标签工具 | `SWE-agent/sweagent/agent/history_processors.py` | 179 | TagToolCallObservations |
| 默认处理器 | `SWE-agent/sweagent/agent/history_processors.py` | 74 | DefaultHistoryProcessor |

---

## 9. 延伸阅读

- 前置知识：`docs/swe-agent/04-swe-agent-agent-loop.md`（Agent 循环中的历史管理）
- 相关机制：`docs/swe-agent/11-swe-agent-prompt-organization.md`（Prompt 组织与历史关系）
- 对比分析：`docs/codex/07-codex-memory-context.md`（Codex 的 LLM 压缩实现）

---

*✅ Verified: 基于 SWE-agent/sweagent/agent/history_processors.py 源码分析*
*基于版本：SWE-agent (baseline 2026-02-08) | 最后更新：2026-02-25*
