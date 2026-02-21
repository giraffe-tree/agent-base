# Kimi CLI 如何避免 Tool 无限循环调用

**结论先行**: Kimi CLI 通过 **Checkpoint + D-Mail 时间旅行机制** 防止 tool 无限循环。核心设计是"状态可回滚"，通过 `max_steps_per_turn` 硬性限制和 `max_retries_per_step` 重试上限，结合 **D-Mail 指令注入** 让 LLM 自我修正。

---

## 1. 核心防护机制：Checkpoint + D-Mail

### 1.1 Checkpoint 创建与回滚

位于 `kimi-cli/src/kimi_cli/soul/context.py`：

```python
class Context:
    def __init__(self, file_backend: Path):
        self._history: list[Message] = []
        self._next_checkpoint_id: int = 0

    async def checkpoint(self, add_user_message: bool):
        """创建 checkpoint"""
        checkpoint_id = self._next_checkpoint_id
        self._next_checkpoint_id += 1

        # 持久化 checkpoint 标记
        async with aiofiles.open(self._file_backend, "a") as f:
            await f.write(json.dumps({"role": "_checkpoint", "id": checkpoint_id}) + "\n")

        # 可选：向 LLM 注入 checkpoint 提示
        if add_user_message:
            await self.append_message(
                Message(role="user", content=[system(f"CHECKPOINT {checkpoint_id}")])
            )

    async def revert_to(self, checkpoint_id: int):
        """回滚到指定 checkpoint（时间旅行核心）"""
        if checkpoint_id >= self._next_checkpoint_id:
            raise ValueError(f"Checkpoint {checkpoint_id} does not exist")

        # 1. 旋转上下文文件（保留历史）
        rotated_file_path = await next_available_rotation(self._file_backend)
        await aiofiles.os.replace(self._file_backend, rotated_file_path)

        # 2. 重建上下文到指定 checkpoint
        self._history.clear()
        self._token_count = 0
        self._next_checkpoint_id = 0

        async with (
            aiofiles.open(rotated_file_path) as old_file,
            aiofiles.open(self._file_backend, "w") as new_file,
        ):
            async for line in old_file:
                line_json = json.loads(line)
                # 遇到目标 checkpoint 停止复制
                if line_json["role"] == "_checkpoint" and line_json["id"] == checkpoint_id:
                    break
                await new_file.write(line)
                # 恢复内存状态
                if line_json["role"] == "_checkpoint":
                    self._next_checkpoint_id = line_json["id"] + 1
                else:
                    self._history.append(Message.model_validate(line_json))
```

**防循环原理**: 当检测到循环或异常时，可以 `revert_to(checkpoint_id)` 回滚到之前的安全状态，然后注入 D-Mail 指令让 LLM 自我修正。

### 1.2 D-Mail 机制

位于 `kimi-cli/src/kimi_cli/soul/denwarenji.py` 和 `kimisoul.py`：

```python
class BackToTheFuture(Exception):
    """触发回滚到指定 checkpoint 并注入新消息"""
    def __init__(self, checkpoint_id: int, messages: list[Message]):
        self.checkpoint_id = checkpoint_id
        self.messages = messages

# 在 _agent_loop 中捕获处理
async def _agent_loop(self):
    while not done:
        try:
            result = await self._step()
        except BackToTheFuture as bttf:
            # 1. 回滚到指定 checkpoint
            self.context.revert_to(bttf.checkpoint_id)
            # 2. 新建 checkpoint
            new_cid = await self.context.checkpoint(add_user_message=False)
            # 3. 注入 D-Mail 消息（系统提示 + 修正指令）
            await self.context.append_messages(bttf.messages)
            continue  # 继续执行
```

**D-Mail 内容示例**: 当检测到循环时，DenwaRenji 会生成类似：
```
"你似乎陷入了重复调用 XXX 工具的循环。请反思之前的操作，
尝试不同的方法解决问题。建议：1. 查看文件当前状态 2. 制定新的修改计划"
```

---

## 2. 硬性限制

### 2.1 最大步数限制

位于 `kimi-cli/src/kimi_cli/config.py`：

```python
class LoopControl(BaseModel):
    """Agent 循环控制配置"""

    max_steps_per_turn: int = Field(default=100, ge=1)
    """单次 turn 的最大步数（工具调用次数）"""

    max_retries_per_step: int = Field(default=3, ge=1)
    """单步最大重试次数"""
```

**硬性上限**: 100 步后强制终止，防止无限循环。

### 2.2 重试机制

```python
from tenacity import retry, wait_exponential_jitter, stop_after_attempt

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((NetworkError, APIError)),
)
async def call_with_retry(self, ...):
    """仅对网络/API错误重试，业务错误不重试"""
    return await self.llm.call(...)
```

**关键设计**: 仅重试网络层错误，工具调用失败不重试（避免加重循环）。

---

## 3. 触发 Checkpoint 重建的场景

| 场景 | 触发点 | 行为 |
|------|--------|------|
| **上下文压缩** | token 预算逼近上限 | 清空历史 → 重建 checkpoint → 注入摘要 |
| **D-Mail 回滚** | DenwaRenji 检测到循环 | revert_to → 新建 checkpoint → 注入指令 |
| **Turn 开始** | 每次用户输入 | 创建 checkpoint 0 作为基线 |

---

## 4. 防循环流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                Kimi CLI Tool 调用防循环流程                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Turn 开始                                                      │
│        │                                                        │
│        ▼                                                        │
│   创建 Checkpoint 0（基线）                                       │
│        │                                                        │
│        ▼                                                        │
│   ┌───────────────────┐                                        │
│   │ 执行 _step()      │                                        │
│   │ (工具调用)        │                                        │
│   └─────────┬─────────┘                                        │
│             │                                                   │
│     ┌───────┴───────┬──────────────────┐                      │
│     ▼               ▼                  ▼                      │
│   成功            失败              循环检测                   │
│     │               │                  │                       │
│     │          ┌────┘                  │                       │
│     │          ▼                       ▼                       │
│     │     重试计数 < 3?           DenwaRenji                   │
│     │          │                  触发 BackToTheFuture         │
│     │         是│                       │                       │
│     │          ▼                       ▼                       │
│     │     指数退避重试            context.revert_to()          │
│     │          │                  回滚到安全状态                │
│     └──────────┴──────────────────────┤                       │
│                                       │                       │
│                                       ▼                       │
│                                新建 Checkpoint                 │
│                                       │                       │
│                                       ▼                       │
│                                注入 D-Mail 指令                 │
│                                (系统提示 + 修正建议)            │
│                                       │                       │
│                                       ▼                       │
│                                继续 _step()                    │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ 步数 >= 100?                                          │   │
│   └────────────────────────┬────────────────────────────────┘   │
│                            │是                                  │
│                            ▼                                    │
│                    抛出 MaxStepsReached                         │
│                    强制终止 turn                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 与其他 Agent 的对比

| 防护机制 | Kimi CLI | Gemini CLI | Codex | OpenCode | SWE-agent |
|---------|----------|------------|-------|----------|-----------|
| **状态回滚** | ✅ Checkpoint | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **LLM 自修正** | ✅ D-Mail 注入 | ✅ Final Warning | ❌ 无 | ❌ 无 | ✅ Autosubmit |
| **硬性步数限制** | ✅ 100步 | ✅ 100轮 | ✅ 有 | ❌ Infinity | ✅ 无限制 |
| **重试上限** | ✅ 3次 | ✅ 3次 | ✅ 5/4次 | ❌ 无 | ✅ 3次 |
| **循环检测** | ❌ 无（依赖 D-Mail） | ✅ 三层检测 | ❌ 无 | ✅ Doom loop | ❌ 无 |

---

## 6. 总结

Kimi CLI 的防循环设计哲学是**"状态可回滚 + LLM 自修正"**：

1. **Checkpoint 锚点**: 每个 turn 创建可回滚的安全点
2. **D-Mail 时间旅行**: 检测到问题时回滚并注入修正指令
3. **硬性限制**: 100 步上限和 3 次重试限制兜底
4. **压缩联动**: 上下文压缩时重建 checkpoint，保证状态一致性

Kimi CLI 的独特之处在于**不依赖智能循环检测**，而是通过**可回滚的状态管理**和**指令注入**让 LLM 自我修正，这与 Gemini CLI 的"检测-干预"模式形成对比。

---

*文档版本: 2026-02-21*
*基于代码版本: kimi-cli (baseline 2026-02-08)*
