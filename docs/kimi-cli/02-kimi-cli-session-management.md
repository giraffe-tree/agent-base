# Session 管理（kimi-cli）

本文基于 `./kimi-cli` 源码，解释 kimi-cli 如何实现 session 生命周期管理、基于 checkpoint 的时间旅行、context 压缩、以及 D-Mail 消息传递系统。
为适配"先看全貌再看细节"的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 Session 生命周期流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 创建或恢复 Session                                       │
│  ┌─────────────────┐                                            │
│  │ Session.create()│ 或 │ Session.continue_()                   │
│  │ 生成 UUID       │      │ 获取最近会话                        │
│  └────────┬────────┘      │ 或按 ID 查找                        │
└───────────┼───────────────┼─────────────────────────────────────┘
            │               │
            ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│  INITIALIZE: Session 初始化                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ Session 数据结构                       │                     │
│  │  ├── id: UUID                          │                     │
│  │  ├── work_dir: 工作目录                │                     │
│  │  ├── context_file: context.jsonl       │                     │
│  │  ├── wire_file: wire.jsonl             │                     │
│  │  └── title: 从首条输入派生             │                     │
│  │                                      │                     │
│  │ Context 初始化                        │                     │
│  │  ├── 加载/创建 context.jsonl          │                     │
│  │  ├── _history: [] (内存缓存)          │                     │
│  │  └── _next_checkpoint_id: 0           │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  AGENT LOOP: 对话执行与 Checkpoint 管理                           │
│  ┌────────────────────────────────────────┐                     │
│  │ KimiSoul._turn()                       │                     │
│  │  ├── _checkpoint() ◄─────────────────┐ │                     │
│  │  │   ├── 生成 checkpoint_id          │ │                     │
│  │  │   └── 写入 "_checkpoint" 标记     │ │                     │
│  │  ├── append_message(user_msg)         │ │                     │
│  │  └── _agent_loop()                    │ │                     │
│  │       ├── llm.chat()                  │ │                     │
│  │       ├── 处理工具调用                │ │                     │
│  │       ├── 处理 approval               │ │                     │
│  │       └── 捕获 BackToTheFuture        │ │                     │
│  │  ◄── 循环直到对话结束或 checkpoint 跳转                          │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  TIME TRAVEL: Checkpoint 跳转与 D-Mail                           │
│  ┌────────────────────────────────────────┐                     │
│  │ revert_to(checkpoint_id)               │                     │
│  │  ├── 文件轮转 (备份到 context_1.jsonl) │                     │
│  │  ├── 清空内存缓存                     │                     │
│  │  ├── 重放历史到 checkpoint            │                     │
│  │  └── 写入新 context.jsonl             │                     │
│  │                                      │                     │
│  │ D-Mail (时间旅行消息)                  │                     │
│  │  ├── send_dmail()                     │                     │
│  │  └── fetch_pending_dmail()            │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  COMPACTION: 上下文压缩                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ SimpleCompaction.compact()             │                     │
│  │  ├── 保留最近 N 条消息                 │                     │
│  │  ├── 使用 LLM 总结旧消息               │                     │
│  │  └── 返回 compacted + preserved        │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流
```

### 1.2 数据结构与存储关系图

```text
┌────────────────────────────────────────────────────────────────────┐
│ [A] Session 核心组件                                                │
└────────────────────────────────────────────────────────────────────┘

                         ┌─────────────────┐
                         │     Session     │
                         │   (会话实体)    │
                         └────────┬────────┘
                                  │
      ┌───────────────────────────┼───────────────────────────┐
      ▼                           ▼                           ▼
┌─────────────┐          ┌─────────────────┐         ┌─────────────┐
│   Context   │          │    KimiSoul     │         │   Wire      │
│  (上下文)   │◄────────▶│    (Agent 核心) │         │  (事件流)   │
├─────────────┤          ├─────────────────┤         ├─────────────┤
│ context.jsonl│         │ - _agent        │         │ wire.jsonl  │
│ _history[]  │          │ - _context      │         │ - TurnBegin │
│ _token_count│          │ - _denwa_renji  │         │ - StepBegin │
│ _checkpoint │          │ - _approval     │         │ - Approval  │
└─────────────┘          └─────────────────┘         └─────────────┘
                                  │
                                  ▼
                         ┌─────────────────┐
                         │  DenwaRenji     │
                         │  (D-Mail 系统)  │
                         └─────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ [B] 存储目录结构                                                    │
└────────────────────────────────────────────────────────────────────┘

~/.local/share/kimi/                    # 共享目录
├── kimi.json                           # 元数据文件
├── sessions/
│   └── {md5(work_dir)}/               # 工作目录哈希
│       └── {session_id}/              # 会话 UUID
│           ├── context.jsonl          # 消息历史 + checkpoint
│           ├── wire.jsonl             # 事件日志
│           └── context_1.jsonl        # 轮转备份
└── logs/
    └── kimi.log                       # 应用日志

┌────────────────────────────────────────────────────────────────────┐
│ [C] context.jsonl 内容格式                                          │
└────────────────────────────────────────────────────────────────────┘

{"role": "user", "content": [...]}              # 用户消息
{"role": "assistant", "content": [...]}         # 助手消息
{"role": "tool", "content": [...]}              # 工具消息
{"role": "_checkpoint", "id": 0}                # Checkpoint 标记
{"role": "_usage", "input_tokens": 100, ...}    # Token 统计

图例: ───▶ 依赖/引用  ┌─┐ 数据结构/文件
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 session 从创建到压缩的完整流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `3`（知道核心组件和存储结构）。
- **10 分钟版**：通读 `3~7`（能定位 checkpoint、time travel 相关问题）。

### 2.1 一句话定义

kimi-cli 的 Session 是**"基于 checkpoint 的时间可逆执行单元"**：每轮对话前创建 checkpoint，支持随时回退到任意 checkpoint，配合 D-Mail 系统实现"向过去发送消息"的时间旅行功能。

---

## 3. 核心组件详解

### 3.1 Session 类

**文件**: `src/kimi_cli/session.py`

```python
@dataclass(slots=True, kw_only=True)
class Session:
    """A session of a work directory."""
    id: str                    # UUID-based session ID
    work_dir: KaosPath         # Working directory path
    work_dir_meta: WorkDirMeta # Metadata about work directory
    context_file: Path         # Path to context.jsonl
    wire_file: WireFile        # Path to wire.jsonl
    title: str                 # Session title
    updated_at: float          # Last update timestamp

    # 类方法
    @classmethod
    async def create(cls, ...) -> "Session": ...        # 创建新 session
    @classmethod
    async def find(cls, ...) -> "Session | None": ...   # 查找 session
    @classmethod
    async def list(cls, work_dir: KaosPath) -> list["Session"]: ...
    @classmethod
    async def continue_(cls, work_dir: KaosPath) -> "Session | None": ...
```

### 3.2 Context 类

**文件**: `src/kimi_cli/soul/context.py`

```python
class Context:
    def __init__(self, file_backend: Path):
        self._file_backend = file_backend
        self._history: list[Message] = []      # In-memory cache
        self._token_count: int = 0
        self._next_checkpoint_id: int = 0      # Auto-increment counter
```

---

## 4. Checkpoint 系统详解

### 4.1 创建 Checkpoint

```python
async def checkpoint(self, add_user_message: bool):
    checkpoint_id = self._next_checkpoint_id
    self._next_checkpoint_id += 1

    # 写入 checkpoint 标记到 context 文件
    async with aiofiles.open(self._file_backend, "a", encoding="utf-8") as f:
        await f.write(
            json.dumps({"role": "_checkpoint", "id": checkpoint_id}) + "\n"
        )

    # 可选：添加合成用户消息用于可见性
    if add_user_message:
        await self.append_message(
            Message(role="user", content=[system(f"CHECKPOINT {checkpoint_id}")])
        )
```

### 4.2 回退到 Checkpoint

```python
async def revert_to(self, checkpoint_id: int):
    # 1. 文件轮转备份
    rotated_file_path = await next_available_rotation(self._file_backend)
    await aiofiles.os.replace(self._file_backend, rotated_file_path)

    # 2. 清空内存缓存
    self._history.clear()
    self._token_count = 0
    self._next_checkpoint_id = 0

    # 3. 重放历史到指定 checkpoint
    async with aiofiles.open(rotated_file_path, encoding="utf-8") as old_file, \
               aiofiles.open(self._file_backend, "w", encoding="utf-8") as new_file:
        async for line in old_file:
            line_json = json.loads(line)
            # 复制行直到找到目标 checkpoint
            if (line_json.get("role") == "_checkpoint" and
                line_json.get("id") == checkpoint_id):
                break
            await new_file.write(line)
```

### 4.3 D-Mail 时间旅行

**文件**: `src/kimi_cli/soul/denwarenji.py`

命名源自《命运石之门》的 "DeLorean Mail"，允许向过去的 checkpoint 发送消息。

```python
class DenwaRenji:
    """Phone microwave (name subject to change) - Time travel messaging system"""

    async def send_dmail(self, checkpoint_id: int, message: Message):
        """Schedule a message to be delivered to a specific checkpoint"""
        # 存储消息到 pending 队列

    async def fetch_pending_dmail(self, checkpoint_id: int) -> list[Message]:
        """Retrieve pending time-travel messages for a checkpoint"""
        # 获取并清除该 checkpoint 的待发送消息
```

---

## 5. Agent Loop 详解

### 5.1 KimiSoul 核心循环

**文件**: `src/kimi_cli/soul/kimisoul.py`

```python
class KimiSoul:
    def __init__(self, agent, context: Context):
        self._agent = agent
        self._context = context
        self._denwa_renji = agent.runtime.denwa_renji
        self._approval = agent.runtime.approval

    async def _turn(self, user_message: Message) -> TurnOutcome:
        # 1. 创建 checkpoint
        await self._checkpoint()
        # 2. 追加用户消息
        await self._context.append_message(user_message)
        # 3. 执行 agent loop
        return await self._agent_loop()

    async def _agent_loop(self) -> TurnOutcome:
        while True:
            # 1. 检查并处理 D-Mail
            dmail = await self._denwa_renji.fetch_pending_dmail(current_checkpoint)

            # 2. LLM 对话
            response = await self._agent.llm.chat(...)

            # 3. 处理工具调用和 approval
            for tool_call in response.tool_calls:
                if requires_approval:
                    approval_result = await self._approval.request(...)

            # 4. 捕获时间旅行异常
            except BackToTheFuture as e:
                # 处理 checkpoint 跳转
                await self._context.revert_to(e.checkpoint_id)
                continue
```

---

## 6. Compaction 机制

### 6.1 触发条件

```python
async def isOverflow(input: { tokens, model }) -> bool:
    config = await Config.get()
    if config.compaction?.auto == False:
        return False

    context = input.model.limit.context
    count = input.tokens.total or (input.tokens.input + output + cache.read + cache.write)

    reserved = config.compaction?.reserved or COMPACTION_BUFFER
    usable = context - max_output_tokens - reserved
    return count >= usable
```

### 6.2 压缩策略

**文件**: `src/kimi_cli/soul/compaction.py`

```python
class SimpleCompaction:
    async def compact(self, messages: Sequence[Message], llm: LLM) -> Sequence[Message]:
        # 1. 保留最近 N 条消息（默认 2）
        to_compact = history[:preserve_start_index]
        to_preserve = history[preserve_start_index:]

        # 2. 使用 LLM 总结旧消息
        result = await kosong.step(
            chat_provider=llm.chat_provider,
            system_prompt="You are a helpful assistant that compacts conversation context.",
            history=[compact_message]
        )

        # 3. 返回 compacted + preserved
        return compacted_messages + to_preserve
```

---

## 7. Wire 协议与事件系统

### 7.1 Wire 事件类型

**文件**: `src/kimi_cli/wire/types.py`

```python
# 核心事件
TurnBegin / TurnEnd           # 对话轮次边界
StepBegin / StepInterrupted   # 步骤生命周期
CompactionBegin / CompactionEnd  # 压缩事件
ApprovalRequest / ApprovalResponse  # 用户审批流
```

### 7.2 事件用途

- **UI 同步**: 实时更新前端状态
- **日志记录**: 完整事件追踪
- **调试分析**: 问题定位

---

## 8. Web API Session 管理

**文件**: `src/kimi_cli/web/api/sessions.py`

```python
# RESTful API
POST   /api/sessions              # 创建 session
GET    /api/sessions/{id}         # 获取 session 信息
POST   /api/sessions/{id}/fork    # 在指定 turn fork

# WebSocket 支持实时更新
```

### 8.1 Forking

创建新 session，复制指定 turn 之前的所有历史：

```python
async def fork(session_id: str, turn_id: str) -> Session:
    # 1. 创建新 session
    # 2. 复制历史到指定 turn
    # 3. 重新映射 message ID
    # 4. 复制所有 parts
```

---

## 9. 排障速查

| 问题 | 检查点 |
|------|--------|
| checkpoint 跳转失败 | 检查 `revert_to` 的文件轮转和重放逻辑 |
| D-Mail 未送达 | 查看 `DenwaRenji` 的 pending 队列状态 |
| 上下文溢出 | 检查 `isOverflow` 的阈值计算和 compaction 触发 |
| 会话文件损坏 | 查看 `context.jsonl` 的 JSON 有效性 |
| 内存占用过高 | 考虑减少 `_history` 缓存或增加压缩频率 |

---

## 10. 架构特点总结

1. **文件追加架构**: 不可变历史，所有数据追加到 JSONL
2. **Checkpoint 标记**: 特殊 `"_checkpoint"` 条目嵌入在上下文流中
3. **文件轮转**: 回退时创建备份而非删除，保证数据安全
4. **懒加载**: 按需从文件恢复上下文
5. **时间旅行**: D-Mail 系统支持向过去发送消息
6. **自动压缩**: 基于 LLM 的上下文压缩，长会话自动维护
7. **事件驱动**: Wire 协议支持实时 UI 同步和日志记录
