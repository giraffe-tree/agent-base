# Agent-Base：六大开源 Code Agent 源码深度解析

[![研究类型](https://img.shields.io/badge/类型-源码研究-blue)](./docs)
[![文档总量](https://img.shields.io/badge/docs-154篇-green)](./docs)
[![主线项目](https://img.shields.io/badge/核心项目-6个-orange)](./docs)

> 从“会用 Agent”到“懂 Agent 架构取舍”。
>
> 在线阅读：[https://giraffe-tree.github.io/agent-base/](https://giraffe-tree.github.io/agent-base/)

---

## 仓库定位

本仓库是对多套开源 Code Agent 的**源码级拆解 + 横向对比**，核心目标：

- 看懂 Agent Loop 如何驱动“推理 -> 工具调用 -> 反馈 -> 再推理”
- 看懂不同项目在 Safety、Memory、MCP、Checkpoint 等问题上的工程取舍
- 为你设计/改造自己的 Agent 系统提供可复用参考

---

## 6 个项目对比

> 💡 **阅读建议**: 从 Codex 开始（文档最完整），再根据兴趣选择。每个项目都有独特的架构亮点。

<table>
<tr>
<td width="33%">

**🦀 Codex** (Rust)
> *适合研究: 企业级安全机制*

- 完善的安全沙箱和权限分级
- 支持 OpenAI / Azure OpenAI 提供商
- **推荐入门首选**

📄 [概览](./docs/codex/01-codex-overview.md) · [循环](./docs/codex/04-codex-agent-loop.md) · [MCP](./docs/codex/06-codex-mcp-integration.md)

</td>
<td width="33%">

**🔷 Gemini CLI** (TypeScript)
> *适合研究: 内存管理和上下文策略*

- 三层分层内存架构
- JIT 子目录懒加载
- 状态机驱动的调度器

📄 [概览](./docs/gemini-cli/01-gemini-cli-overview.md) · [循环](./docs/gemini-cli/04-gemini-cli-agent-loop.md) · [内存](./docs/gemini-cli/07-gemini-cli-memory-context.md)

</td>
<td width="33%">

**🌙 Kimi CLI** (Python)
> *适合研究: 状态持久化和回滚*

- D-Mail 时间旅行机制
- Checkpoint 完整实现
- 命令级撤销/重做

📄 [概览](./docs/kimi-cli/01-kimi-cli-overview.md) · [循环](./docs/kimi-cli/04-kimi-cli-agent-loop.md) · [Checkpoint](./docs/kimi-cli/07-kimi-cli-memory-context.md)

</td>
</tr>
<tr>
<td width="33%">

**⚡ OpenCode** (TypeScript)
> *适合研究: 现代 Web 集成*

- Vercel AI SDK 架构
- 流式响应处理
- 超时和进度管理

📄 [概览](./docs/opencode/01-opencode-overview.md) · [循环](./docs/opencode/04-opencode-agent-loop.md) · [MCP](./docs/opencode/06-opencode-mcp-integration.md)

</td>
<td width="33%">

**🔬 SWE-agent** (Python)
> *适合研究: 学术研究/自动化修复*

- 可配置 History Processors
- 专为软件工程任务设计
- 丰富的实验追踪

📄 [概览](./docs/swe-agent/01-swe-agent-overview.md) · [循环](./docs/swe-agent/04-swe-agent-agent-loop.md) · [Prompt](./docs/swe-agent/11-swe-agent-prompt-organization.md)

</td>
<td width="33%">

**🎯 Qwen Code** (TypeScript)
> *适合研究: 工程化架构与循环检测*

- 基于 Gemini CLI 的架构演进
- 完善的循环检测服务 (LoopDetectionService)
- 结构化 Agent 设计模式

📄 [概览](./docs/qwen-code/01-qwen-code-overview.md) · [循环](./docs/qwen-code/04-qwen-code-agent-loop.md) · [MCP](./docs/qwen-code/06-qwen-code-mcp-integration.md)

</td>
</tr>
</table>

----

## 覆盖范围（基于 `docs/` 全量盘点）

截至 2026-02-26，`docs/` 目录共 **154 篇**文档：

- 主线技术文档：95 篇
- Questions 专题文档：59 篇

| 类别 | 目录 | 内容 |
|:---|:---|:---|
| 核心项目（6） | `codex` / `gemini-cli` / `kimi-cli` / `opencode` / `swe-agent` / `qwen-code` | 每个项目按统一编号体系拆解（01~12） |
| 跨项目对比 | `comm` | 共性抽象、架构对比、ACP、Plan & Execute、未来方向 |
| 补充专题 | `cursor` / `claude` | Cursor Checkpoint 存储分析、Claude 消息上下文保留机制 |

---

## 统一分析框架（01~12 编号）

绝大多数项目都按以下主线组织，便于横向对读：

| 编号 | 主题 |
|:---:|:---|
| `01` | Overview（整体架构与核心取舍） |
| `02` | CLI Entry / Session Management |
| `03` | Session Runtime |
| `04` | Agent Loop |
| `05` | Tools System |
| `06` | MCP Integration |
| `07` | Memory Context |
| `08` | UI Interaction |
| `09` | Web Server |
| `10` | Safety Control |
| `11` | Prompt Organization |
| `12` | Logging |

> **扩展内容**：`13` [ACP Integration](./docs/comm/13-comm-acp-integration.md) - 跨项目 ACP（Agent Client Protocol）实现对比

---

## 文档地图（按目录）

| 目录 | 主线文档 | Questions | 快速入口 |
|:---|:---:|:---:|:---|
| [comm](./docs/comm/) | 15 | 2 | [概览](./docs/comm/01-comm-overview.md) / [Agent Loop 对比](./docs/comm/04-comm-agent-loop.md) / [ACP 是什么](./docs/comm/comm-what-is-acp.md) / [ACP 跨项目对比](./docs/comm/13-comm-acp-integration.md) / [Plan and Execute 对比](./docs/comm/comm-plan-and-execute.md) |
| [codex](./docs/codex/) | 13 | 8 | [概览](./docs/codex/01-codex-overview.md) / [Loop](./docs/codex/04-codex-agent-loop.md) / [Safety](./docs/codex/10-codex-safety-control.md) |
| [gemini-cli](./docs/gemini-cli/) | 13 | 9 | [概览](./docs/gemini-cli/01-gemini-cli-overview.md) / [Loop](./docs/gemini-cli/04-gemini-cli-agent-loop.md) / [Memory](./docs/gemini-cli/07-gemini-cli-memory-context.md) |
| [kimi-cli](./docs/kimi-cli/) | 16 | 12 | [入门](./docs/kimi-cli/00-kimi-cli-onboarding.md) / [概览](./docs/kimi-cli/01-kimi-cli-overview.md) / [Memory+Checkpoint](./docs/kimi-cli/07-kimi-cli-memory-context.md) |
| [opencode](./docs/opencode/) | 14 | 10 | [概览](./docs/opencode/01-opencode-overview.md) / [Session 管理](./docs/opencode/02-opencode-session-management.md) / [Loop](./docs/opencode/04-opencode-agent-loop.md) |
| [swe-agent](./docs/swe-agent/) | 12 | 11 | [概览](./docs/swe-agent/01-swe-agent-overview.md) / [Loop](./docs/swe-agent/04-swe-agent-agent-loop.md) / [Tools](./docs/swe-agent/05-swe-agent-tools-system.md) |
| [qwen-code](./docs/qwen-code/) | 12 | 4 | [概览](./docs/qwen-code/01-qwen-code-overview.md) / [Loop](./docs/qwen-code/04-qwen-code-agent-loop.md) / [Safety](./docs/qwen-code/10-qwen-code-safety-control.md) |
| [cursor](./docs/cursor/) | 1 | 2 | [Checkpoint 映射](./docs/cursor/questions/cursor-checkpoint-official-description-and-state-vscdb-mapping.md) / [state.vscdb 分析](./docs/cursor/questions/cursor-state-vscdb-checkpoint-analysis.md) |
| [claude](./docs/claude/) | 0 | 1 | [消息上下文保留](./docs/claude/questions/claude-message-context-retention.md) |

> 完整目录导航请看：[`_sidebar.md`](./_sidebar.md)

---

## 高频 Questions 专题入口

| 主题 | 文档入口 |
|:---|:---|
| Tool 并发调用 | [Codex](./docs/codex/questions/codex-tool-call-concurrency.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-tool-call-concurrency.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-tool-call-concurrency.md) / [OpenCode](./docs/opencode/questions/opencode-tool-call-concurrency.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-tool-call-concurrency.md) |
| 工具错误处理 | [Codex](./docs/codex/questions/codex-tool-error-handling.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-tool-error-handling.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-tool-error-handling.md) / [OpenCode](./docs/opencode/questions/opencode-tool-error-handling.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-tool-error-handling.md) / [Qwen Code](./docs/qwen-code/questions/qwen-code-tool-error-handling.md) |
| 防止无限循环 | [Codex](./docs/codex/questions/codex-infinite-loop-prevention.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-infinite-loop-prevention.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-infinite-loop-prevention.md) / [OpenCode](./docs/opencode/questions/opencode-infinite-loop-prevention.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-infinite-loop-prevention.md) / [Qwen Code](./docs/qwen-code/questions/qwen-code-loop-detection.md) |
| 上下文压缩 | [Codex](./docs/codex/questions/codex-context-compaction.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-context-compaction.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-context-compaction.md) / [OpenCode](./docs/opencode/questions/opencode-context-compaction.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-context-compaction.md) / [Qwen Code](./docs/qwen-code/questions/qwen-code-context-compaction.md) |
| Plan and Execute | [跨项目总览](./docs/comm/comm-plan-and-execute.md) / [Codex](./docs/codex/questions/codex-plan-and-execute.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-plan-and-execute.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-plan-and-execute.md) / [OpenCode](./docs/opencode/questions/opencode-plan-and-execute.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-plan-and-execute.md) |
| Checkpoint 与回滚 | [Kimi 实现](./docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md) / [Kimi 权衡](./docs/kimi-cli/questions/kimi-cli-checkpoint-no-file-rollback-tradeoffs.md) / [OpenCode 实现](./docs/opencode/questions/opencode-checkpoint-implementation.md) / [SWE-agent 实现](./docs/swe-agent/questions/swe-agent-checkpoint-implementation.md) / [SWE-agent 权衡](./docs/swe-agent/questions/swe-agent-checkpoint-no-file-rollback-tradeoffs.md) / [Cursor 映射分析](./docs/cursor/questions/cursor-checkpoint-official-description-and-state-vscdb-mapping.md) |
| Subagent / 多代理 | [Codex](./docs/codex/questions/codex-subagent-implementation.md) / [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-subagent-implementation.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-subagent-implementation.md) / [OpenCode](./docs/opencode/questions/opencode-subagent-implementation.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-subagent-implementation.md) / [Qwen Code](./docs/qwen-code/questions/qwen-code-subagent-implementation.md) |
| Why keep reasoning | [Gemini CLI](./docs/gemini-cli/questions/gemini-cli-why-keep-reasoning.md) / [Kimi CLI](./docs/kimi-cli/questions/kimi-cli-why-keep-reasoning.md) / [OpenCode](./docs/opencode/questions/opencode-why-keep-reasoning.md) / [SWE-agent](./docs/swe-agent/questions/swe-agent-why-keep-reasoning.md) / [Claude](./docs/claude/questions/claude-message-context-retention.md) |
| ACP 协议实现 | [跨项目 ACP 对比](./docs/comm/13-comm-acp-integration.md) / [什么是 ACP](./docs/comm/comm-what-is-acp.md) |

---

## 三段学习路径

### 路线 A：30 分钟快速建立直觉

1. [Code Agent 全局认知（comm）](./docs/comm/01-comm-overview.md)
2. [Codex Agent Loop](./docs/codex/04-codex-agent-loop.md)
3. [跨项目 Agent Loop 对比](./docs/comm/04-comm-agent-loop.md)

### 路线 B：2 小时完成架构骨架

1. [跨项目概览](./docs/comm/01-comm-overview.md)
2. [Tools 对比](./docs/comm/05-comm-tools-system.md)
3. [MCP 对比](./docs/comm/06-comm-mcp-integration.md)
4. [Memory 对比](./docs/comm/07-comm-memory-context.md)
5. [Safety 对比](./docs/comm/10-comm-safety-control.md)
6. [Plan & Execute 对比](./docs/comm/comm-plan-and-execute.md)
7. [ACP Integration 对比](./docs/comm/13-comm-acp-integration.md)

### 路线 C：专题深入

1. Checkpoint：`kimi-cli` / `opencode` / `swe-agent` / `cursor`
2. 推理保留与上下文：`gemini-cli` / `kimi-cli` / `opencode` / `swe-agent` / `claude`
3. 未来趋势：[`从第一性原理看 Coding Agent 的未来突破`](./docs/comm/comm-future-breakthrough-first-principles.md)


---

### 获取源码（可选）

如需对照源码阅读：

```bash
git clone https://github.com/openai/codex.git
git clone https://github.com/google-gemini/gemini-cli.git
git clone https://github.com/MoonshotAI/kimi-cli.git
git clone https://github.com/SWE-agent/SWE-agent.git
git clone https://github.com/anomalyco/opencode.git
git clone https://github.com/QwenLM/qwen-code.git
```

---

## 研究基线

- **时间**: 2026-02-22
- **来源**: 各项目 GitHub 当时最新分支
- **方法**: 源码阅读 + 关键流程图解 + 跨项目对比

---

## 贡献建议

欢迎提交 Issue / PR：

- 修正文档中的事实性错误或路径失效
- 增补新的 Questions 专题（建议沿用已有命名风格）
- 在 `template/` 下复用模板补齐尚未覆盖的分析维度

