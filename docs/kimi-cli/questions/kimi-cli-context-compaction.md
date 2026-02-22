# Kimi CLI 上下文压缩机制

## 是否支持

✅ **支持显式压缩** - Kimi CLI 实现了显式的上下文压缩机制，与 Checkpoint 系统深度集成，支持向历史检查点发送消息的 D-Mail 功能。

## 核心设计

**协议化设计 + Checkpoint 集成**: 通过 `Compaction` 协议定义压缩行为，压缩后重建 checkpoint 锚点；支持 D-Mail 机制向历史状态发送消息。

## 关键代码位置

| 文件路径 | 职责 |
|---------|------|
| `kimi-cli/src/kimi_cli/soul/compaction.py` | 压缩核心实现 |
| `kimi-cli/src/kimi_cli/soul/compaction.py:45-90` | `Compaction` 协议接口定义 |
| `kimi-cli/src/kimi_cli/soul/compaction.py:120-180` | `compact_conversation()` 主流程 |
| `kimi-cli/src/kimi_cli/soul/compaction.py:200-260` | `generate_structured_summary()` 结构化摘要 |
| `kimi-cli/src/kimi_cli/checkpoint/manager.py:300-350` | 压缩后 checkpoint 重建 |
| `kimi-cli/src/kimi_cli/prompts/compact.md` | 压缩提示词模板（XML 格式） |

## 压缩流程

```
触发条件
    │
    ├─► Token 超过上下文 75%
    ├─► 用户命令 /compact
    └─► Checkpoint 创建时自动评估
    │
    ▼
┌─────────────────┐
│  Select         │──► 默认保留最近 2 条
│  Preserve       │    user/assistant 对
│  Strategy       │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Structured     │──► 生成 XML 格式摘要
│  Summary Gen    │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Checkpoint     │──► 重建 checkpoint 锚点
│  Rebuild        │    指向压缩后状态
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  D-Mail Ready   │──► 启用向历史检查点
└─────────────────┘      发送消息
```

## 实现细节

### 1. Compaction 协议接口

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:45
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@runtime_checkable
class Compaction(Protocol):
    """上下文压缩协议"""

    async def compact(
        self,
        messages: list[Message],
        preserve_last_n: int = 2,
        **options
    ) -> CompactionResult:
        """执行压缩，返回压缩结果"""
        ...

    def should_compact(self, context: ConversationContext) -> bool:
        """判断是否需要压缩"""
        ...

    def get_preserved_ranges(self, messages: list[Message]) -> list[Range]:
        """获取需要保留的消息范围"""
        ...


@dataclass
class CompactionResult:
    success: bool
    summary: str
    original_count: int
    compacted_count: int
    preserved_ids: list[str]
    checkpoint_ref: str | None  # 关联的 checkpoint
```

### 2. 保留策略

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:120
class DefaultCompactionStrategy:
    """默认压缩策略：保留最近 N 条对话"""

    PRESERVE_LAST_N = 2  # 保留最近 2 条 user/assistant

    def get_preserved_ranges(self, messages: list[Message]) -> list[Range]:
        """确定需要保留的消息范围"""
        preserved = []

        # 从后向前找连续的 user/assistant 对
        count = 0
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.role in ("user", "assistant"):
                preserved.append(i)
                count += 1
                if count >= self.PRESERVE_LAST_N * 2:
                    break

        return [Range(min(preserved), max(preserved))] if preserved else []

    def should_compact(self, context: ConversationContext) -> bool:
        """触发条件判断"""
        token_ratio = context.current_tokens / context.max_tokens
        return token_ratio > 0.75  # 75% 阈值
```

### 3. 结构化 XML 摘要

Kimi CLI 使用 XML 格式输出结构化摘要：

```markdown
<!-- kimi-cli/src/kimi_cli/prompts/compact.md -->

你是一个对话摘要助手。请将以下对话总结为结构化的 XML 格式：

<compact>
  <goal>
    用户的核心目标和需求
  </goal>

  <context>
    <files>
      涉及的文件列表及当前状态
    </files>
    <decisions>
      已做出的技术决策
    </decisions>
  </context>

  <progress>
    <completed>
      已完成的任务
    </completed>
    <in_progress>
      进行中的任务
    </in_progress>
    <pending>
      待办事项（保留原格式）
    </pending>
  </progress>

  <notes>
    其他需要注意的信息
  </notes>
</compact>

规则：
1. 保留所有文件路径的完整性
2. 待办事项使用 - [ ] 格式保留
3. 代码片段使用 ```language 格式
```

### 4. Checkpoint 集成

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:200
async def compact_with_checkpoint(
    self,
    messages: list[Message],
    checkpoint_manager: CheckpointManager
) -> CompactionResult:
    """压缩并重建 checkpoint"""

    # 1. 执行压缩
    result = await self.compact(messages)

    if not result.success:
        return result

    # 2. 创建压缩后的 checkpoint
    compacted_checkpoint = await checkpoint_manager.create_checkpoint(
        messages=result.compacted_messages,
        metadata={
            "type": "post_compaction",
            "original_count": result.original_count,
            "compaction_summary": result.summary,
            "preserved_message_ids": result.preserved_ids,
        }
    )

    # 3. 更新 checkpoint 链
    await checkpoint_manager.link_checkpoint(
        from_id=result.original_checkpoint_id,
        to_id=compacted_checkpoint.id,
        link_type="compaction"
    )

    result.checkpoint_ref = compacted_checkpoint.id
    return result
```

### 5. D-Mail 支持

D-Mail（Divergence Mail）允许向历史检查点发送消息：

```python
# kimi-cli/src/kimi_cli/checkpoint/manager.py:320
class CheckpointManager:
    async def send_dmail(
        self,
        target_checkpoint_id: str,
        message: Message,
        compaction_context: CompactionResult | None = None
    ) -> DMAResult:
        """
        向历史检查点发送消息（D-Mail）

        如果目标检查点已被压缩，需要：
        1. 解析 compaction 摘要
        2. 判断消息相关性
        3. 决定是追加到摘要还是展开历史
        """
        target = await self.get_checkpoint(target_checkpoint_id)

        if target.metadata.get("type") == "post_compaction":
            # 目标已被压缩
            summary = target.metadata["compaction_summary"]

            # 判断消息是否涉及压缩前历史
            if self._message_requires_history(message, summary):
                # 需要展开部分历史
                expanded = await self._expand_compacted_range(
                    target, message
                )
                return DMAResult(
                    action="expanded",
                    new_context=expanded
                )

        # 直接追加消息
        return DMAResult(
            action="appended",
            checkpoint_id=target_checkpoint_id
        )
```

### 6. 压缩状态展示

```python
# kimi-cli/src/kimi_cli/soul/compaction.py:260
def format_compaction_report(result: CompactionResult) -> str:
    """生成压缩报告"""
    lines = [
        "🗜️  上下文已压缩",
        f"   原消息数: {result.original_count}",
        f"   压缩后: {result.compacted_count} 条消息 + 摘要",
        f"   压缩率: {(1 - result.compacted_count/result.original_count)*100:.1f}%",
        "",
        "保留内容：",
    ]

    for msg_id in result.preserved_ids[:5]:
        lines.append(f"   - {msg_id}")

    if len(result.preserved_ids) > 5:
        lines.append(f"   ... 还有 {len(result.preserved_ids) - 5} 条")

    if result.checkpoint_ref:
        lines.append(f"\n📍 Checkpoint: {result.checkpoint_ref}")

    return "\n".join(lines)
```

## 设计权衡

### 优点

| 优势 | 说明 |
|------|------|
| **协议化扩展** | Compaction 协议支持自定义压缩策略 |
| **状态可追溯** | 与 Checkpoint 集成，压缩后可回溯 |
| **D-Mail 创新** | 可向历史检查点发送消息，支持时间线修正 |
| **结构化输出** | XML 格式便于下游解析和处理 |

### 缺点

| 劣势 | 说明 |
|------|------|
| **复杂度较高** | Checkpoint + Compaction + D-Mail 三者交织 |
| **存储开销** | 需要保存压缩前后两份数据 |
| **D-Mail 成本** | 展开压缩历史可能触发新的压缩 |
| **XML 解析** | 需要可靠的 XML 解析和错误处理 |

### 与其他 Agent 对比

| 维度 | Kimi CLI | Codex | OpenCode |
|------|----------|-------|----------|
| **保留策略** | 固定 N 条 | 智能选择 | 双重机制 |
| **持久化** | Checkpoint 集成 | JSONL 文件 | 内存状态 |
| **时间线操作** | D-Mail 支持 | 无 | 无 |
| **输出格式** | XML | 纯文本 | 结构化对象 |

### 适用场景

- ✅ 需要 checkpoint 回溯的复杂任务
- ✅ 需要向历史状态发送指令的场景
- ✅ 多轮迭代需要保留关键里程碑
- ⚠️ 简单任务可能过度设计
