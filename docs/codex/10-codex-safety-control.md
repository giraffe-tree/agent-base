# Safety Control（codex）

结论先行：codex 的 safety-control 采用“策略配置 + 执行前门控 + 审批分支 + 失败分级”的分层设计。控制主线不是单点拦截器，而是从 `ToolsConfig/MCP 配置` 注入到 `TurnContext`，在 `ToolRouter/ToolRegistry` 与 MCP 调用路径上多点生效。

> 说明：当前仓库内 `codex` 实码未完整收录，本文依据 `docs/codex/*` 中已提取的源码级引用整理。

---

## 1. 跨项目统一安全控制流程图

```text
+--------------------------------------------+
| 用户请求 User Request                      |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 策略来源 Policy Source                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 执行前检查 Pre-Execution Check             |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 审批闸门 Approval Gate                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 工具执行 Tool Execution                    |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 边界/沙箱 Boundary and Sandbox Guard       |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 结果/错误 Result or Error                  |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 重试/中止/降级 Retry, Abort, or Fallback   |
+--------------------------------------------+
```

---

## 2. codex 项目级控制链路

```text
[配置策略 ToolsConfig + MCP Server Config]
                    |
                    v
[回合上下文 Turn Context]
                    |
                    v
[路由分发 Tool Router Dispatch]
                    |
                    v
[注册表门控 Tool Registry Dispatch]
                    |
                    v
[类型/变更检查 Type and Mutating Check]
                    |
                    v
[并发门控 tool_call_gate]
                    |
                    v
[MCP 审批请求 MCP Approval Request]
                    |
                    v
               +-----------------------------+
               | 接受或拒绝 Accept / Decline |
               +-------------+---------------+
                             |
             +---------------+----------------+
             |                                |
             v                                v
[执行工具 session.call_tool]      [拒绝结果返回 Rejected Result]
             |                                |
             +---------------+----------------+
                             v
          [错误分级 Function Call Error Classify]
                             |
                             v
        [继续或中止 Turn Continue or Abort]
```

---

## 3. 策略来源（Policy Source）

- `ToolsConfig`：控制 shell 工具类型、仅 JS REPL 模式、实验工具集合。
- `McpServerConfig`：控制 MCP `enabled_tools/disabled_tools/scopes/tool_timeout_sec`。
- `TurnContext`：把 `sandbox/approval/cwd/model` 注入单个 turn，使后续 dispatch 基于同一上下文判定。

核心点：策略不是只在启动时读取一次，而是被带入每次 turn 的工具执行路径。

---

## 4. 执行前检查与审批

- `ToolRouter.dispatch_tool_call()`：在 `js_repl_tools_only` 等模式下提前阻断不合规 direct tool call。
- `ToolRegistry.dispatch()`：做 `matches_kind`、`is_mutating` 与并发门控（`tool_call_gate.wait_ready()`）。
- MCP 工具调用进入 `handle_mcp_tool_call()` 后，会先走 `maybe_request_mcp_tool_approval()`：
  - `Accept`：继续 `session.call_tool()`
  - `Decline`：直接构造拒绝结果返回模型

---

## 5. 权限边界与失败处理

- 本地 shell 调用映射为 `SandboxPermissions::UseDefault`，避免模型在 tool payload 中任意放大权限。
- MCP 远端能力通过 server 级 allow/deny 与 scope 约束，避免“连上即全开”。
- 错误分级：
  - `FunctionCallError::Fatal`：中止
  - `FunctionCallError::RespondToModel`：把失败回传给模型并继续 loop
- Hook 失败分级：
  - `FailedContinue`：记警告继续
  - `FailedAbort`：中止回合

---

## 6. 证据索引（项目名 + 文件路径 + 关键职责）

- `codex` + `docs/codex/04-codex-agent-loop.md` + turn 主链路、`TurnContext` 字段注入与运行边界。
- `codex` + `docs/codex/05-codex-tools-system.md` + `ToolsConfig`、`ToolRouter/ToolRegistry` 门控与错误分级。
- `codex` + `docs/codex/06-codex-mcp-integration.md` + MCP 配置面与 `maybe_request_mcp_tool_approval()` 审批分支。
- `codex` + `docs/codex/questions/codex-tool-call-concurrency.md` + 并发工具调用控制与 mutating 工具串行化。

---

## 7. 边界与不确定性

- 本文结论基于仓库内研究文档中的源码引用，不是直接在本仓库编译运行 `codex-rs` 得出。
- 若后续引入 `codex` 实码镜像，建议补一节“配置键值到 Rust 结构体字段”的逐项映射核对。

