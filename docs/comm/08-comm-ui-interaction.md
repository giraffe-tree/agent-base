# UI 交互模式对比

## 1. 概念定义

**UI 交互模式** 指 Agent CLI 与用户进行交互的方式和界面呈现形式。良好的交互设计能提升用户体验，让用户更好地理解和控制 Agent 的执行过程。

### 核心要素

- **输入方式**：用户如何输入指令
- **输出展示**：Agent 如何展示响应
- **流式处理**：实时展示模型输出
- **工具可视化**：展示工具调用和执行过程
- **状态反馈**：展示当前执行状态

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 使用简单的命令行交互，输出以文本为主，支持轨迹保存供后续分析。

**交互特点**

| 特性 | 实现方式 |
|------|----------|
| 输入 | 命令行参数 + 配置文件 |
| 输出 | 纯文本日志 |
| 流式输出 | 不支持（批量输出） |
| 工具展示 | 命令文本 |
| 状态反馈 | 日志级别（INFO/WARNING/ERROR） |

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Logger | `sweagent/utils/log.py` | 1 | 日志配置 |
| Trajectory | `sweagent/agent/agents.py` | 400 | 轨迹保存 |

**输出示例**

```
INFO:sweagent:Starting agent with config: {...}
INFO:sweagent:Step 1: Thought: Let me look at the files
INFO:sweagent:Action: ls -la
INFO:sweagent:Observation: total 32...
```

### 2.2 Codex

**实现概述**

Codex 提供 TUI（Terminal User Interface）和直接执行两种模式。TUI 基于 Ratatui 库，提供丰富的交互体验。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  TUI Mode (终端用户界面)                                  │
│  ┌─────────────────────────────────────────────────────┐│
│  │ ChatArea                    对话区域                ││
│  │ ├── User message            用户消息                ││
│  │ ├── Assistant response      助手响应                ││
│  │ └── Tool output             工具输出                ││
│  ├─────────────────────────────────────────────────────┤│
│  │ StatusBar                   状态栏                  ││
│  │ └── 当前状态/进度                                   ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Event Handling (事件处理)                                │
│  ├── Key events               键盘事件                │
│  ├── Mouse events             鼠标事件                │
│  └── Resize events            窗口调整                │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| TUI | `codex-rs/tui/src/lib.rs` | 1 | TUI 实现 |
| ChatArea | `codex-rs/tui/src/chat_area.rs` | 1 | 对话区域 |
| Event | `codex-rs/tui/src/event.rs` | 1 | 事件处理 |

**交互特点**

| 特性 | 实现方式 |
|------|----------|
| 输入 | TUI 输入框 / 命令行参数 |
| 输出 | 富文本 TUI / 纯文本 |
| 流式输出 | 支持（字符级） |
| 工具展示 | 可折叠的工具调用块 |
| 状态反馈 | 状态栏实时更新 |

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 提供基于 Node.js 的交互式 CLI，支持丰富的流式输出和事件驱动 UI。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  UI Layer (界面层)                                        │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Terminal UI                                           ││
│  │ ├── Content streaming     内容流式展示              ││
│  │ ├── Thought display       思考过程展示              ││
│  │ ├── Tool execution        工具执行状态              ││
│  │ └── Approval prompt       确认提示                ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Event System (事件系统)                                  │
│  ├── useGeminiStream        Hook 封装                 │
│  ├── submitQuery()          查询提交                  │
│  └── processGeminiStreamEvents() 事件处理             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Stream Events (流事件)                                   │
│  ├── Content                文本内容                  │
│  ├── Thought                思考内容                  │
│  ├── ToolCallRequest        工具调用请求              │
│  ├── ToolExecution          工具执行                  │
│  └── Finished               完成事件                  │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| UI | `packages/cli/src/ui/` | - | UI 组件 |
| useGeminiStream | `packages/cli/src/ui/hooks/useGeminiStream.ts` | 1 | Stream Hook |
| Events | `packages/core/src/core/events.ts` | 1 | 事件定义 |

**交互特点**

| 特性 | 实现方式 |
|------|----------|
| 输入 | 交互式输入 |
| 输出 | 流式 Markdown |
| 流式输出 | 支持（Token 级） |
| 工具展示 | 内联工具卡片 |
| 状态反馈 | 实时事件驱动 |

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 提供基于 asyncio 的流式交互，通过 Wire 协议与 UI 解耦，支持丰富的实时反馈。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Wire Protocol (通信协议)                                 │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Agent (KimiSoul)                                    ││
│  │ ├── TurnBegin/TurnEnd           Turn 事件          ││
│  │ ├── StepBegin/StepEnd           Step 事件          ││
│  │ ├── CompactionBegin/End         压缩事件           ││
│  │ └── ToolResult                  工具结果           ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  UI Rendering (界面渲染)                                  │
│  ├── 流式 Markdown          实时内容                  │
│  ├── 工具调用卡片           工具可视化                │
│  ├── Token 使用统计         资源监控                  │
│  └── 进度指示器             状态反馈                  │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Wire | `kimi-cli/src/kimi_cli/wire.py` | 1 | 通信协议 |
| Events | `kimi-cli/src/kimi_cli/events.py` | 1 | 事件定义 |
| UI | `kimi-cli/src/kimi_cli/render.py` | 1 | 渲染逻辑 |

**交互特点**

| 特性 | 实现方式 |
|------|----------|
| 输入 | 交互式输入 |
| 输出 | 流式 Markdown |
| 流式输出 | 支持（Token 级） |
| 工具展示 | 工具卡片 + 进度 |
| 状态反馈 | Turn/Step 事件 |

### 2.5 OpenCode

**实现概述**

OpenCode 提供交互式 CLI 和可选的 Web UI，基于 Bun/Node 和事件驱动架构。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  CLI Mode (命令行模式)                                    │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Interactive REPL                                      ││
│  │ ├── User input              用户输入                ││
│  │ ├── Assistant streaming     流式响应                ││
│  │ ├── Tool execution          工具执行                ││
│  │ └── Permission prompt       权限确认                ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Event Bus (事件总线)                                     │
│  ├── message.created        消息创建                  │
│  ├── part.updated           Part 更新                 │
│  ├── tool.executed          工具执行                  │
│  └── permission.requested   权限请求                  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Web Mode (可选 Web UI)                                   │
│  └── 基于 WebSocket 的实时同步                        │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Bus | `packages/opencode/src/bus.ts` | 1 | 事件总线 |
| Part | `packages/opencode/src/session/part.ts` | 1 | Part 更新 |
| UI | `packages/opencode/src/ui/` | - | UI 组件 |

**交互特点**

| 特性 | 实现方式 |
|------|----------|
| 输入 | 交互式输入 |
| 输出 | 流式 Markdown |
| 流式输出 | 支持（Part 级） |
| 工具展示 | Part 状态可视化 |
| 状态反馈 | Bus 事件驱动 |

---

## 3. 相同点总结

### 3.1 通用交互模式

| 模式 | 说明 | 支持 Agent |
|------|------|-----------|
| REPL | 交互式问答 | 全部 |
| 单次执行 | 单条指令 | Codex, Gemini CLI, Kimi CLI, OpenCode |
| 流式输出 | 实时展示 | Codex, Gemini CLI, Kimi CLI, OpenCode |
| 确认提示 | 危险操作确认 | 全部 |

### 3.2 流式输出实现

所有现代 Agent 都支持流式输出：

```
用户输入
    │
    ▼
┌─────────────────┐
│ LLM.stream()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 逐 Token/字符   │
│ 输出到 UI       │
└─────────────────┘
```

### 3.3 工具可视化

| Agent | 展示方式 | 特点 |
|-------|----------|------|
| SWE-agent | 纯文本 | 简单 |
| Codex | 可折叠块 | 交互式 |
| Gemini CLI | 内联卡片 | 美观 |
| Kimi CLI | 工具卡片 | 详细 |
| OpenCode | Part 状态 | 细粒度 |

---

## 4. 不同点对比

### 4.1 UI 技术栈

| Agent | 技术栈 | 特点 |
|-------|--------|------|
| SWE-agent | Python logging | 简单 |
| Codex | Ratatui (Rust) | 高性能 TUI |
| Gemini CLI | Node.js + 自定义 | 灵活 |
| Kimi CLI | Python rich | 美观 |
| OpenCode | Bun + 自定义 | 现代 |

### 4.2 流式粒度

| Agent | 粒度 | 延迟 |
|-------|------|------|
| SWE-agent | 无 | - |
| Codex | 字符级 | 低 |
| Gemini CLI | Token 级 | 低 |
| Kimi CLI | Token 级 | 低 |
| OpenCode | Part 级 | 中 |

### 4.3 事件系统

| Agent | 架构 | 特点 |
|-------|------|------|
| SWE-agent | 回调 | 简单 |
| Codex | Channel | Actor 模型 |
| Gemini CLI | Event Emitter | 标准 JS |
| Kimi CLI | Wire 协议 | 解耦 |
| OpenCode | Bus 总线 | 发布订阅 |

### 4.4 平台支持

| Agent | 终端 | Web | IDE |
|-------|------|-----|-----|
| SWE-agent | 是 | 否 | 否 |
| Codex | 是 | 否 | 否 |
| Gemini CLI | 是 | 否 | 是 |
| Kimi CLI | 是 | 否 | 否 |
| OpenCode | 是 | 是 | 否 |

---

## 5. 源码索引

### 5.1 UI 入口

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/tui/src/lib.rs` | 1 | TUI 入口 |
| Gemini CLI | `packages/cli/src/index.ts` | 1 | CLI 入口 |
| Kimi CLI | `kimi-cli/src/kimi_cli/main.py` | 100 | 渲染入口 |
| OpenCode | `packages/opencode/src/main.ts` | 1 | CLI 入口 |

### 5.2 流式输出

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/tui/src/chat_area.rs` | 100 | 流式渲染 |
| Gemini CLI | `packages/cli/src/ui/hooks/useGeminiStream.ts` | 1 | Stream Hook |
| Kimi CLI | `kimi-cli/src/kimi_cli/render.py` | 100 | 流式渲染 |
| OpenCode | `packages/opencode/src/session/processor.ts` | 200 | Part 更新 |

### 5.3 事件系统

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex-rs/tui/src/event.rs` | 1 | 事件处理 |
| Gemini CLI | `packages/core/src/core/events.ts` | 1 | 事件定义 |
| Kimi CLI | `kimi-cli/src/kimi_cli/events.py` | 1 | 事件定义 |
| OpenCode | `packages/opencode/src/bus.ts` | 1 | 事件总线 |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 简洁高效 | SWE-agent | 无干扰，专注任务 |
| 终端体验 | Codex | Ratatui TUI |
| 流式展示 | Gemini CLI | 事件驱动 |
| 协议解耦 | Kimi CLI | Wire 协议 |
| Web 访问 | OpenCode | Web UI 支持 |
