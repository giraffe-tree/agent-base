# Kimi CLI Tool Call 并发机制（questions）

## 结论

Kimi CLI **已实现 tool call 并发调用**，主要由底层 `kosong` 工具执行层实现。

- 项目名 + 文件路径 + 关键职责：
  - `kimi-cli` + `kimi-cli/packages/kosong/src/kosong/tooling/simple.py`：`SimpleToolset` 把工具调用包装为 `asyncio.create_task` 并发执行。
  - `kimi-cli` + `kimi-cli/packages/kosong/src/kosong/__init__.py`：`step()` 收集多个 `tool_call` 的 future，`StepResult.tool_results()` 聚合结果。
  - `kimi-cli` + `kimi-cli/src/kimi_cli/soul/kimisoul.py`：agent step 中 `await result.tool_results()`，再统一写入上下文。

## 如何实现

1. 模型单次 step 可能产出多个 `tool_calls`。  
2. `on_tool_call()` 对每个调用执行 `toolset.handle(tool_call)`：  
   - `SimpleToolset.handle()` 返回 `asyncio.Task`（并发）或立即结果。  
3. 所有 future 放入 `tool_result_futures[tool_call.id]`。  
4. `StepResult.tool_results()` 按 `tool_calls` 顺序逐个 `await` 对应 future，得到稳定输出顺序。  
5. `kimisoul._step()` 获取结果后 `_grow_context()` 统一追加 tool message。

## 流程图

```text
+-------------+
| kosong.step |
+------+------+
       |
       v
+-------------------------------+
| LLM streaming: on_tool_call   |
+---------------+---------------+
                |
                v
+--------------------------------+
| toolset.handle(tool_call)      |
+---------------+----------------+
                |
                v
+--------------------------+
| SimpleToolset ?          |
+-----------+--------------+
            |是                    |否
            v                      v
 +--------------------------+   +--------------------------+
 | asyncio.create_task(_call)|  | 同步结果包装为 future     |
 +------------+-------------+   +------------+-------------+
              \                           /
               v                         v
          +--------------------------------------+
          | tool_result_futures[tool_call.id]    |
          +-------------------+------------------+
                              |
                              v
                    +------------------+
                    | StepResult 返回   |
                    +--------+---------+
                             |
                             v
                 +-----------------------------+
                 | await StepResult.tool_results() |
                 +---------------+-------------+
                                 |
                                 v
                 +----------------------------------+
                 | kimisoul._grow_context 追加结果   |
                 +----------------------------------+
```

## 数据格式

### 1) 消息上的工具调用

```python
class Message(BaseModel):
    role: Role
    content: list[ContentPart]
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
```

### 2) Step 结果

```python
@dataclass(frozen=True, slots=True)
class StepResult:
    id: str | None
    message: Message
    usage: TokenUsage | None
    tool_calls: list[ToolCall]
    _tool_result_futures: dict[str, ToolResultFuture]
```

### 3) 工具结果聚合

```python
async def tool_results(self) -> list[ToolResult]:
    for tool_call in self.tool_calls:
        future = self._tool_result_futures[tool_call.id]
        result = await future
```

## 备注

- Kimi CLI 的并发是“**执行并发 + 结果顺序化回收**”，既提高吞吐也保证上下文写入顺序可预测。
