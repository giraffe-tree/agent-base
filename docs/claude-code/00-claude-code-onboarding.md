# Claude Code 文档化 TODO

> **说明**：本文档是 Claude Code 子项目的文档化入口与进度追踪。按统一的文档编号体系，列出所有待撰写的文档清单。

---

## TL;DR

Claude Code（Anthropic 官方 CLI）源码已纳入本仓库（`claude-code/src/`），但对应的技术文档尚待补齐。本文档作为**总览与进度看板**，追踪 `docs/claude-code/` 下所有待完成的文档项。

当前状态：**仅 `questions/` 下有一篇已完成的深度分析，其余主文档全部待撰写。**

---

## 文档清单（按统一编号体系）

### 核心主文档

| 编号 | 文档名 | 状态 | 对应主题 |
|------|--------|------|----------|
| 01 | `01-claude-code-overview.md` | [x] 已完成 | 项目概览与架构分层 |
| 02 | `02-claude-code-cli-entry.md` | [x] 已完成 | CLI 入口与参数解析 |
| 03 | `03-claude-code-session-runtime.md` | [x] 已完成 | Session 生命周期与运行时 |
| **04** | **`04-claude-code-agent-loop.md`** | [x] 已完成 | **Agent Loop（核心机制）** |
| 05 | `05-claude-code-tools-system.md` | [x] 已完成 | 工具系统设计 |
| **06** | **`06-claude-code-mcp-integration.md`** | [x] 已完成 | **MCP 集成（核心机制）** |
| **07** | **`07-claude-code-memory-context.md`** | [x] 已完成 | **Memory / 上下文管理（核心机制）** |
| 08 | `08-claude-code-ui-interaction.md` | [x] 已完成 | UI 交互与事件流 |
| 09 | `09-claude-code-web-server.md` | [x] 已完成 | Web Server / 远程模式 |
| 10 | `10-claude-code-safety-control.md` | [x] 已完成 | 安全控制与审批机制 |
| 11 | `11-claude-code-prompt-organization.md` | [x] 已完成 | Prompt 组织策略 |
| 12 | `12-claude-code-logging.md` | [x] 已完成 | 日志与可观测性 |
| 13 | `13-claude-code-acp-integration.md` | [x] 已完成 | ACP（Agent Context Protocol）集成 |

### Questions（深度分析）

| 文档名 | 状态 | 说明 |
|--------|------|------|
| `questions/claude-message-context-retention.md` | [x] 已完成 | [消息上下文保留机制](./questions/claude-message-context-retention.md) |
| `questions/claude-infinite-loop-prevention.md` | [x] 已完成 | 无限循环 / Max Steps 防护 |
| `questions/claude-tool-error-handling.md` | [x] 已完成 | 工具调用错误处理策略 |
| `questions/claude-tool-call-concurrency.md` | [x] 已完成 | 工具并发执行机制 |
| `questions/claude-context-compaction.md` | [x] 已完成 | 上下文压缩 / 截断策略 |
| `questions/claude-subagent-implementation.md` | [x] 已完成 | Subagent / Agent 内嵌调用实现 |
| `questions/claude-skill-execution-timeout.md` | [x] 已完成 | Skill / 工具执行超时处理 |
| `questions/claude-plan-and-execute.md` | [x] 已完成 | Plan & Execute 模式实现 |
| `questions/claude-revert-user-edit-conflict.md` | [x] 已完成 | Checkpoint / 用户编辑冲突回滚 |
| `questions/claude-why-keep-reasoning.md` | [x] 已完成 | 保留 reasoning 的设计取舍 |

---

## 撰写优先级建议

### Phase 1：核心骨架（先写这 3 篇）
1. `01-claude-code-overview.md` —— 建立全局视图
2. `04-claude-code-agent-loop.md` —— Agent 项目是研究核心
3. `07-claude-code-memory-context.md` —— 已有 `questions/claude-message-context-retention.md` 可作为输入

### Phase 2：支撑机制（再写这 3 篇）
4. `05-claude-code-tools-system.md`
5. `06-claude-code-mcp-integration.md`
6. `02-claude-code-cli-entry.md` + `03-claude-code-session-runtime.md`

### Phase 3：外围与治理
7. `08-claude-code-ui-interaction.md`
8. `10-claude-code-safety-control.md`
9. `12-claude-code-logging.md`
10. `11-claude-code-prompt-organization.md`
11. `09-claude-code-web-server.md`
12. `13-claude-code-acp-integration.md`

### Phase 4：Questions 深度分析
- 优先补齐与其他项目共通的热点问题：`infinite-loop-prevention`、`tool-error-handling`、`subagent-implementation`。

---

## 关键源码入口（供撰写时参考）

| 组件 | 源码路径 | 备注 |
|------|----------|------|
| 源码根目录 | `claude-code/src/` | TypeScript 代码 |
| CLI 入口 | `claude-code/src/cli.ts` | 需确认实际文件名 |
| Agent Loop | `claude-code/src/agent/` 或 `commands/` | 需进一步探索 |
| 工具系统 | `claude-code/src/tools/` | 推测路径 |

> ⚠️ 具体源码文件路径需在撰写时通过 `glob` / `grep` 在 `claude-code/src` 下确认，并在各文档中标注 **✅ Verified**。

---

## 文档撰写规范（自我 checklist）

每篇文档在撰写时，建议包含以下结构：

- [ ] **结论先行**：一句话总结核心机制
- [ ] **关键代码位置**：文件路径 + 职责说明
- [ ] **ASCII / Mermaid 流程图**：在详细代码解释前给出视觉概览
- [ ] **代码片段 + 设计取舍**：与其他项目（Codex / Gemini CLI / Kimi CLI）做横向对比
- [ ] **证据标记**：使用 ✅ Verified / ⚠️ Inferred / ❓ Pending

---

## 相关参考

- [统一文档编号体系说明](../../CLAUDE.md#document-numbering-system)
- [Kimi CLI Onboarding](../kimi-cli/00-kimi-cli-onboarding.md) —— 可作为 onboarding 模板参考
- [消息上下文保留（已完稿）](./questions/claude-message-context-retention.md)

---

*最后更新：2026-03-31*
