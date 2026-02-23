# AI Coding Agent 源码深度解析

[![研究项目](https://img.shields.io/badge/类型-源码研究-blue)](./docs)
[![研究基线](https://img.shields.io/badge/基线-2026--02--08-green)](./docs)
[![覆盖项目](https://img.shields.io/badge/覆盖-5个主流Agent-orange)](./docs)

> **从"会用"到"懂原理"——零基础理解 AI Coding Agent 内部机制**
>
> 📖 **在线阅读**: [https://giraffe-tree.github.io/agent-base/](https://giraffe-tree.github.io/agent-base/)

---

## Agent 核心循环

Coding Agent 的本质是一个**持续推理循环**：接收任务 → AI思考 → 执行动作 → 观察结果 → 继续直到完成

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   用户输入   │────▶│   AI 思考   │────▶│  执行工具   │────▶│  观察结果   │
│ "修复 bug"  │     │  规划步骤   │     │ 读文件/执行 │     │ 成功/失败   │
└─────────────┘     └──────┬──────┘     └─────────────┘     └──────┬──────┘
                           │                                        │
                           └──────────────◀─────────────────────────┘
                                          (循环直到任务完成)
```

---

## 为什么要学习这个？

<details>
<summary><b>👨‍💻 我是开发者</b> —— 想知道 Agent 如何调用我的代码？</summary>

- 理解 Agent 如何安全地执行 shell 命令和用户代码
- 学习 Token 超限时的上下文压缩策略
- 掌握工具注册和调用的标准化流程 (MCP)

</details>

<details>
<summary><b>🏗️ 我是架构师</b> —— 想设计自己的 Agent 系统？</summary>

- 对比 5 个主流项目的架构取舍
- 学习状态持久化、Checkpoint 回滚机制
- 理解沙箱隔离和权限控制的最佳实践

</details>

<details>
<summary><b>🔬 我是研究者</b> —— 想了解 Agent 的能力边界？</summary>

- 深入 Agent Loop 的决策边界和错误恢复
- 分析不同项目的 Prompt 组织策略
- 研究 Memory 管理对长任务的影响

</details>

---

## 三阶段学习路径

### 🌱 Stage 1: 建立直觉 (30分钟)

**目标**: 理解 Agent 基本工作原理

| 顺序 | 文档 | 阅读时间 | 收获 |
|:---:|:---|:---:|:---|
| 1 | [04-codex-agent-loop.md](./docs/codex/04-codex-agent-loop.md) | 15 min | 理解 Agent 循环执行流程 |
| 2 | [04-comm-agent-loop.md](./docs/comm/04-comm-agent-loop.md) | 10 min | 了解不同项目的实现差异 |
| 3 | [06-codex-mcp-integration.md](./docs/codex/06-codex-mcp-integration.md) | 10 min | 理解工具如何被调用 |

**读完你能**: 向别人解释 "Agent 是怎么工作的"

### 🌿 Stage 2: 理解核心机制 (2小时)

**目标**: 掌握关键设计决策和权衡

```
基础概念 ──────▶ 核心机制 ──────▶ 进阶主题
    │              │               │
    ▼              ▼               ▼
Agent Loop    MCP 工具系统      Safety 沙箱
Memory 管理    上下文压缩       Checkpoint 回滚
```

| 主题 | 推荐文档 | 关键问题 |
|:---|:---|:---|
| **Agent Loop** | [codex](./docs/codex/04-codex-agent-loop.md) · [gemini-cli](./docs/gemini-cli/04-gemini-cli-agent-loop.md) · [kimi-cli](./docs/kimi-cli/04-kimi-cli-agent-loop.md) · [opencode](./docs/opencode/04-opencode-agent-loop.md) · [swe-agent](./docs/swe-agent/04-swe-agent-agent-loop.md) | 如何优雅地中断循环？如何处理流式输出？ |
| **MCP 集成** | [codex](./docs/codex/06-codex-mcp-integration.md) · [gemini-cli](./docs/gemini-cli/06-gemini-cli-mcp-integration.md) · [kimi-cli](./docs/kimi-cli/06-kimi-cli-mcp-integration.md) · [opencode](./docs/opencode/06-opencode-mcp-integration.md) · [swe-agent](./docs/swe-agent/06-swe-agent-mcp-integration.md) | 工具如何注册？参数如何传递？ |
| **Memory 管理** | [codex](./docs/codex/07-codex-memory-context.md) · [gemini-cli](./docs/gemini-cli/07-gemini-cli-memory-context.md) · [kimi-cli](./docs/kimi-cli/07-kimi-cli-memory-context.md) | Token 超限怎么办？历史记录如何压缩？ |
| **Safety 控制** | [codex](./docs/codex/10-codex-safety-control.md) · [gemini-cli](./docs/gemini-cli/10-gemini-cli-safety-control.md) | 如何安全执行用户代码？权限如何分级？ |

**读完你能**: 对比不同方案的优劣，参与技术选型讨论

### 🌳 Stage 3: 专题深入研究

**目标**: 成为特定领域的专家

- **Checkpoint 机制**: [kimi-cli/questions/](./docs/kimi-cli/questions/) —— 状态回滚的完整实现
- **Prompt 工程**: [11-*-prompt-organization.md](./docs/codex/11-codex-prompt-organization.md) —— 系统提示词设计
- **安全沙箱**: [codex 安全专题](./docs/codex/10-codex-safety-control.md) —— 企业级隔离方案

---

## 5 个项目对比

> 💡 **阅读建议**: 从 Codex 开始（文档最完整），再根据兴趣选择

<table>
<tr>
<td width="50%">

**🦀 Codex** (Rust)
> *适合研究: 企业级安全机制*

- 完善的安全沙箱和权限分级
- 多模型支持 (OpenAI 全系列)
- **推荐入门首选**

📄 [01-概览](./docs/codex/01-codex-overview.md) · [04-循环](./docs/codex/04-codex-agent-loop.md) · [06-MCP](./docs/codex/06-codex-mcp-integration.md)

</td>
<td width="50%">

**🔷 Gemini CLI** (TypeScript)
> *适合研究: 内存管理和上下文策略*

- 三层分层内存架构
- JIT 子目录懒加载
- 状态机驱动的调度器

📄 [01-概览](./docs/gemini-cli/01-gemini-cli-overview.md) · [04-循环](./docs/gemini-cli/04-gemini-cli-agent-loop.md) · [07-内存](./docs/gemini-cli/07-gemini-cli-memory-context.md)

</td>
</tr>
<tr>
<td width="50%">

**🌙 Kimi CLI** (Python)
> *适合研究: 状态持久化和回滚*

- D-Mail 时间旅行机制
- Checkpoint 完整实现
- 命令级撤销/重做

📄 [01-概览](./docs/kimi-cli/01-kimi-cli-overview.md) · [04-循环](./docs/kimi-cli/04-kimi-cli-agent-loop.md) · [07-Checkpoint](./docs/kimi-cli/07-kimi-cli-memory-context.md)

</td>
<td width="50%">

**⚡ OpenCode** (TypeScript)
> *适合研究: 现代 Web 集成*

- Vercel AI SDK 架构
- 流式响应处理
- 超时和进度管理

📄 [01-概览](./docs/opencode/01-opencode-overview.md) · [04-循环](./docs/opencode/04-opencode-agent-loop.md) · [06-MCP](./docs/opencode/06-opencode-mcp-integration.md)

</td>
</tr>
<tr>
<td width="50%">

**🔬 SWE-agent** (Python)
> *适合研究: 学术研究/自动化修复*

- 可配置 History Processors
- 专为软件工程任务设计
- 丰富的实验追踪

📄 [01-概览](./docs/swe-agent/01-swe-agent-overview.md) · [04-循环](./docs/swe-agent/04-swe-agent-agent-loop.md) · [11-Prompt](./docs/swe-agent/11-swe-agent-prompt-organization.md)

</td>
<td width="50%">

**📊 跨项目对比** (comm)
> *适合快速了解共性和差异*

- 抽象通用设计模式
- 横向对比表格
- 架构取舍分析

📄 [01-概览](./docs/comm/01-comm-overview.md) · [04-循环对比](./docs/comm/04-comm-agent-loop.md) · [06-MCP对比](./docs/comm/06-comm-mcp-integration.md)

</td>
</tr>
</table>

---

## 核心机制速查

遇到具体问题？直接跳转:

| 你想了解 | 推荐文档 |
|:---|:---|
| Agent 如何循环执行？ | [04-codex-agent-loop.md](./docs/codex/04-codex-agent-loop.md) |
| 工具如何注册调用？ | [06-codex-mcp-integration.md](./docs/codex/06-codex-mcp-integration.md) |
| Token 超限怎么处理？ | [07-codex-memory-context.md](./docs/codex/07-codex-memory-context.md) |
| 如何安全执行代码？ | [10-codex-safety-control.md](./docs/codex/10-codex-safety-control.md) |
| Checkpoint 如何实现？ | [07-kimi-cli-memory-context.md](./docs/kimi-cli/07-kimi-cli-memory-context.md) |

---

## 文档编号说明

本仓库使用统一编号系统，便于横向对比:

| 编号 | 主题 | 优先级 |
|:---:|:---|:---:|
| `01` | Overview 概览 | ⭐ 必读 |
| `04` | **Agent Loop** | ⭐⭐ 核心 |
| `06` | **MCP Integration** | ⭐⭐ 核心 |
| `07` | **Memory Context** | ⭐⭐ 核心 |
| `10` | Safety Control | 按需 |
| `11+` | 专题深度 | 进阶 |

> 💡 **技巧**: 编号一致的文档可以横向对比。例如 `04-codex-agent-loop.md` vs `04-gemini-cli-agent-loop.md`

---

## FAQ

<details>
<summary><b>什么是 MCP?</b></summary>

**MCP** (Model Context Protocol) 是 Anthropic 提出的标准化协议，让 AI Agent 能够统一调用外部工具（文件系统、数据库、API等）。

可以理解为 Agent 的 "USB 接口标准"——无论工具用什么语言实现，都能通过 MCP 接入。

</details>

<details>
<summary><b>Agent Loop 和 main 函数有什么区别？</b></summary>

传统的 `main` 函数是**线性执行**的：A → B → C → 结束

Agent Loop 是**循环推理**的：
1. 接收用户输入
2. LLM 推理下一步动作
3. 执行动作（读文件/运行命令/调用 API）
4. 观察结果，回到步骤 2
5. 直到判断任务完成，返回结果

**关键区别**: Agent 会自主决定"什么时候该做什么"，而不是按预设顺序执行。

</details>

<details>
<summary><b>Checkpoint 是什么？有什么用？</b></summary>

**Checkpoint** 是 Agent 执行过程中的"存档点"。

类比游戏存档：
- 你可以在任意时刻保存当前状态
- 之后可以随时回到这个状态
- 走错路了？读档重来

在 Kimi CLI 中，这被称为 "D-Mail" 机制，支持命令级的撤销和重做。

</details>

<details>
<summary><b>零基础可以看懂吗？</b></summary>

可以。本仓库假设你:
- 会用 Claude/Cursor 等 AI 编程工具
- 有基础编程经验
- 对 "AI 如何执行代码" 感到好奇

不需要事先了解 Agent 架构，文档会从零开始解释。

</details>

---

## 开始阅读

### 推荐新手路径

```
第一步: 在线浏览 → https://giraffe-tree.github.io/agent-base/
         ↓
第二步: 阅读 codex/04-codex-agent-loop.md (15分钟)
         ↓
第三步: 阅读 comm/04-comm-agent-loop.md (10分钟)
         ↓
第四步: 根据兴趣选择具体项目深入
```

### 获取源码（可选）

如需对照源码阅读：

```bash
git clone https://github.com/openai/codex.git
git clone https://github.com/google-gemini/gemini-cli.git
git clone https://github.com/MoonshotAI/kimi-cli.git
git clone https://github.com/SWE-agent/SWE-agent.git
git clone https://github.com/anomalyco/opencode.git
```

---

## 研究基线

- **时间**: 2026-02-08
- **来源**: 各项目 GitHub 当时最新分支
- **方法**: 源码阅读 + 关键流程图解 + 跨项目对比

---

## 贡献与反馈

欢迎通过 Issue 或 PR 补充：

- 发现文档错误或过时
- 补充新的分析视角
- 添加更多项目研究