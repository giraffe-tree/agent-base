# Agent Base Research

本仓库对主流 **AI Coding Agent** 进行源码级拆解，帮助你深入理解智能编程助手的内部工作机制。

## 什么是 Coding Agent？

Coding Agent 是一种能够**自主理解代码、执行开发任务**的 AI 系统。不同于简单的代码补全工具，它可以：

- 📝 **阅读代码库** - 理解项目结构和依赖关系
- 🔧 **执行命令** - 运行测试、安装依赖、构建项目
- 🐛 **修复 Bug** - 定位问题并提交修复代码
- 💬 **持续对话** - 在多轮交互中保持上下文

## 本仓库研究范围

我们深入拆解以下核心机制：

| 主题 | 说明 | 对应文档 |
|------|------|----------|
| **Agent Loop** | 核心执行循环（输入→推理→工具执行→输出） | `04-*agent-loop.md` |
| **Tools System** | 工具注册、调用、结果处理机制 | `05-*tools-system.md` |
| **MCP 集成** | 外部工具协议接入（如文件系统、数据库等） | `06-*mcp-integration.md` |
| **Memory Context** | 对话历史管理、Token 压缩、持久化 | `07-*memory-context.md` |
| **UI 交互** | CLI 界面、Web 界面、用户输入处理 | `08-*ui-interaction.md` |
| **安全控制** | 权限管理、沙箱隔离、审批流程 | `10-*safety-control.md` |

> 📅 **研究基线**：所有上游仓库均来自 2026-02-08 从 GitHub clone 的当时最新分支。

---

## 快速对比：各项目特点一览

| 项目 | 开发方 | 语言 | 核心特点 | 最佳适用场景 |
|------|--------|------|----------|--------------|
| **Codex** | OpenAI | Rust | 多模型支持、完善的安全沙箱、两阶段记忆 | 企业级生产环境 |
| **Gemini CLI** | Google | TypeScript | 三层分层内存、JIT 子目录加载、Extension 生态 | 大型项目开发 |
| **OpenCode** | 社区 | TypeScript | Vercel AI SDK 深度集成、流式架构、Zod 类型安全 | 现代 Web 开发 |
| **Kimi CLI** | Moonshot | Python | D-Mail 时间旅行、Checkpoint 机制、极简设计 | 快速原型开发 |
| **SWE-agent** | 学术/社区 | Python | 可配置 History Processors、Trajectory 重放 | 研究/自动化修复 |

---

## 源码获取（可选）

如需对照源码阅读文档：

```bash
# export ALL_PROXY=http://localhost:7890
git clone https://github.com/openai/codex.git
git clone https://github.com/anomalyco/opencode.git
git clone https://github.com/google-gemini/gemini-cli.git
git clone https://github.com/MoonshotAI/kimi-cli.git
git clone https://github.com/SWE-agent/SWE-agent.git
```

## 文档结构

`docs/` 目录按**产品维度 + 主题维度**组织：

```
docs/
├── codex/                    # Codex CLI (Rust)
│   ├── 04-codex-agent-loop.md
│   ├── 05-codex-tools-system.md
│   ├── 06-codex-mcp-integration.md
│   └── 07-codex-memory-context.md
├── gemini-cli/               # Gemini CLI (TypeScript)
├── opencode/                 # OpenCode (TypeScript)
├── kimi-cli/                 # Kimi CLI (Python)
├── swe-agent/                # SWE-agent (Python)
├── comm/                     # 跨项目共性抽象
└── cursor/questions/         # Cursor 专项研究
```

### 文档编号约定

| 编号 | 主题 | 核心内容 |
|:----:|------|----------|
| `02` | CLI 入口 | 启动流程、参数解析、初始化 |
| `03` | Session Runtime | 会话生命周期、状态管理 |
| `04` | **Agent Loop** ⭐ | 核心执行循环（必读） |
| `05` | Tools System | 工具注册、调用、结果处理 |
| `06` | **MCP Integration** ⭐ | 外部工具协议接入 |
| `07` | **Memory Context** ⭐ | 对话历史、Token 管理、压缩 |
| `08` | UI Interaction | CLI 界面、用户输入处理 |
| `09` | Web Server | HTTP API、实时通信 |
| `10` | Safety Control | 权限、沙箱、审批 |
| `11+` | 专题 | checkpoint/revert 等深度话题 |

---

## 核心概念速览

### 1. Agent Loop（智能体循环）

所有 Coding Agent 的核心执行模式：

```
┌─────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐
│  用户输入 │ -> │ LLM 推理  │ -> │ 工具执行 │ -> │ 结果输出 │
└─────────┘    └──────────┘    └─────────┘    └─────────┘
                     ↑                              │
                     └──────── 历史上下文 <─────────┘
```

### 2. MCP（Model Context Protocol）

标准化的外部工具接入协议，让 Agent 能调用：
- 文件系统操作
- 数据库查询
- API 调用
- 自定义工具

### 3. Memory Context（记忆上下文）

管理多轮对话中的历史记录：
- **存储**：如何保存对话历史
- **压缩**：Token 超出限制时的处理
- **检索**：快速定位相关信息

---

## 推荐阅读路径

### 🔰 初学者路线（推荐）

**阶段 1：建立整体认知（约 30 分钟）**
1. 先读任意一个项目的 `04-*agent-loop.md`（建议从 codex 或 opencode 开始）
2. 理解 Agent Loop 的基本执行流程

**阶段 2：深入核心机制（约 2 小时）**
3. 对比阅读不同项目的 `04` 文档，理解设计差异
4. 阅读 `06-*mcp-integration.md`，理解外部工具接入
5. 阅读 `07-*memory-context.md`，理解上下文管理

**阶段 3：专题研究（按需）**
6. 对感兴趣的项目，阅读 `05`、`08`、`09`、`10` 补全细节
7. 深入 `questions/` 目录研究特定机制

### 🎯 目标导向速查

| 想了解什么 | 推荐阅读 |
|-----------|----------|
| Agent 如何执行代码？ | `04-*agent-loop.md` |
| Agent 如何调用外部工具？ | `06-*mcp-integration.md` |
| Agent 如何记住对话历史？ | `07-*memory-context.md` |
| 各项目架构对比？ | 对比各项目的 `04` 文档 |
| Codex 的安全机制？ | `docs/codex/10-codex-safety-control.md` |
| Kimi 的 Checkpoint 机制？ | `docs/kimi-cli/07-kimi-cli-memory-context.md` |

---

## 文档覆盖进度

| 项目 | 已覆盖 | 备注 |
|------|--------|------|
| `codex` | ✅ `02~10` | 完整覆盖 |
| `opencode` | ✅ `02~10` | 含 `questions/11` |
| `gemini-cli` | ✅ `02~10` | 完整覆盖 |
| `kimi-cli` | ✅ `02~10` | 含 `questions/11`、`12` |
| `comm` | ✅ `02~10` | 跨项目共性层 |
| `swe-agent` | ✅ `02`、`04~07` | 完整覆盖 |
| `cursor` | 📝 `questions` | 专项研究进行中 |
