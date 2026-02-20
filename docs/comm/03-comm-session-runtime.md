# Session 与运行时对比

## 1. 概念定义

**Session（会话）** 是 Agent 与用户一次完整交互的上下文容器，负责管理：
- 对话历史（用户消息和助手响应）
- 工具执行状态
- 文件系统上下文
- 运行时配置

**Runtime（运行时）** 是 Agent 执行的基础环境，负责：
- 提供执行上下文
- 管理资源生命周期
- 处理并发和异步操作
- 提供工具和能力注册

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 的 Session 管理相对简单，主要通过 `Agent` 类维护状态。Runtime 通过 `SWEEnv` 提供 Docker 容器环境。

**核心组件**

```
┌─────────────────────────────────────────────────────────┐
│  Agent (Session 管理者)                                   │
├─────────────────────────────────────────────────────────┤
│  - history: list[dict]     对话历史                      │
│  - config: AgentConfig     Agent 配置                   │
│  - model: BaseModel        模型接口                     │
│  - tools: ToolConfig       工具配置                     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  SWEEnv (Runtime 环境)                                    │
├─────────────────────────────────────────────────────────┤
│  - container: Container    Docker 容器                  │
│  - workdir: Path           工作目录                     │
│  - communicate()           执行命令                     │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Agent | `sweagent/agent/agents.py` | 200 | Session 管理 |
| SWEEnv | `sweagent/environment/` | - | Runtime 环境 |
| History | `sweagent/agent/agents.py` | 250 | 历史记录管理 |

### 2.2 Codex

**实现概述**

Codex 的 Session 是核心概念，使用 Rust 的 Arc 和 RwLock 实现线程安全的状态共享。Runtime 通过 tokio 异步运行时驱动。

**核心组件**

```
┌─────────────────────────────────────────────────────────┐
│  Session (会话上下文)                                     │
├─────────────────────────────────────────────────────────┤
│  - conversation_id: String  会话 ID                     │
│  - cwd: PathBuf             当前工作目录                │
│  - storage: Arc<dyn Storage> 存储后端                   │
│  - hooks: Arc<Hooks>        Hook 集合                   │
│  - mcp_client_manager       MCP 管理器                  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  TurnContext (Turn 运行时)                                │
├─────────────────────────────────────────────────────────┤
│  - session: Arc<Session>    父会话                      │
│  - sub_id: String           Turn ID                     │
│  - tools_config             工具配置                    │
│  - tool_call_gate           工具调用门控                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  tokio Runtime (异步运行时)                               │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Session | `codex-rs/core/src/session.rs` | 100 | 会话结构体 |
| TurnContext | `codex-rs/core/src/turn_context.rs` | 1 | Turn 上下文 |
| Storage | `codex-rs/core/src/storage/` | - | 存储抽象 |

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 的 Session 管理通过 `GeminiClient` 实现，支持会话级和 prompt 级状态。Runtime 通过 Node.js 事件循环驱动。

**核心组件**

```
┌─────────────────────────────────────────────────────────┐
│  GeminiClient (会话管理器)                                │
├─────────────────────────────────────────────────────────┤
│  - sessionTurnCount: number      会话轮次计数           │
│  - maxSessionTurns: number       最大轮次限制           │
│  - currentSequenceModel          当前模型               │
│  - toolRegistry                  工具注册表             │
│  - mcpClientManager              MCP 管理器             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Turn (单轮运行时)                                        │
├─────────────────────────────────────────────────────────┤
│  - id: string                    Turn ID                │
│  - events: EventEmitter          事件流                 │
│  - process()                     执行逻辑               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Scheduler (工具执行状态机)                               │
├─────────────────────────────────────────────────────────┤
│  - state: State                  当前状态               │
│  - schedule()                    调度执行               │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| GeminiClient | `packages/core/src/core/client.ts` | 80 | 会话管理 |
| Turn | `packages/core/src/core/turn.ts` | 1 | Turn 实现 |
| Scheduler | `packages/core/src/scheduler/` | - | 调度器 |

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 的 Session 通过 `Session` 类管理上下文，`KimiSoul` 负责运行时协调。支持 checkpoint 机制实现状态回滚。

**核心组件**

```
┌─────────────────────────────────────────────────────────┐
│  Session (会话状态)                                       │
├─────────────────────────────────────────────────────────┤
│  - id: str                       会话 ID                │
│  - context: Context              上下文                 │
│  - checkpoints: list[Checkpoint] 检查点                 │
│  - wire: Wire                    通信通道               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Context (上下文管理)                                     │
├─────────────────────────────────────────────────────────┤
│  - history: list[Message]        消息历史               │
│  - token_count: int              Token 计数             │
│  - checkpoint()                  创建检查点             │
│  - revert_to()                   回滚到检查点           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  KimiSoul (运行时核心)                                    │
├─────────────────────────────────────────────────────────┤
│  - session: Session              关联会话               │
│  - agent_loop()                  Agent 循环             │
│  - step()                        单步执行               │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Session | `kimi-cli/src/kimi_cli/session.py` | 100 | 会话类 |
| Context | `kimi-cli/src/kimi_cli/context.py` | 1 | 上下文 |
| KimiSoul | `kimi-cli/src/kimi_cli/agent/soul.py` | 300 | 运行时核心 |

### 2.5 OpenCode

**实现概述**

OpenCode 的 Session 基于数据库持久化，支持消息 Part 级别的状态管理。Runtime 通过 Bun/Node 异步运行时驱动。

**核心组件**

```
┌─────────────────────────────────────────────────────────┐
│  SessionV2 (会话实体)                                     │
├─────────────────────────────────────────────────────────┤
│  - id: string                    会话 ID                │
│  - title: string                 会话标题               │
│  - status: Status                状态 (busy/idle)       │
│  - created_at, updated_at        时间戳                 │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  MessageV2 (消息实体)                                     │
├─────────────────────────────────────────────────────────┤
│  - id: string                    消息 ID                │
│  - sessionID: string             所属会话               │
│  - type: user/assistant          消息类型               │
│  - parts: Part[]                 内容片段               │
│  - finish?: string               结束原因               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Part (内容片段)                                          │
├─────────────────────────────────────────────────────────┤
│  - id: string                    Part ID                │
│  - type: text/tool/reasoning     类型                   │
│  - status: pending/running       状态                   │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| SessionV2 | `packages/opencode/src/session/session.ts` | 100 | 会话定义 |
| MessageV2 | `packages/opencode/src/session/message.ts` | 1 | 消息实体 |
| Part | `packages/opencode/src/session/part.ts` | 1 | 内容片段 |

---

## 3. 相同点总结

### 3.1 会话生命周期

| 阶段 | 说明 |
|------|------|
| 创建 | 初始化会话 ID、加载配置 |
| 运行 | 处理用户输入，执行 Agent 循环 |
| 持久化 | 保存对话历史到存储 |
| 恢复 | 从存储加载历史会话 |
| 结束 | 清理资源，关闭连接 |

### 3.2 通用状态

所有 Agent 都管理以下状态：

- **会话标识**：唯一 ID
- **对话历史**：用户和助手的消息记录
- **工作目录**：当前操作的文件系统路径
- **工具状态**：可用工具和它们的配置
- **Token 计数**：上下文使用量

### 3.3 运行时特性

| 特性 | 支持 Agent |
|------|-----------|
| 异步执行 | 全部 |
| 流式输出 | 全部 |
| 工具调用 | 全部 |
| 错误恢复 | Codex, OpenCode |

---

## 4. 不同点对比

### 4.1 状态管理方式

| Agent | 存储方式 | 持久化 | 查询能力 |
|-------|----------|--------|----------|
| SWE-agent | 内存 | 可选 JSON | 无 |
| Codex | SQLite | 自动 | SQL 查询 |
| Gemini CLI | 内存 + 文件 | 手动 | 有限 |
| Kimi CLI | 内存 | 无 | 无 |
| OpenCode | SQLite | 自动 | SQL + 索引 |

### 4.2 上下文结构

| Agent | 上下文单位 | 组织方式 | 特点 |
|-------|-----------|----------|------|
| SWE-agent | Message | 列表 | 简单线性 |
| Codex | Conversation + Turn | 嵌套 | Actor 模型 |
| Gemini CLI | Session + Turn | 嵌套 | 事件驱动 |
| Kimi CLI | Context + Message | 树形 | Checkpoint 支持 |
| OpenCode | Session + Message + Part | 三层 | 最细粒度 |

### 4.3 运行时架构

| Agent | 运行时 | 并发模型 | 特点 |
|-------|--------|----------|------|
| SWE-agent | Python | 同步/多线程 | 简单直接 |
| Codex | tokio | 异步/多任务 | 高性能 |
| Gemini CLI | Node.js | 事件循环 | 标准 JS |
| Kimi CLI | asyncio | 异步/协程 | Pythonic |
| OpenCode | Bun/Node | 事件循环 | 现代 JS |

### 4.4 状态恢复机制

| Agent | 回滚能力 | 实现方式 | 应用场景 |
|-------|----------|----------|----------|
| SWE-agent | 否 | 无 | - |
| Codex | 否 | 无 | - |
| Gemini CLI | 否 | 无 | - |
| Kimi CLI | 是 | Checkpoint + revert | D-Mail 时间线 |
| OpenCode | 否 | 无 | - |

### 4.5 资源管理

| Agent | 资源隔离 | 清理策略 | 特点 |
|-------|----------|----------|------|
| SWE-agent | Docker 容器 | 会话结束关闭 | 强隔离 |
| Codex | Seatbelt/Landlock | 自动清理 | 系统级 |
| Gemini CLI | 无 | 无 | 轻量 |
| Kimi CLI | 无 | 无 | 轻量 |
| OpenCode | 无 | 无 | 轻量 |

---

## 5. 源码索引

### 5.1 Session 定义

| Agent | 文件路径 | 行号 | 类/结构体 |
|-------|----------|------|-----------|
| SWE-agent | `sweagent/agent/agents.py` | 200 | `DefaultAgent` |
| Codex | `codex-rs/core/src/session.rs` | 100 | `Session` |
| Gemini CLI | `packages/core/src/core/client.ts` | 80 | `GeminiClient` |
| Kimi CLI | `kimi-cli/src/kimi_cli/session.py` | 100 | `Session` |
| OpenCode | `packages/opencode/src/session/session.ts` | 100 | `SessionV2` |

### 5.2 运行时核心

| Agent | 文件路径 | 行号 | 类/函数 |
|-------|----------|------|---------|
| SWE-agent | `sweagent/environment/swe_env.py` | 100 | `SWEEnv` |
| Codex | `codex-rs/core/src/agent_loop.rs` | 150 | `AgentLoop` |
| Gemini CLI | `packages/core/src/core/client.ts` | 100 | `sendMessageStream` |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/soul.py` | 300 | `KimiSoul` |
| OpenCode | `packages/opencode/src/session/prompt.ts` | 200 | `SessionPrompt` |

### 5.3 存储/持久化

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/agent/agents.py` | 400 | `save_trajectory` |
| Codex | `codex-rs/core/src/storage/sqlite.rs` | 1 | SQLite 存储 |
| Gemini CLI | `packages/core/src/persistence/` | - | 文件持久化 |
| Kimi CLI | 无 | - | 无持久化 |
| OpenCode | `packages/opencode/src/db/` | - | SQLite + Drizzle |

---

## 6. 架构特点总结

### 6.1 设计哲学对比

| Agent | 设计目标 | 复杂度 | 适用场景 |
|-------|----------|--------|----------|
| SWE-agent | 学术研究 | 中 | 可复现实验 |
| Codex | 性能与安全 | 高 | 企业级应用 |
| Gemini CLI | IDE 集成 | 中 | 开发者工具 |
| Kimi CLI | 灵活性 | 中 | 探索性任务 |
| OpenCode | 可扩展性 | 高 | Agent 系统实验 |

### 6.2 选择建议

- **需要强隔离**：选择 SWE-agent（Docker）或 Codex（Seatbelt）
- **需要状态回滚**：选择 Kimi CLI（Checkpoint 机制）
- **需要细粒度状态**：选择 OpenCode（Part 级别管理）
- **需要持久化**：选择 Codex 或 OpenCode（SQLite）
- **需要高性能**：选择 Codex（Rust/tokio）
