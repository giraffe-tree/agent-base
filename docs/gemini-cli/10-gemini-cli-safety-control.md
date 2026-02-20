# Safety Control（gemini-cli）

结论先行：`gemini-cli` 的 safety-control 是规则引擎主导的前置拦截模型。核心链路是 `settings/CLI/TOML -> PolicyEngine -> Scheduler.checkPolicy -> ALLOW | ASK_USER | DENY`，并在确认后支持动态规则沉淀（会话或落盘）。

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

## 2. gemini-cli 项目级控制链路

```text
[配置输入 Settings / CLI / TOML]
               |
               v
[策略构建 createPolicyEngineConfig]
               |
               v
[策略引擎 PolicyEngine]
               |
               v
[调度前检查 Scheduler.checkPolicy]
               |
               v
         +------------------------------------+
         | ALLOW / ASK_USER / DENY            |
         | 允许 / 询问用户 / 拒绝             |
         +-----------+-------------+----------+
                     |             |
            +--------+---+     +---+----------------------+
            | ALLOW      |     | DENY                    |
            v            |     v                         |
[执行工具 ToolExecutor]  | [策略违规返回 Policy Violation]
            ^            |                               |
            |            +--------------+----------------+
            |                           |
            |               [用户确认 User Confirmation]
            |                           |
            |               [动态规则更新 Dynamic Rule Update]
            +---------------------------+
                        |
                        v
                 [工具结果 Tool Result]
```

---

## 3. 策略来源（配置如何进入运行时）

- 输入层：
  - `schemas/settings.schema.json` 定义 `policyPaths`、`tools.allowed/exclude`、`mcp.allowed/excluded`、`mcpServers.*.trust` 等。
  - CLI 侧把用户配置合并为 `effectiveSettings`。
- 构建层：
  - `packages/cli/src/config/policy.ts` 提取 policy 相关字段。
  - `packages/core/src/policy/config.ts` 组合默认策略、用户 TOML、admin 策略、settings 派生规则。
- 注入层：
  - `Config` 初始化时构造 `PolicyEngine` 并交给 scheduler 调用。

---

## 4. 执行前检查与审批流程

- Scheduler 在每个工具执行前调用 `checkPolicy`。
- 决策分支：
  - `DENY`：立即返回 `POLICY_VIOLATION`，工具不执行。
  - `ASK_USER`：进入确认流，用户可选择允许一次或持续允许。
  - `ALLOW`：直接执行工具。
- 动态规则：
  - 用户确认“总是允许”后可更新运行时规则，并可写入自动保存策略文件（例如 `auto-saved.toml`）。

---

## 5. 权限边界（文件系统、shell、MCP）

- 文件与 shell 路径边界：
  - `validatePathAccess` 限制访问工作区与受控临时目录。
  - shell 的 `cwd` 与路径先校验，再进入命令策略判定。
- shell 风控：
  - 对复合命令、子命令、重定向做解析；不安全可解析性场景降级为 `ASK_USER`（非交互模式可转 `DENY`）。
- MCP 治理：
  - 服务启停受 `allow/exclude/admin allowlist/session disable` 多层限制。
  - `trust` 与 `includeTools/excludeTools` 决定可暴露工具面。

---

## 6. 失败处理与已知边界

- 拒绝/违规：统一以策略违规响应返回，不执行实际副作用工具。
- 非交互环境：`ASK_USER` 可能无法确认，系统按配置降级到拒绝路径。
- 已知边界：仓库中可见 `safety checker` 相关代码路径，但主运行时是否默认注入需按版本核对，文档落地时应标注“以当前代码路径为准”。

---

## 7. 证据索引（项目名 + 文件路径 + 关键职责）

- `gemini-cli` + `gemini-cli/schemas/settings.schema.json` + 安全策略相关配置项 schema。
- `gemini-cli` + `gemini-cli/packages/cli/src/config/config.ts` + `effectiveSettings` 汇总与 core 配置注入。
- `gemini-cli` + `gemini-cli/packages/cli/src/config/policy.ts` + CLI 配置到 PolicySettings 的映射。
- `gemini-cli` + `gemini-cli/packages/core/src/policy/config.ts` + 策略构建、规则合成、动态持久化。
- `gemini-cli` + `gemini-cli/packages/core/src/policy/toml-loader.ts` + TOML 解析、校验、优先级处理。
- `gemini-cli` + `gemini-cli/packages/core/src/policy/policy-engine.ts` + 规则匹配与 `ALLOW/ASK_USER/DENY` 决策。
- `gemini-cli` + `gemini-cli/packages/core/src/scheduler/scheduler.ts` + tool 执行前策略检查主流程。
- `gemini-cli` + `gemini-cli/packages/core/src/scheduler/policy.ts` + 策略检查与确认后规则更新。
- `gemini-cli` + `gemini-cli/packages/core/src/tools/shell.ts` + shell 执行前路径校验与确认信息构建。
- `gemini-cli` + `gemini-cli/packages/core/src/tools/mcp-client-manager.ts` + MCP 服务启用与连接治理。
- `gemini-cli` + `gemini-cli/packages/core/src/tools/mcp-tool.ts` + MCP 工具调用侧确认与执行路径。


