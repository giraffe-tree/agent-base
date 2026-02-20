# 跨 Agent 概述对比

## 1. 概念定义

Agent CLI 是一类将大型语言模型（LLM）与本地环境（文件系统、Shell、网络等）连接起来的命令行工具。它们允许用户通过自然语言与 AI 交互，让 AI 能够读取文件、执行命令、修改代码并自主完成软件开发任务。

### 核心能力

- **代码理解与生成**：分析现有代码库，生成新代码
- **环境交互**：通过工具调用与本地环境（Shell、文件系统）交互
- **多轮推理**：通过 Agent Loop 持续迭代直到任务完成
- **上下文管理**：管理对话历史、文件上下文和工具执行结果

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 是由普林斯顿大学 NLP 组开发的学术型代码 Agent，专注于自动化软件工程任务（如 GitHub Issue 修复）。采用 Python/Pydantic 技术栈，强调可复现性和研究友好性。

**关键代码位置**

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `sweagent/run/run.py` | CLI 入口和命令解析 |
| Agent Loop | `sweagent/agent/agents.py` | 主循环逻辑 |
| 工具系统 | `sweagent/tools/` | Bundle 配置驱动工具 |
| 环境管理 | `sweagent/environment/` | Docker 容器化执行 |
| 模型接口 | `sweagent/agent/models/` | 多模型支持 |

**技术特点**
- 使用 Pydantic 进行配置验证
- Docker 容器隔离执行环境
- Bundle 系统模块化工具定义
- 支持多种模型输出格式解析

### 2.2 Codex (OpenAI)

**实现概述**

Codex 是 OpenAI 官方推出的 Rust 实现 Agent CLI，注重性能和安全沙箱。采用 Rust/tokio 技术栈，提供原生性能和严格的权限控制。

**关键代码位置**

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `codex-rs/cli/src/main.rs` | CLI 入口 |
| Agent Loop | `codex-rs/core/src/agent_loop.rs` | 主循环和 Turn 管理 |
| 工具系统 | `codex-rs/core/src/tools/` | Handler 注册模式 |
| 沙箱系统 | `codex-rs/core/src/sandbox/` | 安全执行环境 |
| 会话管理 | `codex-rs/core/src/session.rs` | Session 生命周期 |

**技术特点**
- Rust/tokio 高性能异步
- 原生安全沙箱（macOS Seatbelt / Linux Landlock）
- MCP 工具集成支持
- Hook 扩展机制

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 是 Google 官方的 TypeScript 实现，基于 Gemini API。采用 TypeScript/Node 技术栈，提供丰富的 IDE 集成和智能体能力。

**关键代码位置**

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `packages/cli/src/index.ts` | CLI 入口 |
| Agent Loop | `packages/core/src/core/client.ts` | GeminiClient 主循环 |
| Turn 处理 | `packages/core/src/core/turn.ts` | 单轮流解析 |
| 工具系统 | `packages/core/src/tools/` | 声明式工具定义 |
| Scheduler | `packages/core/src/scheduler/` | 工具执行状态机 |

**技术特点**
- 递归 continuation 驱动循环
- Scheduler 状态机管理工具执行
- 内置 Loop Detection 防止无限循环
- 模型路由自动选择

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 是月之暗面（Moonshot AI）推出的 Python 实现 Agent，采用 asyncio 技术栈，注重上下文管理和多 Agent 协作。

**关键代码位置**

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `kimi-cli/src/kimi_cli/main.py` | CLI 入口 |
| Agent Loop | `kimi-cli/src/kimi_cli/agent/soul.py` | KimiSoul 主循环 |
| 会话管理 | `kimi-cli/src/kimi_cli/session.py` | Session 状态 |
| 工具系统 | `kimi-cli/src/kimi_cli/tools/` | 模块化工具 |
| ACP 集成 | `kimi-cli/src/kimi_cli/acp/` | 多 Agent 协议 |

**技术特点**
- Python/asyncio 异步
- Checkpoint + revert 上下文回滚机制
- D-Mail 跨时间线消息系统
- Ralph 自动迭代模式

### 2.5 OpenCode

**实现概述**

OpenCode 是由 Saunders 开发的 TypeScript 实现，基于 Vercel AI SDK。注重 Agent 系统、权限控制和上下文压缩。

**关键代码位置**

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `packages/opencode/src/main.ts` | CLI 入口 |
| Agent Loop | `packages/opencode/src/session/prompt.ts` | SessionPrompt 主循环 |
| 处理器 | `packages/opencode/src/session/processor.ts` | 单轮事件处理 |
| 工具系统 | `packages/opencode/src/tool/` | Zod Schema 定义 |
| 权限系统 | `packages/opencode/src/session/permission.ts` | PermissionNext |

**技术特点**
- TypeScript/Vercel AI SDK
- Zod 类型安全的工具定义
- 多 Agent 系统（build/plan/explore 等）
- 上下文压缩（Compaction）机制

---

## 3. 相同点总结

### 3.1 架构共性

| 维度 | 共性描述 |
|------|----------|
| **Agent Loop** | 都实现了"模型推理 → 工具调用 → 结果回注 → 继续推理"的循环 |
| **工具系统** | 都支持文件操作、Shell 执行、代码搜索等核心工具 |
| **上下文管理** | 都管理对话历史和工具执行结果 |
| **流式输出** | 都支持模型输出的实时流式展示 |
| **安全控制** | 都有某种形式的权限确认机制 |

### 3.2 工具共性

所有 Agent 都提供以下核心工具类别：

- **文件操作**：read、write、edit、glob、grep
- **Shell 执行**：bash/command 执行
- **代码搜索**：语义搜索或文本搜索
- **网络访问**：web search、URL fetch
- **任务完成**：submit/done 标记

### 3.3 交互模式共性

- 命令行界面作为主入口
- 交互式 REPL 模式
- 单次命令执行模式
- 会话历史持久化

---

## 4. 不同点对比

### 4.1 技术栈对比

| Agent | 语言 | 运行时 | 配置格式 |
|-------|------|--------|----------|
| SWE-agent | Python | 同步/异步 | Pydantic + YAML |
| Codex | Rust | tokio 异步 | Rust struct |
| Gemini CLI | TypeScript | Node.js | TypeScript 对象 |
| Kimi CLI | Python | asyncio | Pydantic |
| OpenCode | TypeScript | Bun/Node | Zod Schema |

### 4.2 Agent Loop 机制对比

| Agent | 循环模式 | 核心组件 | 特点 |
|-------|----------|----------|------|
| SWE-agent | Step 循环 | `Agent` 类 | 基于历史记录的 step 推进 |
| Codex | Turn 循环 | `AgentLoop` + `Turn` | actor 模型消息传递 |
| Gemini CLI | 递归 continuation | `GeminiClient` + `Turn` | 递归驱动，Scheduler 状态机 |
| Kimi CLI | Step 循环 + checkpoint | `KimiSoul` + `_agent_loop` | 可回滚的 step 循环 |
| OpenCode | while(true) 循环 | `SessionPrompt.loop` | 任务驱动的多分支循环 |

### 4.3 沙箱/安全机制对比

| Agent | 沙箱技术 | 权限控制 | 特点 |
|-------|----------|----------|------|
| SWE-agent | Docker 容器 | 命令过滤 | 容器隔离 |
| Codex | Seatbelt/Landlock + network sandbox | 策略引擎 | 原生系统级沙箱 |
| Gemini CLI | 无（直接执行） | 策略 + 用户确认 | 审批流程 |
| Kimi CLI | 无（直接执行） | 用户确认 | 简单确认机制 |
| OpenCode | 无（直接执行） | PermissionNext 系统 | 细粒度权限 |

### 4.4 上下文管理对比

| Agent | 管理方式 | 压缩机制 | 特点 |
|-------|----------|----------|------|
| SWE-agent | 基于 token 限制 | 无内置压缩 | 窗口滑动 |
| Codex | Conversation 窗口 | 无内置压缩 | 模型级窗口管理 |
| Gemini CLI | Session 管理 | `tryCompressChat` | 动态压缩 |
| Kimi CLI | Context + checkpoint | `compact_context` | 显式压缩 + 回滚 |
| OpenCode | Message DB | `SessionCompaction` | compaction + prune |

### 4.5 工具系统对比

| Agent | 定义方式 | 扩展性 | MCP 支持 |
|-------|----------|--------|----------|
| SWE-agent | YAML Bundle | Bundle 配置 | 否 |
| Codex | Handler trait | 动态注册 | 是 |
| Gemini CLI | 声明式类 | 内置 + 发现 + MCP | 是 |
| Kimi CLI | Python 类 | 模块化 | ACP 协议 |
| OpenCode | Zod Schema | 动态注册 + 插件 | 是 |

### 4.6 多 Agent 协作对比

| Agent | 协作机制 | 子 Agent | 特点 |
|-------|----------|----------|------|
| SWE-agent | 否 | 否 | 单 Agent |
| Codex | 否 | 否 | 单 Agent |
| Gemini CLI | 否 | 否 | 单 Agent（IDE 扩展除外） |
| Kimi CLI | ACP 协议 | `CreateSubagent` + `Task` | 支持子 Agent |
| OpenCode | Subtask 机制 | 多 Agent 系统 | explore/build/plan |

---

## 5. 源码索引

### 5.1 入口文件

| Agent | 文件路径 | 说明 |
|-------|----------|------|
| SWE-agent | `sweagent/run/run.py:1` | CLI 入口 |
| Codex | `codex-rs/cli/src/main.rs:1` | CLI 入口 |
| Gemini CLI | `packages/cli/src/index.ts:1` | CLI 入口 |
| Kimi CLI | `kimi-cli/src/kimi_cli/main.py:1` | CLI 入口 |
| OpenCode | `packages/opencode/src/main.ts:1` | CLI 入口 |

### 5.2 Agent Loop 核心

| Agent | 文件路径 | 说明 |
|-------|----------|------|
| SWE-agent | `sweagent/agent/agents.py:200` | `DefaultAgent` 类 |
| Codex | `codex-rs/core/src/agent_loop.rs:150` | `AgentLoop` 结构体 |
| Gemini CLI | `packages/core/src/core/client.ts:100` | `GeminiClient.sendMessageStream` |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py:300` | `KimiSoul._agent_loop` |
| OpenCode | `packages/opencode/src/session/prompt.ts:200` | `SessionPrompt.loop` |

### 5.3 工具系统核心

| Agent | 文件路径 | 说明 |
|-------|----------|------|
| SWE-agent | `sweagent/tools/tools.py:1` | `ToolConfig` 类 |
| Codex | `codex-rs/core/src/tools/registry.rs:50` | `ToolRegistry` 结构体 |
| Gemini CLI | `packages/core/src/tools/tools.ts:80` | `ToolRegistry` 类 |
| Kimi CLI | `kimi-cli/src/kimi_cli/tools/__init__.py:50` | 工具初始化 |
| OpenCode | `packages/opencode/src/tool/tool.ts:75` | `Tool.define` 工厂函数 |

### 5.4 会话/上下文管理

| Agent | 文件路径 | 说明 |
|-------|----------|------|
| SWE-agent | `sweagent/agent/agents.py:80` | `AgentConfig` 配置 |
| Codex | `codex-rs/core/src/session.rs:100` | `Session` 结构体 |
| Gemini CLI | `packages/core/src/core/client.ts:80` | `GeminiClient` 会话 |
| Kimi CLI | `kimi-cli/src/kimi_cli/session.py:100` | `Session` 类 |
| OpenCode | `packages/opencode/src/session/session.ts:100` | Session 状态 |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 学术研究/可复现性 | SWE-agent | Docker 隔离，配置驱动，学术友好 |
| 性能敏感/企业安全 | Codex | Rust 高性能，原生沙箱 |
| IDE 集成/智能体 | Gemini CLI | Google 官方，IDE 支持好 |
| 多 Agent 协作 | Kimi CLI | ACP 协议，子 Agent 支持 |
| Agent 系统实验 | OpenCode | 多 Agent 架构，权限系统完善 |
