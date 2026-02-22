# ACP 是什么？——面向了解 MCP 和 Agent Loop 的初学者

## 一句话结论

**ACP（Agent Connect Protocol / Agent Communication Protocol）是一种让 Agent 之间互相通信、协作的协议。** 如果说 MCP 解决的是"Agent 如何调用外部工具"，那么 ACP 解决的是"Agent 如何与其他 Agent 协作"以及"Agent 如何被外部系统调用"。

---

## 目录

1. [你已经知道的：MCP 和 Agent Loop](#1-你已经知道的)
2. [ACP 要解决什么问题？](#2-acp-要解决什么问题)
3. [ACP 的核心概念](#3-acp-的核心概念)
4. [ACP 与 MCP 的关系](#4-acp-与-mcp-的关系)
5. [在 Kimi CLI 中的实际应用](#5-在-kimi-cli-中的实际应用)
6. [一图看懂三者关系](#6-一图看懂三者关系)
7. [常见问题](#7-常见问题)

---

## 1. 你已经知道的

在深入 ACP 之前，先确认一下你已经掌握的两个基础概念：

### Agent Loop（你已了解）

```text
while (任务未完成):
    response = LLM.推理(上下文)       # 模型思考下一步
    results  = 执行工具(response)      # 调用工具（读文件、跑命令等）
    上下文.更新(results)               # 把执行结果放回上下文
    if 任务完成(): break               # 判断是否结束
```

Agent Loop 是 AI Coding Agent 的核心循环——"思考 → 行动 → 观察 → 再思考"。

### MCP（你已了解）

```text
┌─────────┐         ┌─────────────┐         ┌─────────────┐
│  Agent  │──请求──▶│  MCP Client │──调用──▶│  MCP Server │
│         │◀──结果──│             │◀──返回──│  (外部工具)  │
└─────────┘         └─────────────┘         └─────────────┘
```

MCP（Model Context Protocol）让 Agent 能够通过标准化协议调用外部工具（数据库查询、API 调用、浏览器操作等）。

**到这里的关键认知**：MCP 是 Agent → 工具 的连接协议。

---

## 2. ACP 要解决什么问题？

### 当单个 Agent 不够用时

假设你让一个 Coding Agent 完成这样的任务：

> "重构整个项目的认证系统，从 session-based 迁移到 JWT，同时更新所有相关的 API 端点和测试。"

一个 Agent 独自处理这个任务会遇到什么问题？

```text
问题 1：上下文窗口不够
  → 认证模块 + API 端点 + 测试文件 = 可能超过模型上下文限制

问题 2：任务太复杂
  → 需要同时理解安全机制、API 设计、测试策略

问题 3：串行太慢
  → 如果能让多个 Agent 分工并行处理就好了
```

**这就是 ACP 要解决的问题：让多个 Agent 之间能协作。**

### 三种核心场景

| 场景 | 说明 | 类比 |
|------|------|------|
| **Agent ↔ Agent** | 两个 Agent 互相通信 | 两个程序员协作 |
| **外部系统 → Agent** | IDE/编辑器调用 Agent 的能力 | 项目经理给程序员派任务 |
| **Agent → 子 Agent** | 主 Agent 创建子 Agent 分发任务 | 技术负责人分配子任务给团队 |

---

## 3. ACP 的核心概念

### 3.1 ACP 是一个"Agent 服务化"协议

最简单的理解方式：**MCP 把工具变成服务，ACP 把 Agent 变成服务。**

```text
MCP 的世界观：
  Agent 是使用者，Tool 是被使用者
  Agent ──调用──▶ Tool

ACP 的世界观：
  Agent 既是使用者，也可以是被使用者
  Agent A ──调用──▶ Agent B（Agent B 像一个"智能工具"）
  IDE    ──调用──▶ Agent（Agent 像一个"服务"）
```

### 3.2 核心能力

| 能力 | 说明 |
|------|------|
| **Agent 发现** | 发现有哪些 Agent 可以调用 |
| **Agent 调用** | 向一个远程 Agent 发送任务请求 |
| **配置传递** | 告诉被调用的 Agent 它可以用哪些 MCP Server |
| **权限管理** | 管理 Agent 之间的权限审批 |
| **状态流式传输** | 实时获取被调用 Agent 的执行进度 |

### 3.3 ACP Server 模式

在 ACP 架构中，一个 Agent 可以作为 ACP Server 运行——等待外部的调用请求：

```text
┌─────────────────────────────────────────────────────────────┐
│  Kimi CLI 作为 ACP Server 运行                                │
│                                                               │
│  kimi acp     ← 启动 ACP 服务器模式                          │
│                                                               │
│  等待请求 ──▶ 收到任务 ──▶ 执行 Agent Loop ──▶ 返回结果       │
│                                                               │
│  外部客户端（IDE、其他 Agent）可以：                           │
│  ├── 发送编码任务                                             │
│  ├── 传入 MCP Server 配置（Agent 可用的工具）                  │
│  ├── 请求权限审批                                             │
│  └── 流式获取执行过程                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. ACP 与 MCP 的关系

### 4.1 互补而非替代

ACP 和 MCP 不是竞争关系，它们解决不同层次的问题：

| 维度 | MCP | ACP |
|------|-----|-----|
| **解决什么** | Agent 如何调用工具 | Agent 如何被调用 / Agent 之间如何协作 |
| **谁是服务端** | Tool Server（提供工具的进程） | Agent Server（提供 Agent 能力的进程） |
| **谁是客户端** | Agent（使用工具） | 另一个 Agent / IDE / 外部系统 |
| **通信内容** | 工具定义 + 工具调用 + 工具结果 | 任务描述 + 配置（含 MCP Server） + 执行结果 |
| **类比** | 程序员使用 IDE 的各种功能 | 项目经理给程序员派任务 |

### 4.2 ACP 可以"桥接" MCP

这是 Kimi CLI 的一个典型设计——通过 ACP 协议获取 MCP 配置：

```text
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  ACP Client  │─────▶│  ACP Server  │      │  MCP Server  │
│  (调用方)     │      │  (Kimi CLI)  │─────▶│  (实际工具)   │
│              │      │              │      │              │
│  发送任务 +   │      │  收到任务     │      │  被 Agent    │
│  MCP 配置     │      │  解析 MCP 配置│      │  调用执行     │
│              │      │  执行任务     │      │              │
└──────────────┘      └──────────────┘      └──────────────┘

流程：
1. ACP Client 告诉 Kimi CLI："帮我做 X 任务，你可以用这些 MCP Server"
2. Kimi CLI 将 ACP 格式的 MCP 配置转换为标准 MCP 配置
3. Kimi CLI 在执行任务时，调用这些 MCP Server 提供的工具
```

转换代码的核心逻辑：

```python
def acp_mcp_servers_to_mcp_config(mcp_servers: list[MCPServer]) -> MCPConfig:
    """将 ACP 协议传来的 MCP Server 配置，转换为 Agent 内部使用的标准格式"""
    # ACP 传来的配置格式 → 标准 MCP 配置格式
    # HttpMcpServer  → {"url": ..., "transport": "http"}
    # SseMcpServer   → {"url": ..., "transport": "sse"}
    # McpServerStdio → {"command": ..., "transport": "stdio"}
```

---

## 5. 在 Kimi CLI 中的实际应用

### 5.1 四种运行模式

Kimi CLI 支持四种运行模式，其中 `acp` 模式就是 ACP 的体现：

```text
kimi              → shell 模式（默认，交互式对话）
kimi --print      → print 模式（非交互，批处理）
kimi --acp        → ACP 模式（作为 ACP Server 运行，等待外部调用）
kimi --wire       → wire 模式（实验性协议）
```

### 5.2 ACP 在安全控制中的角色

当 Kimi CLI 以 ACP Server 模式运行时，权限审批通过 ACP 协议传递：

```text
正常交互模式：
  Agent 想执行 rm 命令 → 在终端弹出确认框 → 用户点击批准/拒绝

ACP 模式：
  Agent 想执行 rm 命令 → 通过 ACP 协议发送权限请求 → 调用方返回审批结果
```

### 5.3 ACP 子 Agent 创建

Kimi CLI 支持通过 ACP 协议创建子 Agent，用于任务分解：

```text
主 Agent（KimiSoul）
  │
  ├── 分析任务："重构认证系统"
  │
  ├── 创建子 Agent A（通过 ACP）
  │   └── 任务："重构 auth 模块"
  │
  ├── 创建子 Agent B（通过 ACP）
  │   └── 任务："更新 API 端点"
  │
  └── 汇总子 Agent 结果
```

---

## 6. 一图看懂三者关系

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         完整架构图                                    │
│                                                                       │
│   ┌─────────┐     ACP 协议      ┌─────────────────────────────────┐ │
│   │  IDE /  │ ─────────────────▶│       Agent (ACP Server)         │ │
│   │  外部   │ ◀─────────────────│                                   │ │
│   │  系统   │   任务+MCP配置     │   ┌───────────────────────┐     │ │
│   └─────────┘   /结果+流式状态   │   │    Agent Loop         │     │ │
│                                   │   │  ┌─────┐  ┌───────┐  │     │ │
│                                   │   │  │ LLM │─▶│ Tools │  │     │ │
│   ┌─────────┐     ACP 协议      │   │  └─────┘  └───┬───┘  │     │ │
│   │ 另一个  │ ─────────────────▶│   │       ▲       │      │     │ │
│   │ Agent   │ ◀─────────────────│   │       └───────┘      │     │ │
│   └─────────┘                    │   └───────────────────────┘     │ │
│                                   │              │                   │ │
│                                   │              │ MCP 协议          │ │
│                                   │              ▼                   │ │
│                                   │   ┌─────────────────────┐       │ │
│                                   │   │   MCP Servers       │       │ │
│                                   │   │  ├── 文件操作工具    │       │ │
│                                   │   │  ├── 数据库工具      │       │ │
│                                   │   │  └── 浏览器工具      │       │ │
│                                   │   └─────────────────────┘       │ │
│                                   └─────────────────────────────────┘ │
│                                                                       │
│   总结：                                                              │
│   • Agent Loop = Agent 内部的思考-行动循环                            │
│   • MCP = Agent 向下调用工具的协议（Agent → Tool）                    │
│   • ACP = Agent 向外暴露能力的协议（外部 → Agent / Agent → Agent）    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. 常见问题

### Q1: 所有 Agent 都支持 ACP 吗？

不是。在本仓库分析的 5 个项目中，只有 **Kimi CLI** 明确实现了 ACP 协议。

| 项目 | ACP 支持 | 多 Agent 协作方式 |
|------|----------|-------------------|
| Codex | 否 | 单 Agent |
| Gemini CLI | 否 | 单 Agent |
| **Kimi CLI** | **是** | ACP 协议 + 子 Agent |
| OpenCode | 否 | 内置多 Agent（Build/Plan/Explore），非 ACP |
| SWE-agent | 否 | 单 Agent |

### Q2: ACP 和 A2A（Agent-to-Agent）是什么关系？

Google 也提出了 A2A（Agent-to-Agent Protocol），目标类似——解决 Agent 之间的通信。ACP 和 A2A 都属于"Agent 间协议"这个赛道，但具体规范和生态不同。可以把它们理解为解决同一类问题的不同方案。

### Q3: 为什么需要 ACP？直接调用 API 不行吗？

直接调 API 当然可以，但 ACP 提供了标准化的好处：

| 方面 | 直接调 API | 使用 ACP |
|------|-----------|----------|
| 发现能力 | 需要硬编码 | 标准化发现机制 |
| 配置传递 | 自定义格式 | 统一的 MCP 配置传递 |
| 权限管理 | 各自实现 | 标准化审批流程 |
| 状态追踪 | 自己实现轮询 | 内置流式状态 |

类比：MCP 之于工具，就像 ACP 之于 Agent——都是为了**标准化接口、降低集成成本**。

### Q4: 作为初学者，我应该先学 MCP 还是 ACP？

**先学 MCP，再学 ACP。**

学习路径建议：

```text
Step 1: 理解 Agent Loop（你已完成 ✅）
  → Agent 如何循环推理和执行

Step 2: 理解 MCP（你已完成 ✅）
  → Agent 如何调用外部工具

Step 3: 理解 ACP（你正在这里 👈）
  → Agent 如何被外部调用 / Agent 之间如何协作

Step 4: 阅读具体实现
  → 推荐从 Kimi CLI 的 ACP 实现开始
  → 关键文件：kimi-cli/src/kimi_cli/acp/ 目录
```

---

## 8. 延伸阅读

- [MCP 集成对比](docs/comm/06-comm-mcp-integration.md) — 5 个项目如何实现 MCP
- [Kimi CLI MCP 集成](docs/kimi-cli/06-kimi-cli-mcp-integration.md) — ACP 到 MCP 桥接的详细分析
- [Kimi CLI 工具系统](docs/kimi-cli/05-kimi-cli-tools-system.md) — ACP MCP 集成在工具系统中的位置
- [Kimi CLI 安全控制](docs/kimi-cli/10-kimi-cli-safety-control.md) — ACP 模式下的权限审批
- [跨 Agent 概述对比](docs/comm/01-comm-overview.md) — 5 个项目的整体对比

---

*文档版本: 2026-02-22*
*面向读者: 了解 MCP 协议和 Agent Loop 的初学者*
