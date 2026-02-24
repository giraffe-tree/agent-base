# Codex 上下文压缩机制

## 是否支持

✅ **完整支持** - Codex 实现了完整的 LLM 驱动上下文压缩机制，包含渐进式截断作为兜底策略。

## 核心设计

**双层策略**: LLM 智能摘要生成 → 渐进式历史消息移除，优先保留最近用户交互的完整性。

## 关键代码位置

| 文件路径 | 职责 | 行号 |
|---------|------|------|
| `codex/codex-rs/core/src/compact.rs` | 压缩主逻辑，协调摘要生成与历史管理 | 1-300 |
| `codex/codex-rs/core/src/compact.rs` | `run_compact_task_inner()` 核心函数 | 127+ |
| `codex/codex-rs/core/src/compact.rs` | `run_inline_auto_compact_task()` | 91-105 |
| `codex/codex-rs/core/templates/compact/prompt.md` | 压缩提示词模板 | ✅ 已验证 |
| `codex/codex-rs/core/templates/compact/summary_prefix.md` | 摘要前缀模板 | ✅ 已验证 |

## 压缩流程

```
触发条件
    │
    ├─► Token 超过阈值 (默认 80% 上下文窗口)
    ├─► 用户主动命令 /compact
    └─► 系统检测到性能下降
    │
    ▼
┌─────────────────┐
│  LLM 摘要生成   │──► 调用模型生成对话摘要
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  历史消息处理   │──► 保留最近 N 条消息
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  渐进式截断     │──► 兜底：移除最旧消息
└─────────────────┘
    │
    ▼
持久化存储 (JSONL)
```

### 触发条件

1. **Token 阈值触发**: 当对话 token 数超过上下文窗口的 80%（可配置）
2. **用户命令**: `/compact` 命令强制触发
3. **模型切换**: 切换模型时自动清理不兼容的历史格式

## 实现细节

### 1. 摘要生成策略

Codex 使用专门的压缩提示词 (`templates/compact/prompt.md`):

```markdown
# System Prompt for Context Compaction

请总结以下对话的关键信息，保留：
1. 用户的原始需求和目标
2. 已确认的技术决策和代码变更
3. 未完成的待办事项
4. 相关的文件路径和代码结构

输出格式要求：
- 使用第三人称客观描述
- 保留具体的文件路径和代码片段
- 标注待办事项的优先级
```

### 2. 消息保留策略

```rust
// codex/codex-rs/core/src/compact.rs:150
const PRESERVE_RECENT_MESSAGES: usize = 2;
const PRESERVE_USER_MESSAGES: bool = true;

fn should_preserve_message(msg: &Message, index: usize, total: usize) -> bool {
    // 保留最近 N 条消息
    if index >= total - PRESERVE_RECENT_MESSAGES {
        return true;
    }
    // 保留用户消息（避免打断交互流）
    if PRESERVE_USER_MESSAGES && msg.role == Role::User {
        return true;
    }
    false
}
```

### 3. Model-Switch 消息处理

```rust
// codex/codex-rs/core/src/compact.rs:180
fn separate_model_switch_messages(messages: &[Message]) -> (Vec<Message>, Vec<Message>) {
    messages.iter().cloned().partition(|msg| {
        matches!(msg.metadata.as_ref().map(|m| m.message_type),
            Some(MessageType::ModelSwitch))
    })
}
```

切换模型时产生的元数据消息会被单独处理，确保模型切换历史不被压缩丢失。

### 4. 持久化格式

压缩后的内容以 `CompactedItem` 结构存储:

```rust
// codex/codex-rs/core/src/conversation.rs:45
#[derive(Serialize, Deserialize)]
pub struct CompactedItem {
    pub id: String,
    pub created_at: DateTime<Utc>,
    pub summary: String,           // LLM 生成的摘要
    pub original_token_count: usize,
    pub compacted_token_count: usize,
    pub preserved_message_ids: Vec<String>,
    pub metadata: CompactionMetadata,
}
```

存储格式为 JSONL，便于追加和读取：
```
.conversation/{session_id}/compacted.jsonl
```

### 5. 上下文超限处理（兜底策略）

当压缩过程中遇到上下文窗口超限时，启用渐进式截断:

```rust
// codex/codex-rs/core/src/compact.rs:148-165
let mut truncated_count = 0usize;
let max_retries = turn_context.provider.stream_max_retries();

loop {
    // 尝试生成摘要
    match drain_to_completed(&sess, turn_context.as_ref(), ...).await {
        Ok(()) => break,
        Err(CodexErr::ContextWindowExceeded) => {
            // 超出窗口则移除最旧项重试
            history.remove_first_item();
            truncated_count += 1;
            if truncated_count > max_retries {
                return Err(CodexErr::ContextWindowExceeded);
            }
            continue;
        }
        Err(e) => return Err(e),
    }
}
```

**关键机制**：
- 通过 `history.remove_first_item()` 移除最旧的历史项
- 每次移除后重试，直到成功或达到最大重试次数
- 保留最近的用户消息和关键上下文

## 设计权衡

### 优点

| 优势 | 说明 |
|------|------|
| **智能压缩** | LLM 生成摘要保留语义完整性，优于简单截断 |
| **用户感知友好** | 保留最近用户消息，交互连续性不受破坏 |
| **可恢复性** | JSONL 持久化支持历史状态回溯 |
| **兜底机制** | 渐进式截断确保极端情况下系统不崩溃 |

### 缺点

| 劣势 | 说明 |
|------|------|
| **额外成本** | 压缩本身需要调用 LLM，产生 token 成本 |
| **延迟增加** | 压缩过程可能耗时数秒，阻塞新请求 |
| **信息损失** | 摘要必然丢失部分细节，复杂上下文可能受损 |
| **实现复杂** | 需要维护 CompactedItem 状态和兼容性 |

### 适用场景

- ✅ 长会话场景（>50 轮对话）
- ✅ 复杂多文件修改任务
- ✅ 需要历史回溯的企业场景
- ⚠️ 短任务可能因压缩成本不划算
