# Kimi CLI 上下文压缩机制

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 25-35 分钟 |
> | 前置文档 | `docs/kimi-cli/04-kimi-cli-agent-loop.md`、`docs/kimi-cli/07-kimi-cli-memory-context.md` |
> | 文档结构 | 速览 → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

**一句话定义**：Context Compaction 是 AI Coding Agent 解决上下文窗口超限的核心机制，通过将历史消息压缩为摘要来释放 token 预算。

Kimi CLI 的核心取舍：**SimpleCompaction 策略 + 强制保留最近 N 条消息**（对比 Gemini CLI 的两阶段验证压缩、Codex 的渐进式截断兜底）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 压缩策略 | SimpleCompaction：保留最近 N 条，压缩更早历史 | `kimi-cli/src/kimi_cli/soul/compaction.py:42` |
| 触发条件 | `token_count + reserved >= max_context_size` | `kimi-cli/src/kimi_cli/soul/kimisoul.py:342` |
| LLM 调用 | 使用 kosong.step() + EmptyToolset 生成摘要 | `kimi-cli/src/kimi_cli/soul/compaction.py:54` |
| 保留策略 | 默认保留最近 2 条 user/assistant 消息 | `kimi-cli/src/kimi_cli/soul/compaction.py:43` |
| 失败处理 | 返回原始消息，安全回退 | `kimi-cli/src/kimi_cli/soul/compaction.py:96-97` |

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

没有 Context Compaction：
```
用户: "分析这个大型项目并修复 bug"
  -> LLM 调用工具读取文件（产生大量输出）
  -> Token 数迅速达到 128K 上限
  -> 后续无法继续对话，任务中断
```

有 Context Compaction：
```
用户: "分析这个大型项目并修复 bug"
  -> LLM 调用工具读取文件
  -> Token 接近上限，触发压缩
  -> 历史消息被摘要替换，释放预算
  -> 任务继续完成
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| Token 上限硬性限制 | 长对话无法完成，任务中断 |
| 压缩可能丢失关键信息 | 丢失用户原始需求或技术决策 |
| 工具输出过大 | 单次工具调用挤占全部上下文空间 |
| 压缩时机选择 | 过早压缩浪费上下文，过晚导致失败 |

---

## 2. 整体架构

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Runtime                                 │
│ kimi-cli/src/kimi_cli/soul/kimisoul.py:302                   │
└───────────────────────┬─────────────────────────────────────┘
                        │ 触发压缩检查
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Context Compaction ▓▓▓                                  │
│ kimi-cli/src/kimi_cli/soul/                                  │
│   compaction.py                                              │
│ - Compaction (Protocol): 压缩接口定义                        │
│ - SimpleCompaction: 默认实现                                 │
│   - compact(): 主压缩方法                                    │
│   - prepare(): 准备压缩数据                                  │
│                                                              │
│ kimi-cli/src/kimi_cli/soul/kimisoul.py                       │
│ - compact_context(): 主入口                                  │
│   - _compact_with_retry(): 带重试的压缩                      │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ LLM API      │ │ Context      │ │ Slash        │
│ kosong       │ │ 状态管理     │ │ 命令         │
│              │ │              │ │ /compact     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `Compaction` (Protocol) | 定义压缩接口契约 | `kimi-cli/src/kimi_cli/soul/compaction.py:17-33` ✅ Verified |
| `SimpleCompaction` | 默认压缩实现，保留最近 N 条消息 | `kimi-cli/src/kimi_cli/soul/compaction.py:42-117` ✅ Verified |
| `compact()` | 核心压缩方法，调用 LLM 生成摘要 | `kimi-cli/src/kimi_cli/soul/compaction.py:46-76` ✅ Verified |
| `prepare()` | 准备压缩数据，分割待压缩/保留消息 | `kimi-cli/src/kimi_cli/soul/compaction.py:82-116` ✅ Verified |
| `compact_context()` | 主入口，协调压缩流程与上下文清理 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:480-506` ✅ Verified |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant K as KimiSoul
    participant C as SimpleCompaction
    participant L as LLM (kosong)
    participant X as Context

    A->>K: 1. 触发压缩检查
    Note over K: token_count + reserved >= max_context_size
    K->>K: 2. 发送 CompactionBegin 事件
    K->>C: 3. compact(history, llm)

    C->>C: 4. prepare(messages)
    Note over C: 分割待压缩/保留消息

    C->>L: 5. kosong.step() 生成摘要
    Note over L: 使用 EmptyToolset，无工具调用
    L-->>C: 6. 返回摘要内容

    C->>C: 7. 过滤 ThinkPart，构建新消息列表
    C-->>K: 8. 返回压缩后消息

    K->>X: 9. context.clear() 清空旧上下文
    K->>X: 10. checkpoint() 新建检查点
    K->>X: 11. append_message() 写入压缩消息
    K->>K: 12. 发送 CompactionEnd 事件
    K-->>A: 13. 压缩完成
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent Loop 检测到 token 超限 | 在每次 step 前检查，避免调用失败 |
| 3 | 调用 SimpleCompaction.compact() | 解耦压缩策略，支持未来扩展 |
| 4-5 | 准备数据并调用 LLM | 使用 kosong.step 复用现有 LLM 调用机制 |
| 7 | 过滤 ThinkPart | 减少不必要的 token 消耗 |
| 9-11 | 清空并重建上下文 | 原子性操作，配合 checkpoint 保证一致性 |

---

## 3. 核心组件详细分析

### 3.1 SimpleCompaction 内部结构

#### 职责定位

一句话说明：通过保留最近 N 条消息，将更早历史压缩为 LLM 生成的摘要，实现上下文长度控制。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Idle: 初始化
    Idle --> Preparing: 收到压缩请求
    Preparing --> Compacting: 数据准备完成
    Preparing --> Idle: 无需压缩（消息不足）
    Compacting --> Succeeded: LLM 返回摘要
    Compacting --> Failed: LLM 调用失败
    Succeeded --> Idle: 返回压缩结果
    Failed --> Idle: 返回原始消息

    note right of Preparing
        检查 max_preserved_messages
        默认保留最近 2 条
    end note

    note right of Compacting
        调用 kosong.step()
        使用 EmptyToolset
    end note
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Idle | 空闲等待 | 初始化完成或压缩结束 | 收到压缩请求 |
| Preparing | 准备压缩数据 | 收到压缩请求 | 确定待压缩/保留消息范围 |
| Compacting | 调用 LLM 生成摘要 | 有待压缩消息 | LLM 返回结果或失败 |
| Succeeded | 压缩成功 | LLM 成功返回摘要 | 自动返回 Idle |
| Failed | 压缩失败 | LLM 调用失败 | 返回原始消息 |

#### 内部数据流

```text
┌─────────────────────────────────────────────────────────────┐
│  输入层                                                      │
│  ├── 消息历史 ──► 角色过滤 ──► user/assistant 消息           │
│  └── 配置参数 ──► max_preserved_messages (默认 2)            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  处理层                                                      │
│  ├── 主处理器: 消息分割                                       │
│  │   └── 从后向前遍历 ──► 计数 user/assistant ──► 确定分割点 │
│  ├── 辅助处理器: 数据格式化                                   │
│  │   └── 待压缩消息 ──► 添加序号/角色标记 ──► 附加提示词     │
│  └── 协调器: LLM 调用与结果处理                               │
│      └── kosong.step() ──► 过滤 ThinkPart ──► 构建新消息     │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层                                                      │
│  ├── 系统提示前缀 (Previous context has been compacted...)   │
│  ├── 压缩摘要内容                                            │
│  └── 保留的最近 N 条消息                                     │
└─────────────────────────────────────────────────────────────┘
```

#### 关键算法逻辑

```mermaid
flowchart TD
    A[开始压缩] --> B{消息为空?}
    B -->|是| C[返回原消息]
    B -->|否| D{max_preserved <= 0?}
    D -->|是| C
    D -->|否| E[从后向前遍历消息]
    E --> F{找到 N 条 user/assistant?}
    F -->|否| C
    F -->|是| G[分割消息]
    G --> H{有待压缩消息?}
    H -->|否| I[返回保留消息]
    H -->|是| J[格式化待压缩消息]
    J --> K[调用 LLM 生成摘要]
    K --> L[过滤 ThinkPart]
    L --> M[构建新消息列表]
    M --> N[结束]
    C --> N
    I --> N

    style M fill:#90EE90
    style C fill:#FFB6C1
```

**算法要点**：

1. **反向遍历**：从最新消息开始向前计数，确保保留最近交互
2. **角色过滤**：只统计 user/assistant 消息，忽略 system/tool 消息
3. **安全回退**：任何条件不满足时返回原始消息，避免数据丢失
4. **ThinkPart 过滤**：压缩输入和输出都过滤思考内容，减少噪音

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `__init__()` | `max_preserved_messages: int` | - | 初始化保留消息数 | `kimi-cli/src/kimi_cli/soul/compaction.py:43-44` ✅ Verified |
| `compact()` | `messages, llm` | `Sequence[Message]` | 核心压缩方法 | `kimi-cli/src/kimi_cli/soul/compaction.py:46-76` ✅ Verified |
| `prepare()` | `messages` | `PrepareResult` | 准备压缩数据 | `kimi-cli/src/kimi_cli/soul/compaction.py:82-116` ✅ Verified |

---

### 3.2 KimiSoul.compact_context() 内部结构

#### 职责定位

一句话说明：协调压缩流程，处理重试逻辑，管理上下文状态转换。

#### 关键算法逻辑

```mermaid
flowchart TD
    A[compact_context 调用] --> B[发送 CompactionBegin 事件]
    B --> C{重试循环}
    C --> D[调用 _compaction.compact]
    D --> E{成功?}
    E -->|否| F{可重试错误?}
    F -->|是| G[指数退避等待]
    G --> C
    F -->|否| H[抛出异常]
    E -->|是| I[context.clear]
    I --> J[checkpoint]
    J --> K[append_message]
    K --> L[发送 CompactionEnd 事件]
    L --> M[结束]
    H --> M

    style L fill:#90EE90
    style H fill:#FF6B6B
```

---

### 3.3 组件间协作时序

展示完整压缩流程的组件协作：

```mermaid
sequenceDiagram
    participant U as Agent Loop
    participant K as KimiSoul
    participant R as Retry Wrapper
    participant C as SimpleCompaction
    participant P as Prepare
    participant L as LLM
    participant X as Context

    U->>K: _agent_loop() 检查 token
    activate K
    Note over K: token_count + reserved >= max_context

    K->>K: wire_send(CompactionBegin)
    K->>R: _compact_with_retry()
    activate R

    loop 重试 (最多 max_retries)
        R->>C: compact(history, llm)
        activate C

        C->>P: prepare(messages)
        activate P
        P->>P: 反向遍历计数 user/assistant
        P->>P: 分割 to_compact / to_preserve
        P-->>C: PrepareResult
        deactivate P

        alt 有待压缩消息
            C->>C: 格式化消息（添加序号/角色）
            C->>L: kosong.step()
            activate L
            Note over L: system: "You are a helpful assistant..."
            Note over L: toolset: EmptyToolset()
            L-->>C: StepResult
            deactivate L

            C->>C: 过滤 ThinkPart
            C->>C: 构建 [system提示 + 摘要 + 保留消息]
        else 无需压缩
            C-->>R: 返回原消息
        end

        C-->>R: 压缩后消息
        deactivate C
    end

    R-->>K: 压缩结果
    deactivate R

    K->>X: context.clear()
    activate X
    Note over X: 轮转 context.jsonl
    X-->>K: 完成
    deactivate X

    K->>X: checkpoint()
    K->>X: append_message(compacted)

    K->>K: wire_send(CompactionEnd)
    K-->>U: 继续 agent loop
    deactivate K
```

**协作要点**：

1. **Agent Loop 与 KimiSoul**：在每次 step 前检查 token，触发压缩
2. **Retry Wrapper**：使用 tenacity 实现指数退避重试
3. **SimpleCompaction**：纯策略组件，无状态，可替换
4. **Context 状态管理**：清空 + checkpoint + 追加，保证原子性

---

### 3.4 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[消息历史] --> I2[Token 检查]
        I2 --> I3{超限判断}
    end

    subgraph Process["处理阶段"]
        P1[反向遍历分割] --> P2[格式化待压缩消息]
        P2 --> P3[LLM 生成摘要]
        P3 --> P4[过滤 ThinkPart]
    end

    subgraph Output["输出阶段"]
        O1[清空旧上下文] --> O2[新建 Checkpoint]
        O2 --> O3[写入压缩消息]
    end

    I3 -->|是| P1
    P4 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[发生错误] --> E1{错误类型}
    E1 -->|LLM 调用失败| R1{可重试?}
    E1 -->|上下文错误| R2[放弃压缩]
    E1 -->|其他错误| R3[抛出异常]

    R1 -->|是| R1A[指数退避]
    R1A --> R1B[重试压缩]
    R1 -->|否| R2

    R2 --> R2A[返回原始消息]
    R3 --> R3A[中断 Agent Loop]

    R1B --> End[继续主路径]
    R2A --> End

    style R1B fill:#90EE90
    style R2 fill:#FFD700
    style R3 fill:#FF6B6B
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant K as KimiSoul
    participant C as SimpleCompaction
    participant L as LLM
    participant X as Context

    A->>K: 触发压缩检查 (_agent_loop)
    K->>K: 估算当前 token 数
    Note over K: context.token_count + reserved >= max_context_size
    K->>K: wire_send(CompactionBegin)
    K->>C: compact(history, llm)

    C->>C: prepare(messages)
    Note over C: 保留最近 2 条 user/assistant
    C->>L: kosong.step()
    L-->>C: 返回摘要
    C->>C: 过滤 ThinkPart
    C-->>K: 压缩后消息

    K->>X: context.clear()
    K->>X: checkpoint()
    K->>X: append_message(compacted)
    K->>K: wire_send(CompactionEnd)
    K-->>A: 压缩完成
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | 消息列表 | Token 检查 + 阈值判断 | 是否需要压缩 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:341-344` ✅ Verified |
| 分割 | 消息历史 | 反向遍历，保留最近 N 条 | to_compact + to_preserve | `kimi-cli/src/kimi_cli/soul/compaction.py:82-100` ✅ Verified |
| 格式化 | 待压缩消息 | 添加序号、角色标记、提示词 | 结构化输入 | `kimi-cli/src/kimi_cli/soul/compaction.py:107-115` ✅ Verified |
| 摘要 | 格式化输入 | LLM 生成摘要 | 摘要内容 | `kimi-cli/src/kimi_cli/soul/compaction.py:54-59` ✅ Verified |
| 过滤 | 摘要结果 | 移除 ThinkPart | 纯净摘要 | `kimi-cli/src/kimi_cli/soul/compaction.py:72-73` ✅ Verified |
| 输出 | 压缩消息 | 清空 + checkpoint + 追加 | 新上下文状态 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:503-505` ✅ Verified |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始消息历史] --> I2[Token 估算]
        I2 --> I3[预算配置 reserved_context_size]
    end

    subgraph Process["处理阶段"]
        P1[反向遍历分割] --> P2[消息格式化]
        P2 --> P3[LLM 摘要生成]
        P3 --> P4[ThinkPart 过滤]
    end

    subgraph Output["输出阶段"]
        O1[Context.clear] --> O2[Checkpoint 新建]
        O2 --> O3[Append 压缩消息]
    end

    I3 --> P1
    P4 --> O1

    style Process fill:#f9f,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始] --> B{Token 超限?}
    B -->|否| C[无需压缩]
    B -->|是| D{消息数 >= N?}
    D -->|否| C
    D -->|是| E[准备压缩]
    E --> F{有待压缩消息?}
    F -->|否| G[返回保留消息]
    F -->|是| H[调用 LLM]
    H --> I{成功?}
    I -->|否| J{可重试?}
    J -->|是| H
    J -->|否| K[放弃压缩]
    I -->|是| L[应用压缩]
    C --> M[结束]
    G --> M
    K --> M
    L --> M
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:17-33
@runtime_checkable
class Compaction(Protocol):
    """压缩接口定义，支持未来扩展其他压缩策略"""
    async def compact(
        self,
        messages: Sequence[Message],
        llm: LLM
    ) -> Sequence[Message]:
        """将消息序列压缩为新的消息序列"""
        ...

# kimi-cli/src/kimi_cli/soul/compaction.py:42-44
class SimpleCompaction:
    """简单压缩实现，保留最近 N 条消息"""
    def __init__(self, max_preserved_messages: int = 2) -> None:
        self.max_preserved_messages = max_preserved_messages
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `max_preserved_messages` | `int` | 保留的最近消息数量，默认 2 |
| `compact_message` | `Message \| None` | 待压缩消息的格式化输入 |
| `to_preserve` | `Sequence[Message]` | 保留的最近 N 条消息 |

### 5.2 主链路代码

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:46-76
async def compact(
    self,
    messages: Sequence[Message],
    llm: LLM
) -> Sequence[Message]:
    """核心压缩方法"""
    compact_message, to_preserve = self.prepare(messages)
    if compact_message is None:
        return to_preserve

    # 调用 kosong.step 生成摘要
    logger.debug("Compacting context...")
    result = await kosong.step(
        chat_provider=llm.chat_provider,
        system_prompt="You are a helpful assistant that compacts conversation context.",
        toolset=EmptyToolset(),  # 压缩时不使用工具
        history=[compact_message],
    )

    # 构建压缩后消息列表
    content: list[ContentPart] = [
        system("Previous context has been compacted. Here is the compaction output:")
    ]
    compacted_msg = result.message

    # 过滤思考部分
    content.extend(part for part in compacted_msg.content if not isinstance(part, ThinkPart))
    compacted_messages: list[Message] = [Message(role="user", content=content)]
    compacted_messages.extend(to_preserve)
    return compacted_messages
```

**代码要点**：

1. **EmptyToolset 使用**：压缩时不允许工具调用，避免副作用
2. **ThinkPart 过滤**：输入输出都过滤思考内容，减少噪音和 token
3. **系统提示前缀**：明确告知模型这是压缩后的上下文
4. **简单组合**：system 提示 + 摘要 + 保留消息，结构清晰

### 5.3 关键调用链

```text
_agent_loop()                    [kimisoul.py:302]
  -> compact_context()           [kimisoul.py:480]
    -> _compact_with_retry()     [kimisoul.py:489-505]
      -> SimpleCompaction.compact()  [compaction.py:46]
        -> prepare()             [compaction.py:82]
          - 反向遍历消息
          - 计数 user/assistant
          - 分割消息范围
        -> kosong.step()         [compaction.py:54]
          - 调用 LLM 生成摘要
        - 过滤 ThinkPart         [compaction.py:72-73]
      -> context.clear()         [context.py:134]
      -> checkpoint()            [context.py:68]
      -> append_message()        [context.py:162]
```

---

## 6. 设计意图与 Trade-off

### 6.1 Kimi CLI 的选择

| 维度 | Kimi CLI 的选择 | 替代方案 | 取舍分析 |
|-----|----------------|---------|---------|
| 压缩策略 | SimpleCompaction（保留最近 N 条） | Gemini 的两阶段验证 | 简单可靠但无质量保证 |
| 保留策略 | 固定保留最近 2 条 | Codex 的渐进式截断 | 确定性高但不够灵活 |
| LLM 调用 | 单次生成，无验证 | Gemini 的生成+验证 | 成本低但可能丢失信息 |
| 失败处理 | 返回原始消息 | 强制压缩 | 安全保守但可能无法解决超限 |
| 工具处理 | 统一处理（EmptyToolset） | 独立预算 | 简单但工具输出可能被压缩 |

### 6.2 为什么这样设计？

**核心问题**：如何在保证可靠性的前提下，以最小成本实现上下文压缩？

**Kimi CLI 的解决方案**：

- 代码依据：`kimi-cli/src/kimi_cli/soul/compaction.py:42-76`
- 设计意图：通过简单的保留策略 + 单次 LLM 调用，实现确定性压缩
- 带来的好处：
  - 简单可靠：代码量少，行为可预测
  - 成本低：单次 LLM 调用，无验证开销
  - 可扩展：Protocol 设计支持未来替换策略
- 付出的代价：
  - 无质量保证：不验证摘要完整性
  - 固定保留：可能保留过多或过少
  - 无工具保护：工具输出可能被压缩

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统方案: 截断"
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "SimpleCompaction + 保留N条"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "两阶段验证压缩"
    checkout main
    branch "Codex"
    checkout "Codex"
    commit id: "渐进式截断兜底"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "Compaction + Prune"
    checkout main
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "滑动窗口"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| **Kimi CLI** | 简单保留策略 + 单次生成，无验证 | 简单场景、成本敏感、确定性需求 |
| **Gemini CLI** | 两阶段验证 + Reverse Budget | 高质量要求、工具调用频繁 |
| **Codex** | 单次生成 + 渐进式截断兜底 | 成本敏感、需要兜底机制 |
| **SWE-agent** | 无 LLM 压缩，仅滑动窗口 | 短任务、成本敏感、确定性要求高 |
| **OpenCode** | 双重机制（Compaction + Prune） | 复杂场景、需要细粒度控制 |

**详细对比**：

| 维度 | Kimi CLI | Gemini CLI | Codex | SWE-agent | OpenCode |
|-----|----------|------------|-------|-----------|----------|
| **LLM 压缩** | ✅ 有 | ✅ 有 | ✅ 有 | ❌ 无 | ✅ 有 |
| **验证机制** | ❌ 无 | ✅ 两阶段 | ❌ 无 | - | ❌ 无 |
| **保留策略** | 固定 N 条 | Reverse Budget | 渐进截断 | 滑动窗口 | Part 保护 |
| **工具保护** | ❌ 无 | ✅ 独立预算 | ❌ 无 | ❌ 无 | ✅ Skill 保护 |
| **实现复杂度** | 低 | 高 | 中 | 低 | 高 |
| **调用成本** | 低 | 高（2x） | 低 | 零 | 中 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| Token 正常 | `token_count + reserved < max_context_size` | `kimi-cli/src/kimi_cli/soul/kimisoul.py:342` ✅ Verified |
| 消息不足 | 历史消息少于 `max_preserved_messages` | `kimi-cli/src/kimi_cli/soul/compaction.py:96-97` ✅ Verified |
| 无需压缩 | 没有待压缩消息（`to_compact` 为空） | `kimi-cli/src/kimi_cli/soul/compaction.py:102-104` ✅ Verified |
| 压缩完成 | 成功生成摘要并替换上下文 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:505` ✅ Verified |

### 7.2 超时/资源限制

```python
# kimi-cli/src/kimi_cli/soul/kimisoul.py:489-495
@tenacity.retry(
    retry=retry_if_exception(self._is_retryable_error),
    before_sleep=partial(self._retry_log, "compaction"),
    wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
    stop=stop_after_attempt(self._loop_control.max_retries_per_step),
    reraise=True,
)
async def _compact_with_retry() -> Sequence[Message]:
    """带重试的压缩调用"""
```

**重试配置**：
- 初始等待：0.3 秒
- 最大等待：5 秒
- 抖动：0.5 秒
- 最大重试次数：`max_retries_per_step`（配置项）

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| API 连接错误 | 指数退避重试 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:509-517` ✅ Verified |
| API 超时 | 指数退避重试 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:510` ✅ Verified |
| 速率限制 (429) | 指数退避重试 | `kimi-cli/src/kimi_cli/soul/kimisoul.py:512` ✅ Verified |
| 消息不足 | 返回原始消息 | `kimi-cli/src/kimi_cli/soul/compaction.py:96-97` ✅ Verified |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `kimi-cli/src/kimi_cli/soul/kimisoul.py` | 480 | `compact_context()` 主入口 |
| 触发 | `kimi-cli/src/kimi_cli/soul/kimisoul.py` | 341-344 | Agent Loop 中触发压缩检查 |
| 重试 | `kimi-cli/src/kimi_cli/soul/kimisoul.py` | 489-495 | `_compact_with_retry()` 重试包装 |
| 接口 | `kimi-cli/src/kimi_cli/soul/compaction.py` | 17-33 | `Compaction` Protocol 定义 |
| 实现 | `kimi-cli/src/kimi_cli/soul/compaction.py` | 42-117 | `SimpleCompaction` 实现 |
| 核心 | `kimi-cli/src/kimi_cli/soul/compaction.py` | 46-76 | `compact()` 核心压缩方法 |
| 准备 | `kimi-cli/src/kimi_cli/soul/compaction.py` | 82-116 | `prepare()` 数据准备 |
| 手动 | `kimi-cli/src/kimi_cli/soul/slash.py` | 52-62 | `/compact` 命令处理 |
| 上下文 | `kimi-cli/src/kimi_cli/soul/context.py` | 134-160 | `clear()` 清空上下文 |
| 检查点 | `kimi-cli/src/kimi_cli/soul/context.py` | 68-78 | `checkpoint()` 新建检查点 |

---

## 9. 延伸阅读

- 前置知识：`docs/kimi-cli/07-kimi-cli-memory-context.md`
- 相关机制：`docs/kimi-cli/04-kimi-cli-agent-loop.md`
- D-Mail 机制：`docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`
- 跨项目对比：`docs/comm/comm-context-compaction.md`
- 其他项目：
  - Gemini CLI: `docs/gemini-cli/questions/gemini-cli-context-compaction.md`
  - Codex: `docs/codex/questions/codex-context-compaction.md`
  - OpenCode: `docs/opencode/questions/opencode-context-compaction.md`
  - SWE-agent: `docs/swe-agent/questions/swe-agent-context-compaction.md`

---

*✅ Verified: 基于 kimi-cli/src/kimi_cli/soul/compaction.py、kimisoul.py 等源码分析*
*⚠️ Inferred: 部分设计意图基于代码结构推断*
*基于版本：2026-02-08 | 最后更新：2026-03-03*
