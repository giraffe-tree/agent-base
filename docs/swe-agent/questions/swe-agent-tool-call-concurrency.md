# SWE-agent Tool Call 并发机制（questions）

## 结论

SWE-agent **未实现单 step 多 tool call 并发执行**；其 function-calling 解析器强制“每次响应必须且只能一个 tool call”。

- 项目名 + 文件路径 + 关键职责：
  - `SWE-agent` + `SWE-agent/sweagent/tools/parsing.py`：`FunctionCallingParser` 校验 `len(tool_calls) == 1`，否则报错。
  - `SWE-agent` + `SWE-agent/sweagent/types.py`：`StepOutput.tool_calls/tool_call_ids` 数据结构。
  - `SWE-agent` + `SWE-agent/sweagent/agent/agents.py`（在 loop 中）："query -> parse -> execute action" 单行动推进。

## 如何实现（单调用串行）

1. 模型返回 `model_response`。  
2. `FunctionCallingParser.__call__` 读取 `tool_calls`。  
3. 若不是恰好 1 个：抛 `FunctionCallingFormatError`（`missing` 或 `multiple`）。  
4. 取 `tool_calls[0]` 转换为 action。  
5. agent loop 执行动作并进入下一 step。

这意味着：在 function-calling 模式下，SWE-agent 的策略是“**一次一步一个工具动作**”。

## 流程图

```text
+----------------+
| model_response |
+-------+--------+
        |
        v
+-----------------------+
| FunctionCallingParser |
+-----------+-----------+
            |
            v
+----------------------------------+
| len(tool_calls) == 1 ?           |
+-------------+--------------------+
              |否                         |是
              v                           v
 +-----------------------------+   +---------------------------+
 | FunctionCallingFormatError  |   | tool_call = tool_calls[0] |
 +-----------------------------+   +-------------+-------------+
                                                 |
                                                 v
                                      +---------------------+
                                      | parse to action     |
                                      +----------+----------+
                                                 |
                                                 v
                                      +---------------------+
                                      | handle_action 执行  |
                                      +----------+----------+
                                                 |
                                                 v
                                +--------------------------------------+
                                | 写入 StepOutput 并进入下一 step      |
                                +--------------------------------------+
```

## 数据格式

### 1) 解析输入（function calling）

```python
tool_calls = model_response.get("tool_calls", None)
if tool_calls is None or len(tool_calls) != 1:
    raise FunctionCallingFormatError(...)
```

### 2) step 输出结构

```python
class StepOutput(BaseModel):
    thought: str = ""
    action: str = ""
    observation: str = ""
    done: bool = False
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_ids: list[str] | None = None
```

## 备注

- 仓库里有并行概念（例如 batch run 的多 worker），但那是**任务级并行**，不是单次模型响应内的多 tool call 并发。
