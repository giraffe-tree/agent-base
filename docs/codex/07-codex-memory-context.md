# Memory Context 管理（codex）

本文基于 `./codex/codex-rs` 源码，解释 codex 如何实现对话历史（History）和上下文（Context）的管理，包括 Token 估算、上下文压缩和记忆持久化。

---

## 1. 先看全局（流程图）

### 1.1 ContextManager → History → Prompt 流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  用户输入 / 工具输出                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ ResponseInputItem::Message { role:     │                     │
│  │   "user", content: [...] }             │                     │
│  │ ResponseInputItem::FunctionCallOutput  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ContextManager 管理历史                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ ContextManager::record_items()         │                     │
│  │  ├── items: Vec<ResponseItem>          │                     │
│  │  ├── token_info: TokenUsageInfo        │                     │
│  │  └── process_item() 应用截断策略      │                     │
│  │       └── truncate_text() /            │                     │
│  │           truncate_function_output_    │                     │
│  │           items_with_policy()          │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Prompt 构建时规范化处理                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ ContextManager::for_prompt()           │                     │
│  │  └── normalize_history()               │                     │
│  │       ├── ensure_call_outputs_present  │                     │
│  │       ├── remove_orphan_outputs        │                     │
│  │       └── strip_images_when_unsupported│                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 上下文压缩流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  Token 超出阈值检测                                                │
│  ┌────────────────────────────────────────┐                     │
│  │ estimate_token_count() > threshold     │                     │
│  │   └── 触发上下文压缩                  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Context Compaction 执行                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ run_compact_task_inner()               │                     │
│  │  ├── 创建 Compaction Turn              │                     │
│  │  ├── 发送历史到模型进行总结           │                     │
│  │  │   └── SUMMARIZATION_PROMPT         │                     │
│  │  └── 生成 Compaction 项替换旧历史     │                     │
│  │       └── ResponseItem::Compaction    │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  压缩后历史结构                                                   │
│  ┌────────────────────────────────────────┐                     │
│  │ [Compaction] 总结内容                  │                     │
│  │ [Message] 最近的用户消息               │                     │
│  │ [FunctionCallOutput] 最近的工具输出    │                     │
│  │ ...                                    │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `3.1`（知道 ContextManager 存储历史和 Prompt 构建流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道 Token 管理、压缩机制和持久化）。
- **10 分钟版**：通读全文（能定位上下文相关问题，理解压缩策略）。

### 2.1 一句话定义

codex 的 Memory Context 采用"**双层存储 + 惰性压缩**"的设计：使用 `ContextManager` 在内存中维护完整的对话历史，通过字节启发式算法估算 Token 使用量，当接近上下文窗口限制时触发 LLM 驱动的上下文压缩，将旧历史转换为摘要形式。

---

## 3. 核心组件详解

### 3.1 ContextManager 结构

**文件**: `core/src/context_manager/history.rs:25-49`

```rust
#[derive(Debug, Clone, Default)]
pub(crate) struct ContextManager {
    /// The oldest items are at the beginning of the vector.
    items: Vec<ResponseItem>,
    token_info: Option<TokenUsageInfo>,
}

impl ContextManager {
    pub(crate) fn new() -> Self {
        Self {
            items: Vec::new(),
            token_info: TokenUsageInfo::new_or_append(&None, &None, None),
        }
    }
}
```

**关键方法**:

| 方法 | 职责 |
|------|------|
| `record_items()` | 记录新的对话项，应用截断策略 |
| `for_prompt()` | 构建发送给模型的 Prompt，执行规范化 |
| `estimate_token_count()` | 估算当前历史的 Token 使用量 |
| `remove_first_item()` | 移除最旧的项（用于压缩） |
| `drop_last_n_user_turns()` | 回滚最近 N 个用户回合 |

### 3.2 历史规范化 (Normalize)

**文件**: `core/src/context_manager/normalize.rs`

在构建 Prompt 前，codex 会执行三项规范化：

```rust
fn normalize_history(&mut self, input_modalities: &[InputModality]) {
    // 1. 确保每个 function call 都有对应的 output
    normalize::ensure_call_outputs_present(&mut self.items);

    // 2. 移除孤立的 outputs（没有对应 call 的）
    normalize::remove_orphan_outputs(&mut self.items);

    // 3. 当模型不支持图片时，从消息中移除图片
    normalize::strip_images_when_unsupported(input_modalities, &mut self.items);
}
```

**不变性保证**:
- 每个 function call 必须有对应的 output
- 每个 output 必须有对应的 function call
- 图片内容仅在模型支持时保留

### 3.3 Token 估算机制

**文件**: `core/src/context_manager/history.rs:398-417`

codex 使用字节启发式算法估算 Token 数量：

```rust
pub(crate) fn estimate_token_count(
    &self,
    base_instructions: &BaseInstructions,
) -> Option<i64> {
    // 基础指令 Token（系统提示）
    let base_tokens =
        i64::try_from(approx_token_count(&base_instructions.text))
            .unwrap_or(i64::MAX);

    // 历史项 Token
    let items_tokens = self
        .items
        .iter()
        .map(estimate_item_token_count)
        .fold(0i64, i64::saturating_add);

    Some(base_tokens.saturating_add(items_tokens))
}

fn estimate_item_token_count(item: &ResponseItem) -> i64 {
    let model_visible_bytes = estimate_response_item_model_visible_bytes(item);
    approx_tokens_from_byte_count_i64(model_visible_bytes)
}
```

**估算策略**:
- 普通项：使用 JSON 序列化后的字节数
- Reasoning/Compaction 项：使用加密内容的长度（base64 解码后估算）
- 转换系数：字节数 → Token 数的近似转换

---

## 4. 上下文压缩 (Context Compaction)

### 4.1 压缩触发条件

当满足以下条件时触发压缩：
- Token 估算值接近模型上下文窗口限制
- 用户显式请求压缩（`/compact` 命令）

### 4.2 压缩执行流程

**文件**: `core/src/compact.rs:64-120`

```rust
pub(crate) async fn run_compact_task_inner(
    sess: Arc<Session>,
    turn_context: Arc<TurnContext>,
    input: Vec<UserInput>,
) -> CodexResult<()> {
    // 1. 克隆当前历史
    let mut history = sess.clone_history().await;

    // 2. 记录压缩提示词
    history.record_items(&[initial_input_for_turn.into()], policy);

    loop {
        // 3. 构建压缩 Prompt
        let turn_input = history.for_prompt(&turn_context.model_info.input_modalities);
        let prompt = Prompt {
            input: turn_input,
            base_instructions: sess.get_base_instructions().await,
            ..Default::default()
        };

        // 4. 发送给模型生成摘要
        match drain_to_completed(&sess, turn_context.as_ref(), &mut client_session, ...).await {
            Ok(()) => break,
            Err(CodexErr::ContextWindowExceeded) => {
                // 超出窗口则移除最旧项重试
                history.remove_first_item();
                continue;
            }
            Err(e) => return Err(e),
        }
    }
}
```

### 4.3 压缩提示词

**文件**: `core/src/templates/compact/prompt.md`

```markdown
请总结以下对话历史，保留关键信息：
- 用户的主要意图和目标
- 已执行的操作和结果
- 当前的代码状态
- 待解决的问题

请用简洁的语言总结，作为后续对话的上下文。
```

---

## 5. 持久化存储

### 5.1 历史持久化

对话历史通过 `RolloutRecorder` 以 JSON Lines 格式存储：

```
~/.codex/rollouts/
└── {session_id}.jsonl
```

**RolloutItem 结构**（`protocol/src/protocol.rs`）：
```rust
pub struct RolloutItem {
    pub id: String,
    pub item: TurnItem,  // UserMessage, AssistantMessage, FunctionCall 等
    pub timestamp: DateTime<Utc>,
}
```

**格式示例**:
```jsonl
{"id":"msg_001","item":{"type":"user_message","content":"Hello"},"timestamp":"2024-01-20T10:00:00Z"}
{"id":"msg_002","item":{"type":"assistant_message","content":"Hi!"},"timestamp":"2024-01-20T10:00:01Z"}
{"id":"call_001","item":{"type":"function_call","name":"shell","arguments":"{\"cmd\": \"ls\"}"},"timestamp":"2024-01-20T10:00:02Z"}
```

**恢复机制**：启动时通过 `resume_from_rollout()` 读取并重建历史。

### 5.2 记忆系统 (Memory Trace)

**文件**: `core/src/memory_trace.rs`

codex 实现了基于 Trace 文件的记忆构建系统：

```rust
// core/src/memory_trace.rs:16-21
pub struct BuiltMemory {
    pub memory_id: String,
    pub source_path: PathBuf,
    pub raw_memory: String,
    pub memory_summary: String,
}
```

**功能**：从 trace 文件加载原始记录，使用 LLM 生成记忆摘要。

| 阶段 | 处理方式 | 用途 |
|------|---------|------|
| Trace 加载 | 从文件系统读取原始 trace | 获取会话历史 |
| 摘要生成 | 调用模型生成结构化摘要 | 构建长期记忆 |
| 存储 | 持久化到指定路径 | 跨会话复用 |

**构建流程**：
```rust
// core/src/memory_trace.rs:36-50
pub async fn build_memories_from_trace_files(
    client: &ModelClient,
    trace_paths: &[PathBuf],
    model_info: &ModelInfo,
    effort: Option<ReasoningEffortConfig>,
    otel_manager: &OtelManager,
) -> Result<Vec<BuiltMemory>> {
    // 1. 准备 trace 文件
    // 2. 调用模型生成摘要
    // 3. 返回 BuiltMemory 列表
}
```

**模板支持**：
- `templates/memories/stage_one_system.md` - 记忆生成系统提示
- `templates/memories/stage_one_input.md` - 输入格式化模板
- `templates/memories/consolidation.md` - 记忆合并模板

---

## 6. Token 管理策略

### 6.1 截断策略 (TruncationPolicy)

**文件**: `core/src/truncate.rs`

```rust
pub struct TruncationPolicy {
    pub max_output_bytes: usize,
    pub max_item_bytes: usize,
}

impl TruncationPolicy {
    pub fn truncate_text(text: &str, max_bytes: usize) -> String {
        // 保留开头和结尾，中间用 ... 省略
        if text.len() <= max_bytes {
            text.to_string()
        } else {
            let head_len = max_bytes / 2;
            let tail_len = max_bytes - head_len - 3;
            format!("{}...{}", &text[..head_len], &text[text.len()-tail_len..])
        }
    }
}
```

### 6.2 上下文窗口管理

| 场景 | 处理方式 |
|------|---------|
| 正常情况 | 完整历史发送到模型 |
| Token 接近上限 | 触发上下文压缩 |
| 压缩后仍超出 | 移除最旧的历史项 |
| 单条消息过长 | 应用截断策略 |

---

## 7. 与 Agent Loop 的集成

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                     │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │  run_turn() │──▶│  ContextManager │──▶│  Prompt::for_prompt()   │  │
│  └─────────────┘  └─────────────────┘  └─────────────────────────┘  │
│         │                  │                        │               │
│         │                  │                        ▼               │
│         │                  │           ┌───────────────────────┐    │
│         │                  │           │ normalize_history()   │    │
│         │                  │           │ - 确保 call/output 配对│    │
│         │                  │           │ - 移除孤立项          │    │
│         │                  │           │ - 图片处理            │    │
│         │                  │           └───────────────────────┘    │
│         │                  │                        │               │
│         ▼                  ▼                        ▼               │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  Token 检查                                                     │ │
│  │  if estimate_token_count() > threshold {                        │ │
│  │      trigger_compaction();                                      │ │
│  │  }                                                              │ │
│  └───────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 8. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| 上下文丢失 | 检查 `normalize_history()` 的过滤逻辑 | `context_manager/normalize.rs` |
| Token 估算不准 | 查看 `approx_token_count()` 实现 | `truncate.rs` |
| 压缩失败 | 检查 `run_compact_task_inner()` 的重试逻辑 | `compact.rs` |
| 历史未持久化 | 检查 `message_history.jsonl` 文件 | `session/persistence.rs` |
| 图片丢失 | 检查 `strip_images_when_unsupported()` | `context_manager/normalize.rs` |

---

## 9. 架构特点总结

- **双层存储**: 内存中的 `ContextManager` + 磁盘上的 JSONL 持久化
- **字节启发式 Token 估算**: 快速但不精确的 Token 使用量估算
- **惰性压缩**: 仅在接近上下文限制时触发压缩，而非实时压缩
- **不变性保证**: 通过规范化确保 function call/output 配对完整性
- **渐进式截断**: 从压缩到移除旧项的渐进式上下文缩减策略
