# Qwen Code 概述

Qwen Code 是基于 Google Gemini CLI 架构构建的开源 AI 编程助手，使用 TypeScript/React 技术栈，提供交互式终端 UI 和强大的代码辅助能力。

---

## 1. 项目简介

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 语言 | TypeScript | 类型安全的 Node.js 开发 |
| 运行时 | Node.js 20+ | 现代 JavaScript 运行时 |
| UI 框架 | React + Ink | 终端 React 渲染 |
| AI SDK | @google/genai | Google GenAI 官方 SDK |
| 包管理 | pnpm | Monorepo 工作流 |

### 项目定位

- **开源 AI 编程助手**：基于 Gemini CLI 架构的社区驱动版本
- **企业级功能**：支持 session 管理、工具调度、MCP 集成
- **IDE 集成**：原生支持 IDE 上下文同步
- **可扩展**：支持自定义工具和 MCP 服务器

---

## 2. 架构概览

### Monorepo 结构

```
qwen-code/
├── packages/
│   ├── cli/              # CLI 入口与交互层
│   │   ├── index.ts      # 程序入口
│   │   ├── src/gemini.tsx    # React UI 主程序
│   │   ├── src/core/initializer.ts   # 初始化流程
│   │   ├── src/ui/       # Ink 组件
│   │   └── src/commands/ # 子命令实现
│   │
│   └── core/             # 核心逻辑层
│       ├── src/core/     # Agent 核心
│       │   ├── client.ts     # GeminiClient
│       │   ├── turn.ts       # Turn 管理
│       │   └── geminiChat.ts # API 封装
│       ├── src/tools/    # 工具系统
│       │   ├── tool-registry.ts      # 工具注册表
│       │   ├── mcp-client-manager.ts # MCP 管理
│       │   └── *.ts      # 内置工具
│       └── src/services/ # 服务层
│           ├── sessionService.ts     # Session 管理
│           └── chatCompressionService.ts # 上下文压缩
│
├── docs/                 # 文档站点
└── integration-tests/    # 集成测试
```

### 分层架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI Layer                                │
│  qwen-code/packages/cli/index.ts:1                              │
│  ├─ 全局异常处理 (FatalError)                                     │
│  ├─ main() 入口                                                 │
│  └─ 子命令分发 (interactive/non-interactive)                    │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    App Container Layer                          │
│  qwen-code/packages/cli/src/gemini.tsx:209                      │
│  ├─ 配置加载与验证                                               │
│  ├─ 沙盒环境检查                                                 │
│  ├─ 交互式/非交互式模式切换                                       │
│  └─ React UI 渲染 (Ink)                                         │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     GeminiClient Layer                          │
│  qwen-code/packages/core/src/core/client.ts:78                  │
│  ├─ sendMessageStream()  # Agent Loop 入口                       │
│  ├─ processTurn()        # 单轮处理                              │
│  └─ LoopDetectionService # 循环检测                              │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Turn Layer                               │
│  qwen-code/packages/core/src/core/turn.ts:221                   │
│  ├─ Turn.run()           # 单轮流式处理                          │
│  ├─ 事件流解析 (Content/ToolCall/Thought)                        │
│  └─ 工具调用队列管理                                             │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Tools Layer                                │
│  qwen-code/packages/core/src/tools/                             │
│  ├─ tool-registry.ts     # 工具注册与发现                        │
│  ├─ mcp-client-manager.ts # MCP 客户端管理                       │
│  ├─ coreToolScheduler.ts  # 工具调度执行                         │
│  └─ handlers/            # 内置工具实现                          │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Services Layer                              │
│  qwen-code/packages/core/src/services/                          │
│  ├─ sessionService.ts    # JSONL 会话持久化                      │
│  ├─ chatCompressionService.ts # 上下文压缩                       │
│  └─ chatRecordingService.ts   # 聊天记录记录                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件列表

### 3.1 CLI 层

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 入口 | `packages/cli/index.ts:14` | 全局异常处理、主函数调用 |
| 主程序 | `packages/cli/src/gemini.tsx:209` | 配置加载、UI 渲染、模式分发 |
| 初始化器 | `packages/cli/src/core/initializer.ts:33` | 认证、主题、i18n 初始化 |

### 3.2 Core 层

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| GeminiClient | `packages/core/src/core/client.ts:78` | Agent Loop 主控、递归续跑 |
| Turn | `packages/core/src/core/turn.ts:221` | 单轮流式处理、事件解析 |
| GeminiChat | `packages/core/src/core/geminiChat.ts` | API 调用、流式响应处理 |

### 3.3 Tools 层

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| ToolRegistry | `packages/core/src/tools/tool-registry.ts:174` | 工具注册、发现、冲突处理 |
| McpClientManager | `packages/core/src/tools/mcp-client-manager.ts:29` | MCP 客户端生命周期管理 |
| CoreToolScheduler | `packages/core/src/core/coreToolScheduler.ts` | 工具执行调度 |

### 3.4 Services 层

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| SessionService | `packages/core/src/services/sessionService.ts:128` | 会话列表、恢复、删除 |
| ChatCompressionService | `packages/core/src/services/chatCompressionService.ts:78` | 历史压缩、token 管理 |

---

## 4. 源码索引表

### 4.1 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `packages/cli/index.ts` | 14 | 主入口，异常处理 |
| 主程序 | `packages/cli/src/gemini.tsx` | 209 | main() 函数 |
| 初始化 | `packages/cli/src/core/initializer.ts` | 33 | 应用初始化 |
| GeminiClient | `packages/core/src/core/client.ts` | 78 | 主客户端类 |
| sendMessageStream | `packages/core/src/core/client.ts` | 403 | Agent Loop 入口 |
| Turn | `packages/core/src/core/turn.ts` | 221 | Turn 管理 |
| Turn.run | `packages/core/src/core/turn.ts` | 233 | 单轮执行 |
| GeminiChat | `packages/core/src/core/geminiChat.ts` | 1 | API 封装 |

### 4.2 工具系统

| 组件 | 文件路径 | 说明 |
|------|----------|------|
| ToolRegistry | `packages/core/src/tools/tool-registry.ts` | 工具注册表 |
| McpClientManager | `packages/core/src/tools/mcp-client-manager.ts` | MCP 管理器 |
| McpClient | `packages/core/src/tools/mcp-client.ts` | MCP 客户端 |
| CoreToolScheduler | `packages/core/src/core/coreToolScheduler.ts` | 工具调度 |

### 4.3 内置工具

| 工具 | 文件路径 | 说明 |
|------|----------|------|
| read-file | `packages/core/src/tools/read-file.ts` | 文件读取 |
| write-file | `packages/core/src/tools/write-file.ts` | 文件写入 |
| edit | `packages/core/src/tools/edit.ts` | 文件编辑 |
| ls | `packages/core/src/tools/ls.ts` | 目录列表 |
| grep | `packages/core/src/tools/grep.ts` | 文本搜索 |
| shell | `packages/core/src/tools/shell.ts` | Shell 执行 |
| glob | `packages/core/src/tools/glob.ts` | 文件匹配 |
| web-fetch | `packages/core/src/tools/web-fetch.ts` | 网页获取 |
| memory | `packages/core/src/tools/memoryTool.ts` | 记忆存储 |
| todoWrite | `packages/core/src/tools/todoWrite.ts` | 待办事项 |

### 4.4 服务层

| 组件 | 文件路径 | 说明 |
|------|----------|------|
| SessionService | `packages/core/src/services/sessionService.ts` | 会话管理 |
| ChatCompressionService | `packages/core/src/services/chatCompressionService.ts` | 上下文压缩 |
| LoopDetectionService | `packages/core/src/services/loopDetectionService.ts` | 循环检测 |

---

## 5. 与 Gemini CLI 的关系

Qwen Code 基于 Gemini CLI 架构构建，保留了核心设计：

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 架构 | TypeScript/React/Ink | ✅ 继承 |
| Agent Loop | sendMessageStream + Turn.run | ✅ 继承 |
| 工具系统 | ToolRegistry + Scheduler | ✅ 继承 |
| MCP 集成 | McpClientManager | ✅ 继承 |
| 会话管理 | JSONL 格式 | ✅ 继承 |
| 上下文压缩 | 70% 阈值 | ✅ 继承 |

---

## 6. 总结

Qwen Code 是一个基于 Gemini CLI 架构的开源 AI 编程助手：

1. **架构清晰** - Monorepo 结构，cli/core 分层明确
2. **企业级特性** - Session 管理、上下文压缩、循环检测
3. **可扩展** - MCP 服务器、自定义工具、IDE 集成
4. **现代技术栈** - TypeScript/React/Ink，类型安全

> ⚠️ **Inferred**: 本文档基于 Gemini CLI 架构分析，qwen-code 具体实现细节以实际源码为准。
