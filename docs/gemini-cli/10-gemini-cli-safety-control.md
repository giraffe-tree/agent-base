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

## 6. 欺骗性 URL 检测 (2026-02)

Gemini CLI 新增对欺骗性 URL（Deceptive URL）的检测和提示功能。

### 6.1 检测机制

```typescript
// gemini-cli/packages/cli/src/ui/utils/urlSecurityUtils.ts:25-30

function containsDeceptiveMarkers(hostname: string): boolean {
  return (
    // Punycode 标记
    hostname.toLowerCase().includes('xn--') ||
    // 非 ASCII 字符
    /[^\x00-\x7F]/.test(hostname)
  );
}
```

### 6.2 检测流程

```text
URL 字符串输入
       │
       ▼
┌─────────────────┐
│ URL.parse()     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ containsDeceptiveMarkers │
│ - 检查 'xn--' (Punycode) │
│ - 检查非 ASCII 字符      │
└────────┬────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐  ┌────────┐
| 是    │  │ 否     │
└───┬───┘  └────────┘
    │
    ▼
┌─────────────────────────┐
│ DeceptiveUrlDetails     │
│ - originalUrl (Unicode) │
│ - punycodeUrl (ASCII)   │
└─────────────────────────┘
```

### 6.3 用户提示

当检测到欺骗性 URL 时，在工具确认界面显示：
- 原始 Unicode 形式的 URL
- 对应的 Punycode ASCII 形式
- 安全警告提示

---

## 7. Unicode 字符过滤 (2026-02)

Gemini CLI 实现了全面的 Unicode 字符过滤，防止终端显示被恶意操控。

### 7.1 过滤范围

```typescript
// gemini-cli/packages/cli/src/ui/utils/textUtils.ts:120-134

export function stripUnsafeCharacters(str: string): string {
  const strippedAnsi = stripAnsi(str);
  const strippedVT = stripVTControlCharacters(strippedAnsi);

  // 过滤以下字符:
  // - C0 控制字符 (0x00-0x1F) 除 TAB(0x09), LF(0x0A), CR(0x0D)
  // - C1 控制字符 (0x80-0x9F)
  // - BiDi 控制字符 (U+200E, U+200F, U+202A-U+202E, U+2066-U+2069)
  // - 零宽字符 (U+200B ZWSP, U+FEFF BOM)
  return strippedVT.replace(
    /[\x00-\x08\x0B\x0C\x0E-\x1F\x80-\x9F\u200E\u200F\u202A-\u202E\u2066-\u2069\u200B\uFEFF]/g,
    '',
  );
}
```

### 7.2 字符分类

| 类别 | 范围 | 处理方式 |
|------|------|----------|
| C0 控制字符 | 0x00-0x1F (除 0x09, 0x0A, 0x0D) | 移除 |
| C1 控制字符 | 0x80-0x9F | 移除 |
| BiDi 覆盖字符 | U+202A-U+202E, U+2066-U+2069 | 移除 |
| 零宽空格 | U+200B | 移除 |
| BOM | U+FEFF | 移除 |
| **保留** | | |
| 可打印 ASCII | 0x20-0x7E | 保留 |
| Tab | 0x09 | 保留 |
| 换行 | 0x0A, 0x0D | 保留 |
| Unicode 文字 | U+00A0 及以上 | 保留 |
| ZWJ (表情符号) | U+200D | 保留 |
| ZWNJ | U+200C | 保留 |

### 7.3 应用场景

1. **工具确认界面**: 过滤工具参数和输出中的危险字符
2. **终端输出**: 清理 shell 命令输出
3. **历史记录**: 保存前进行过滤

---

## 8. 失败处理与已知边界

- 拒绝/违规：统一以策略违规响应返回，不执行实际副作用工具。
- 非交互环境：`ASK_USER` 可能无法确认，系统按配置降级到拒绝路径。
- 已知边界：仓库中可见 `safety checker` 相关代码路径，但主运行时是否默认注入需按版本核对，文档落地时应标注”以当前代码路径为准”。

---

## 9. 证据索引（项目名 + 文件路径 + 关键职责）

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
- `gemini-cli` + `gemini-cli/packages/cli/src/ui/utils/urlSecurityUtils.ts` + 欺骗性 URL 检测实现。
- `gemini-cli` + `gemini-cli/packages/cli/src/ui/utils/textUtils.ts` + Unicode 字符过滤实现。


