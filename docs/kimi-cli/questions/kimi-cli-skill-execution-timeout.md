# Kimi CLI 工具超时机制（校正版）

本文校对 `./kimi-cli` 当前源码，聚焦“超时”在工具执行中的真实行为。

---

## 1. 结论

- Kimi CLI 存在工具级超时控制，但不是统一一套 `checkpoint commit/rollback` 事务机制。
- Shell 工具超时：默认 `60s`，上限 `300s`，超时会 kill 子进程并返回 `ToolError`。
- MCP 工具超时：由 `config.mcp.client.tool_call_timeout_ms` 控制，超时同样返回 `ToolError`（timeout brief）。
- 当前实现不会因为“工具超时”自动回滚文件系统或 context 到旧 checkpoint。

---

## 2. Shell 工具超时

文件：`src/kimi_cli/tools/shell/__init__.py`

关键点：
- 参数：`timeout` 默认 60，`1 <= timeout <= 300`。
- 执行：`asyncio.wait_for(..., timeout)` 包住 stdout/stderr 读取流程。
- 超时：捕获 `TimeoutError` 后 `process.kill()`，返回错误结果。

对应代码位置：
- `Params.timeout`：约 `:22-30`
- `_run_shell_command()`：约 `:95-123`
- 超时错误返回：约 `:89-93`

---

## 3. MCP 工具超时

文件：`src/kimi_cli/soul/toolset.py`

关键点：
- `MCPTool` 初始化时从配置读取：
  - `runtime.config.mcp.client.tool_call_timeout_ms`
- 调用 `client.call_tool(..., timeout=self._timeout)`。
- timeout 异常会转为 `ToolError(brief="Timeout")`。

对应代码位置：
- `_timeout` 设置：约 `:377`
- `call_tool(... timeout=...)`：约 `:387-391`
- timeout 错误映射：约 `:395-404`

---

## 4. 与 checkpoint 的关系

- Agent loop 在 step 前会创建 checkpoint（`kimisoul.py`）。
- 但工具超时返回的是普通工具错误结果，后续由模型决定下一步。
- 目前没有“超时自动 revert_to(checkpoint)”的硬编码路径。

结论：
- checkpoint 提供“可回退能力”，
- 但“何时回退”依赖 D-Mail/显式逻辑，不是所有超时都自动回滚。

---

## 5. 配置入口

全局配置文件默认：`~/.kimi/config.toml`（不是 `config.yaml`）

相关配置结构：`src/kimi_cli/config.py`
- `mcp.client.tool_call_timeout_ms`（默认 60000）

