# AI Coding Agent 源码深度解析

[![研究项目](https://img.shields.io/badge/类型-源码研究-blue)](./docs)
[![研究基线](https://img.shields.io/badge/基线-2026--02--08-green)](./docs)
[![覆盖项目](https://img.shields.io/badge/覆盖-5个主流Agent-orange)](./docs)

> **面向谁？** 想深入理解 AI Coding Agent 内部机制的开发者、研究人员、架构师
>
> **有什么？** 5 个主流项目的源码级拆解 + 跨项目对比分析 + 核心机制图解

---

## 快速了解

**Coding Agent** 是能自主理解代码、执行开发任务的 AI 系统：

```
用户输入 → LLM 推理 → 工具执行 → 结果输出
                ↑_____________________↓
                      (历史上下文)
```

本仓库通过源码阅读，拆解以下核心机制：

| 机制 | 一句话说明 | 入门文档 |
|------|-----------|----------|
| **Agent Loop** | 核心执行循环（接收输入→推理→调用工具→返回结果） | [`04-*agent-loop.md`](./docs/codex/04-codex-agent-loop.md) |
| **MCP** | 标准化外部工具接入协议（文件/数据库/API） | [`06-*mcp-integration.md`](./docs/codex/06-codex-mcp-integration.md) |
| **Memory** | 对话历史管理、Token 压缩、持久化策略 | [`07-*memory-context.md`](./docs/codex/07-codex-memory-context.md) |
| **Safety** | 权限控制、沙箱隔离、执行审批 | [`10-*safety-control.md`](./docs/codex/10-codex-safety-control.md) |

---

## 5 分钟快速开始

### 方式一：快速概览（推荐新手）

按这个顺序阅读，建立整体认知：

```
1. docs/codex/04-codex-agent-loop.md      → 理解 Agent 如何工作（20分钟）
2. docs/comm/04-comm-agent-loop.md        → 看跨项目共性和差异（15分钟）
3. docs/codex/06-codex-mcp-integration.md → 理解工具调用机制（15分钟）
```

### 方式二：带着问题查（目标导向）

| 你想了解 | 直接看这里 |
|----------|-----------|
| Agent Loop 如何循环执行？ | [`docs/codex/04-codex-agent-loop.md`](./docs/codex/04-codex-agent-loop.md) |
| 如何安全地执行用户代码？ | [`docs/codex/10-codex-safety-control.md`](./docs/codex/10-codex-safety-control.md) |
| Token 超限怎么处理？ | [`docs/codex/07-codex-memory-context.md`](./docs/codex/07-codex-memory-context.md) |
| MCP 工具怎么注册调用？ | [`docs/codex/06-codex-mcp-integration.md`](./docs/codex/06-codex-mcp-integration.md) |
| Checkpoint/回滚怎么实现？ | [`docs/kimi-cli/07-kimi-cli-memory-context.md`](./docs/kimi-cli/07-kimi-cli-memory-context.md) |
| 各项目架构对比？ | [`docs/comm/04-comm-agent-loop.md`](./docs/comm/04-comm-agent-loop.md) |

---

## 项目对比速览

| 项目 | 语言 | 核心特点 | 适合研究什么 |
|------|------|----------|-------------|
| **Codex** | Rust | 多模型支持、完善安全沙箱 | 企业级安全机制 |
| **Gemini CLI** | TypeScript | 三层分层内存、JIT 子目录加载 | 内存管理和上下文策略 |
| **OpenCode** | TypeScript | Vercel AI SDK、流式架构 | 现代 Web 开发集成 |
| **Kimi CLI** | Python | D-Mail 时间旅行、Checkpoint 机制 | 状态回滚和持久化 |
| **SWE-agent** | Python | 可配置 History Processors | 研究/自动化修复流程 |

---

## 文档结构

```
docs/
├── codex/           # Codex CLI (Rust) - 最完整，推荐从这里开始
├── gemini-cli/      # Gemini CLI (TypeScript)
├── opencode/        # OpenCode (TypeScript)
├── kimi-cli/        # Kimi CLI (Python) - Checkpoint 机制详解
├── swe-agent/       # SWE-agent (Python) - 学术研究导向
├── comm/            # 跨项目共性抽象 - 适合对比学习
└── cursor/          # Cursor 专项研究（进行中）
```

### 文档编号说明

| 编号 | 主题 | 优先级 |
|:----:|------|:------:|
| `04` | **Agent Loop** ⭐ | 必读 |
| `06` | **MCP Integration** ⭐ | 必读 |
| `07` | **Memory Context** ⭐ | 必读 |
| `02` | CLI 入口 | 按需 |
| `03` | Session Runtime | 按需 |
| `05` | Tools System | 按需 |
| `08` | UI Interaction | 按需 |
| `10` | Safety Control | 按需 |
| `11+` | 专题深度 | 进阶 |

> 💡 **技巧**：编号一致的文档可以横向对比。例如对比 `04-codex-agent-loop.md` 和 `04-comm-agent-loop.md` 理解设计差异。

---

## 文档覆盖进度

| 项目 | 覆盖范围 | 文档数 | 备注 |
|------|----------|:------:|------|
| `codex` | ✅ 01~11 + questions | 13 | 最完整，推荐入门首选 |
| `opencode` | ✅ 01~11 + questions | 15 | 完整覆盖 |
| `gemini-cli` | ✅ 01~11 + questions | 14 | 完整覆盖 |
| `kimi-cli` | ✅ 01~11 + questions | 16 | Checkpoint 机制深度分析 |
| `swe-agent` | ✅ 01~11 + questions | 16 | 学术研究导向 |
| `comm` | ✅ 01~10 | 10 | 跨项目共性对比 |
| `cursor` | 📝 01 + questions | 3 | 专项研究中 |

### 按主题快速导航

| 主题 | 所有项目文档 |
|------|-------------|
| Overview (01) | [codex](docs/codex/01-codex-overview.md) · [opencode](docs/opencode/01-opencode-overview.md) · [gemini-cli](docs/gemini-cli/01-gemini-cli-overview.md) · [kimi-cli](docs/kimi-cli/01-kimi-cli-overview.md) · [swe-agent](docs/swe-agent/01-swe-agent-overview.md) · [comm](docs/comm/01-comm-overview.md) |
| Agent Loop (04) | [codex](docs/codex/04-codex-agent-loop.md) · [opencode](docs/opencode/04-opencode-agent-loop.md) · [gemini-cli](docs/gemini-cli/04-gemini-cli-agent-loop.md) · [kimi-cli](docs/kimi-cli/04-kimi-cli-agent-loop.md) · [swe-agent](docs/swe-agent/04-swe-agent-agent-loop.md) · [comm](docs/comm/04-comm-agent-loop.md) |
| MCP (06) | [codex](docs/codex/06-codex-mcp-integration.md) · [opencode](docs/opencode/06-opencode-mcp-integration.md) · [gemini-cli](docs/gemini-cli/06-gemini-cli-mcp-integration.md) · [kimi-cli](docs/kimi-cli/06-kimi-cli-mcp-integration.md) · [swe-agent](docs/swe-agent/06-swe-agent-mcp-integration.md) · [comm](docs/comm/06-comm-mcp-integration.md) |
| Memory (07) | [codex](docs/codex/07-codex-memory-context.md) · [opencode](docs/opencode/07-opencode-memory-context.md) · [gemini-cli](docs/gemini-cli/07-gemini-cli-memory-context.md) · [kimi-cli](docs/kimi-cli/07-kimi-cli-memory-context.md) · [swe-agent](docs/swe-agent/07-swe-agent-memory-context.md) · [comm](docs/comm/07-comm-memory-context.md) |
| Safety (10) | [codex](docs/codex/10-codex-safety-control.md) · [opencode](docs/opencode/10-opencode-safety-control.md) · [gemini-cli](docs/gemini-cli/10-gemini-cli-safety-control.md) · [kimi-cli](docs/kimi-cli/10-kimi-cli-safety-control.md) · [swe-agent](docs/swe-agent/10-swe-agent-safety-control.md) · [comm](docs/comm/10-comm-safety-control.md) |
| Prompt (11) | [codex](docs/codex/11-codex-prompt-organization.md) · [opencode](docs/opencode/11-opencode-prompt-organization.md) · [gemini-cli](docs/gemini-cli/11-gemini-cli-prompt-organization.md) · [kimi-cli](docs/kimi-cli/11-kimi-cli-prompt-organization.md) · [swe-agent](docs/swe-agent/11-swe-agent-prompt-organization.md) |

---

## 可选：获取源码

如需对照源码阅读（文档中已包含关键代码片段）：

```bash
git clone https://github.com/openai/codex.git
git clone https://github.com/google-gemini/gemini-cli.git
git clone https://github.com/MoonshotAI/kimi-cli.git
git clone https://github.com/SWE-agent/SWE-agent.git
git clone https://github.com/anomalyco/opencode.git
```

---

## 研究基线

- **时间**：2026-02-08
- **来源**：各项目 GitHub 当时最新分支
- **方法**：源码阅读 + 关键流程图解 + 跨项目对比

---

## 贡献与反馈

欢迎通过 Issue 或 PR 补充：
- 发现文档错误或过时
- 补充新的分析视角
- 添加更多项目研究
