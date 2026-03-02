# Codex Tool Call 并发机制（questions）

## 结论

Codex **已实现 tool call 并发调用**，并且是“**模型能力开关 + 工具级并发能力 + 运行时读写锁**”三层联合控制。

- 项目名 + 文件路径 + 关键职责：
  - `codex` + `codex/codex-rs/core/src/codex.rs`：在模型请求里打开 `parallel_tool_calls`，并维护 `in_flight` 工具 future。
  - `codex` + `codex/codex-rs/core/src/tools/parallel.rs`：`ToolCallRuntime` 根据工具是否支持并发决定读锁/写锁。
  - `codex` + `codex/codex-rs/core/src/tools/router.rs`：`ToolCall` 标准化、判断 `tool_supports_parallel`。
  - `codex` + `codex/codex-rs/core/src/tools/registry.rs`：`ConfiguredToolSpec.supports_parallel_tool_calls` + `is_mutating`/`tool_call_gate`。

## 如何实现

1. 在 turn 模型请求中，从模型信息读取 `supports_parallel_tool_calls`，并写入 prompt 的 `parallel_tool_calls`。  
2. 流式接收 `ResponseEvent::OutputItemDone`，遇到工具调用时生成 tool future，推入 `in_flight`。  
3. `ToolCallRuntime.handle_tool_call()` 判断工具并发能力：  
   - 支持并发：拿 `RwLock` 读锁（可并发）  
   - 不支持并发：拿 `RwLock` 写锁（独占）  
4. 流结束后统一 `drain_in_flight()`，回收所有工具结果并写回历史。  
5. 对变更型工具，`is_mutating` + `tool_call_gate` 再做额外串行保护。

## 流程图

```text
+----------------------+
| run_sampling_request |
+----------+-----------+
           |
           v
+-----------------------------------------------+
| Prompt.parallel_tool_calls = model_supports...|
+----------+------------------------------------+
           |
           v
+----------------------+
| stream ResponseEvent |
+----------+-----------+
           |
           v
+-----------------------------------+
| OutputItemDone 是 tool call 吗？  |
+-----------+-----------------------+
            |否
            +---------------------> (继续 stream)
            |
            |是
            v
+-----------------+      +----------------------------------+
| build_tool_call | ---> | ToolCallRuntime.handle_tool_call |
+-----------------+      +----------------+-----------------+
                                          |
                                          v
                         +----------------------------------+
                         | tool_supports_parallel ?         |
                         +-----------+----------------------+
                                     |是              |否
                                     v                v
                            +----------------+  +----------------+
                            | RwLock 读锁执行 |  | RwLock 写锁执行 |
                            +--------+-------+  +--------+-------+
                                     \               /
                                      v             v
                                   +---------------------+
                                   | in_flight.push_back |
                                   +----------+----------+
                                              |
                                              v
                                        (继续 stream)
                                              |
                                              v
                                   +------------------+
                                   | stream completed |
                                   +--------+---------+
                                            |
                                            v
                                   +------------------+
                                   | drain_in_flight  |
                                   +--------+---------+
                                            |
                                            v
                             +-------------------------------+
                             | tool output 回注历史           |
                             +-------------------------------+
```

## 数据格式

### 1) Prompt 侧并发开关

```rust
pub struct Prompt {
    pub input: Vec<ResponseItem>,
    pub(crate) tools: Vec<ToolSpec>,
    pub(crate) parallel_tool_calls: bool,
}
```

### 2) 运行时工具调用结构

```rust
pub struct ToolCall {
    pub tool_name: String,
    pub call_id: String,
    pub payload: ToolPayload,
}
```

### 3) 配置侧并发能力

```rust
pub struct ConfiguredToolSpec {
    pub spec: ToolSpec,
    pub supports_parallel_tool_calls: bool,
}
```

## 备注

- Codex 是“**支持并发，但非无条件并发**”：会同时受模型能力、工具能力、变更安全门控约束。
