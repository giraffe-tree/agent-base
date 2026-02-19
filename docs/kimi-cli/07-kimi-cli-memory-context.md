# Memory Context 管理（kimi-cli）

本文基于 `./kimi-cli` 源码，解释 Kimi CLI 如何实现 Context 管理、Checkpoint 机制和 D-Mail 时间旅行系统。

---

## 1. 先看全局（流程图）

### 1.1 Context → Checkpoint → WireFile 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│  Context 内存管理                                                 │
│  ┌────────────────────────────────────────┐                     │
│  │ Context                                │                     │
│  │  ├── _history: list[Message]           │                     │
│  │  ├── _token_count: int                 │                     │
│  │  ├── _next_checkpoint_id: int          │                     │
│  │  └── _file_backend: Path               │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  WireFile 持久化                                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ context.jsonl (JSON Lines 格式)        │                     │
│  │  ──────────────────────────────────    │                     │
│  │  {"role": "user", "content": [...]}     │                     │
│  │  {"role": "assistant", "content": [...]}│                     │
│  │  {"role": "_usage", "token_count": 123} │                     │
│  │  {"role": "_checkpoint", "id": 0}       │                     │
│  │  {"role": "user", ...}                  │                     │
│  │  {"role": "_checkpoint", "id": 1}       │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Checkpoint 机制                                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ checkpoint(add_user_message=True)      │                     │
│  │  └── 写入 {"role": "_checkpoint", "id"} │                     │
│  │                                        │                     │
│  │ revert_to(checkpoint_id)               │                     │
│  │  ├── 文件轮换: context.jsonl →         │                     │
│  │  │            context.jsonl.1          │                     │
│  │  └── 重建历史到指定 checkpoint         │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 D-Mail 时间旅行系统

```text
┌─────────────────────────────────────────────────────────────────┐
│  当前对话状态                                                     │
│  [Msg 1] [Msg 2] [Checkpoint 3] [Msg 4] [Msg 5]                  │
│                                    ↑                            │
│                               用户想修改这里                     │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  D-Mail: 发送消息到历史检查点                                     │
│  ┌────────────────────────────────────────┐                     │
│  │ send_dmail(checkpoint_id=3, message)   │                     │
│  │  ├── revert_to(3)                      │                     │
│  │  ├── 添加新消息                        │                     │
│  │  └── 继续对话                          │                     │
│  │       ↓ 创建新的时间线                 │                     │
│  │  [Msg 1] [Msg 2] [Chk 3] [NEW MSG]...  │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 JSONL 持久化和 Checkpoint 机制）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道 Context 类、Checkpoint、Compaction）。
- **10 分钟版**：通读全文（能理解 D-Mail 时间旅行和完整的上下文管理）。

### 2.1 一句话定义

Kimi CLI 的 Memory Context 采用"**JSONL 持久化 + Checkpoint 回滚 + D-Mail 时间旅行**"的设计：使用 JSON Lines 格式追加写入对话历史，通过 Checkpoint 标记实现任意点回滚，并提供 D-Mail 机制向历史检查点发送消息创建新的时间线。

---

## 3. 核心组件详解

### 3.1 Context 类

**文件**: `src/kimi_cli/soul/context.py`

```python
from __future__ import annotations
import json
from collections.abc import Sequence
from pathlib import Path
import aiofiles
from kosong.message import Message

class Context:
    def __init__(self, file_backend: Path):
        self._file_backend = file_backend
        self._history: list[Message] = []
        self._token_count: int = 0
        self._next_checkpoint_id: int = 0
        """下一个 checkpoint ID，从 0 开始递增"""

    async def restore(self) -> bool:
        """从文件恢复上下文"""
        if not self._file_backend.exists():
            return False

        async with aiofiles.open(self._file_backend, encoding="utf-8") as f:
            async for line in f:
                if not line.strip():
                    continue
                line_json = json.loads(line)

                if line_json["role"] == "_usage":
                    self._token_count = line_json["token_count"]
                elif line_json["role"] == "_checkpoint":
                    self._next_checkpoint_id = line_json["id"] + 1
                else:
                    message = Message.model_validate(line_json)
                    self._history.append(message)

        return True

    @property
    def history(self) -> Sequence[Message]:
        return self._history

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def n_checkpoints(self) -> int:
        return self._next_checkpoint_id

    async def append_message(self, message: Message | Sequence[Message]):
        """追加消息到上下文"""
        messages = [message] if isinstance(message, Message) else message
        self._history.extend(messages)

        async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
            for message in messages:
                await f.write(message.model_dump_json(exclude_none=True) + "\n")

    async def update_token_count(self, token_count: int):
        """更新 token 计数"""
        self._token_count = token_count
        async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
            await f.write(json.dumps({"role": "_usage", "token_count": token_count}) + "\n")
```

### 3.2 Checkpoint 机制

**文件**: `src/kimi_cli/soul/context.py:68-133`

```python
async def checkpoint(self, add_user_message: bool):
    """创建检查点"""
    checkpoint_id = self._next_checkpoint_id
    self._next_checkpoint_id += 1
    logger.debug("Checkpointing, ID: {id}", id=checkpoint_id)

    # 写入 checkpoint 标记
    async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
        await f.write(json.dumps({"role": "_checkpoint", "id": checkpoint_id}) + "\n")

    # 可选：添加用户可见的 checkpoint 消息
    if add_user_message:
        await self.append_message(
            Message(role="user", content=[system(f"CHECKPOINT {checkpoint_id}")])
        )

async def revert_to(self, checkpoint_id: int):
    """回滚到指定检查点"""
    if checkpoint_id >= self._next_checkpoint_id:
        raise ValueError(f"Checkpoint {checkpoint_id} does not exist")

    # 1. 文件轮换
    rotated_file_path = await next_available_rotation(self._file_backend)
    await aiofiles.os.replace(self._file_backend, rotated_file_path)

    # 2. 重建历史
    self._history.clear()
    self._token_count = 0
    self._next_checkpoint_id = 0

    async with (
        aiofiles.open(rotated_file_path, encoding="utf-8") as old_file,
        aiofiles.open(self._file_backend, "w", encoding="utf-8") as new_file,
    ):
        async for line in old_file:
            line_json = json.loads(line)

            # 在目标 checkpoint 处停止
            if line_json["role"] == "_checkpoint" and line_json["id"] == checkpoint_id:
                break

            await new_file.write(line)
            # 恢复内存状态
            if line_json["role"] == "_usage":
                self._token_count = line_json["token_count"]
            elif line_json["_checkpoint"]:
                self._next_checkpoint_id = line_json["id"] + 1
            else:
                self._history.append(Message.model_validate(line_json))
```

### 3.3 文件轮换机制

**文件**: `src/kimi_cli/utils/path.py`

```python
async def next_available_rotation(base_path: Path) -> Path | None:
    """
    获取下一个可用的轮换文件路径
    例如: context.jsonl -> context.jsonl.1 -> context.jsonl.2
    """
    directory = base_path.parent
    stem = base_path.name

    # 找到下一个可用的编号
    rotation_number = 1
    while True:
        rotated = directory / f"{stem}.{rotation_number}"
        if not rotated.exists():
            return rotated
        rotation_number += 1

        # 防止无限循环
        if rotation_number > 1000:
            return None
```

---

## 4. Context Compaction (上下文压缩)

### 4.1 SimpleCompaction 实现

**文件**: `src/kimi_cli/soul/compaction.py`

```python
from __future__ import annotations
from collections.abc import Sequence
from kosong.message import Message
from kimi_cli.llm import LLM

class SimpleCompaction:
    """简单的上下文压缩实现"""

    def __init__(self, max_preserved_messages: int = 2) -> None:
        self.max_preserved_messages = max_preserved_messages

    async def compact(
        self,
        messages: Sequence[Message],
        llm: LLM
    ) -> Sequence[Message]:
        compact_message, to_preserve = self.prepare(messages)
        if compact_message is None:
            return to_preserve

        # 调用 LLM 进行压缩
        logger.debug("Compacting context...")
        result = await kosong.step(
            chat_provider=llm.chat_provider,
            system_prompt="You are a helpful assistant that compacts conversation context.",
            toolset=EmptyToolset(),
            history=[compact_message],
        )

        # 构建压缩后的消息
        content = [
            system("Previous context has been compacted. Here is the compaction output:")
        ]
        content.extend(
            part for part in result.message.content
            if not isinstance(part, ThinkPart)  # 过滤思考内容
        )

        compacted_messages: list[Message] = [
            Message(role="user", content=content)
        ]
        compacted_messages.extend(to_preserve)
        return compacted_messages

    def prepare(self, messages: Sequence[Message]) -> PrepareResult:
        """准备压缩：分离需要压缩和保留的消息"""
        if not messages or self.max_preserved_messages <= 0:
            return self.PrepareResult(None, messages)

        history = list(messages)
        preserve_start_index = len(history)
        n_preserved = 0

        # 从后向前遍历，保留最近的用户/助手消息
        for index in range(len(history) - 1, -1, -1):
            if history[index].role in {"user", "assistant"}:
                n_preserved += 1
                if n_preserved == self.max_preserved_messages:
                    preserve_start_index = index
                    break

        if n_preserved < self.max_preserved_messages:
            return self.PrepareResult(None, messages)

        to_compact = history[:preserve_start_index]
        to_preserve = history[preserve_start_index:]

        # 构建压缩输入消息
        compact_message = Message(role="user", content=[])
        for i, msg in enumerate(to_compact):
            compact_message.content.append(
                TextPart(text=f"## Message {i + 1}\nRole: {msg.role}\nContent:\n")
            )
            compact_message.content.extend(
                part for part in msg.content if not isinstance(part, ThinkPart)
            )
        compact_message.content.append(TextPart(text="\n" + prompts.COMPACT))

        return self.PrepareResult(compact_message, to_preserve)
```

### 4.2 压缩提示词

```python
# src/kimi_cli/prompts.py
COMPACT = """\
Please summarize the above conversation history into a concise summary.
Retain the key information, decisions, and context that would be needed
to continue the conversation effectively.
"""
```

---

## 5. D-Mail 时间旅行系统

### 5.1 D-Mail 概念

D-Mail (DeLorean Mail) 是 Kimi CLI 的创新功能，允许用户"向过去发送消息"：

```python
async def send_dmail(
    context: Context,
    checkpoint_id: int,
    message: Message,
    llm: LLM
) -> None:
    """
    向历史检查点发送消息（D-Mail）

    这会：
    1. 回滚到指定的 checkpoint
    2. 添加新消息
    3. 继续对话，创建新的时间线
    """
    # 1. 回滚到检查点
    await context.revert_to(checkpoint_id)

    # 2. 添加新消息
    await context.append_message(message)

    # 3. 触发 AI 响应，创建新的时间线
    response = await llm.chat(context.history)
    await context.append_message(response)
```

### 5.2 使用场景

| 场景 | D-Mail 用法 |
|------|------------|
| 修正之前的错误 | `send_dmail(checkpoint_id=3, "请忽略之前的方案，改用...")` |
| 基于旧版本继续 | `send_dmail(checkpoint_id=5, "在这个版本基础上添加...")` |
| 对比不同方案 | 从同一 checkpoint 创建多个分支 |

---

## 6. 与 Agent Loop 的集成

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                     │
│  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ User Input  │──▶│ Context.append_msg()│──▶│ 写入 context.jsonl  │  │
│  └─────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                                              │       │
│                                                              ▼       │
│  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ LLM Response│◀─│  Context.history    │◀─│ 从 JSONL 读取历史   │  │
│  └─────────────┘  └─────────────────────┘  └─────────────────────┘  │
│         │                                                             │
│         ▼                                                             │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Token Check                                                      │  │
│  │ if token_count > threshold:                                      │  │
│  │    SimpleCompaction.compact()                                   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ User: "/checkpoint" or "/revert 3"                              │  │
│  │   └── Context.checkpoint() / Context.revert_to()                │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 7. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| 历史丢失 | 检查 `restore()` 的 JSON 解析 | `soul/context.py:24` |
| Checkpoint 失败 | 检查文件权限和轮换 | `utils/path.py` |
| 回滚失败 | 确认 checkpoint_id 存在 | `soul/context.py:80` |
| 压缩失败 | 检查 LLM 调用和提示词 | `soul/compaction.py:46` |
| Token 计数不准 | 检查 `_usage` 消息写入 | `soul/context.py:171` |

---

## 8. 架构特点总结

- **JSONL 追加写入**: 高效、可恢复的持久化格式
- **Checkpoint 标记**: 轻量级的历史标记和回滚机制
- **文件轮换**: 优雅的备份策略，保留历史版本
- **LLM 驱动压缩**: 使用模型智能压缩旧上下文
- **D-Mail 时间旅行**: 创新的向历史发送消息功能
- **内存+文件同步**: 内存中的 `_history` 与 JSONL 文件保持同步
