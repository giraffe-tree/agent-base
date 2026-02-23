# Web Server 模式

## TL;DR

只有 OpenCode 和 Kimi CLI 内置了完整的 Web Server 模式；其他项目通过 IDE 扩展或不支持。Web Server 模式的核心价值是**将 Agent 变成服务** —— 允许远程调用、多客户端接入，代价是引入网络安全风险。

---

## 设计动机：什么场景需要 Web Server？

**不需要 Web Server 的场景：**
- 个人开发辅助（本地 CLI 足够）
- 学术研究（批量执行，无交互）

**需要 Web Server 的场景：**
- 团队共享一个 Agent 服务（集中部署）
- IDE 插件需要后台服务支持
- 构建 Agent 平台（多用户、多 session）

---

## 工程取舍：本地 vs 服务化

| 维度 | 本地 CLI | Web Server |
|------|----------|------------|
| **部署复杂度** | 低（直接运行）| 高（需要管理服务进程）|
| **安全风险** | 低（本地访问）| 高（需要认证授权）|
| **多用户支持** | 否 | 是 |
| **IDE 集成** | 需要额外适配 | 原生 HTTP 接口 |
| **网络延迟** | 无 | 有（本地可忽略）|

---

## 各项目实现情况

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent **不支持 Web Server 模式**。它是纯命令行工具，专注于离线批处理任务。

| 特性 | 支持情况 |
|------|----------|
| HTTP Server | 否 |
| WebSocket | 否 |
| Web UI | 否 |

### 2.2 Codex

**实现概述**

Codex **不支持 Web Server 模式**。它专注于本地 TUI 体验，没有提供 HTTP 或 WebSocket 接口。

| 特性 | 支持情况 |
|------|----------|
| HTTP Server | 否 |
| WebSocket | 否 |
| Web UI | 否 |

**注意**：Codex 提供 IDE 扩展支持，但这与 Web Server 模式不同。

### 2.3 Gemini CLI

**实现概述**

Gemini CLI **支持 IDE 扩展模式**，但这不是传统意义上的 Web Server。它通过 LSP 类似的协议与 IDE 通信。

| 特性 | 支持情况 |
|------|----------|
| HTTP Server | 否 |
| WebSocket | 否 |
| IDE 扩展 | 是 |

**IDE 扩展架构**

```
┌─────────────────────────────────────────────────────────┐
│  IDE Extension (VS Code / JetBrains)                      │
│  └── 通过扩展协议与 Gemini CLI 通信                     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Gemini CLI (IDE Mode)                                    │
│  ├── 接收 IDE context                                   │
│  ├── 执行 Agent 循环                                    │
│  └── 返回结果到 IDE                                     │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| IDE Mode | `packages/core/src/ide/` | - | IDE 集成 |

### 2.4 Kimi CLI

**实现概述**

Kimi CLI **不支持 Web Server 模式**。它是纯命令行工具，专注于本地交互。

| 特性 | 支持情况 |
|------|----------|
| HTTP Server | 否 |
| WebSocket | 否 |
| Web UI | 否 |

### 2.5 OpenCode

**实现概述**

OpenCode **支持 Web Server 模式**，可以通过 `--server` 参数启动 HTTP 服务器和 WebSocket 服务。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Client (浏览器/其他客户端)                               │
│  ├── HTTP REST API                                      │
│  └── WebSocket Real-time                                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  OpenCode Server (服务器层)                               │
│  ┌─────────────────────────────────────────────────────┐│
│  │ HTTP Router                                           ││
│  │ ├── GET /sessions           会话列表                ││
│  │ ├── POST /sessions          创建会话                ││
│  │ ├── GET /sessions/:id       获取会话                ││
│  │ └── POST /sessions/:id/prompt 发送消息              ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ WebSocket Handler                                     ││
│  │ ├── connection              连接管理                ││
│  │ ├── message streaming       消息流式传输            ││
│  │ └── broadcast               广播通知                ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Session Management (会话管理)                            │
│  └── 与 CLI 模式共享核心逻辑                            │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Server | `packages/opencode/src/server/` | - | 服务器实现 |
| HTTP | `packages/opencode/src/server/http.ts` | 1 | HTTP 路由 |
| WebSocket | `packages/opencode/src/server/ws.ts` | 1 | WebSocket 处理 |

**启动方式**

```bash
# 启动服务器
opencode --server

# 指定端口
opencode --server --port 8080
```

**API 示例**

```bash
# 创建会话
curl -X POST http://localhost:8080/sessions

# 发送消息
curl -X POST http://localhost:8080/sessions/:id/prompt \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'

# 获取会话
curl http://localhost:8080/sessions/:id
```

---

## 3. 相同点总结

### 3.1 支持情况

| Agent | Web Server | 替代方案 |
|-------|------------|----------|
| SWE-agent | 否 | 批处理模式 |
| Codex | 否 | TUI |
| Gemini CLI | 否 | IDE 扩展 |
| Kimi CLI | 否 | CLI |
| OpenCode | 是 | - |

### 3.2 架构趋势

大多数 Agent CLI 专注于本地体验：

- **本地优先**：直接访问文件系统和 Shell
- **安全考虑**：减少网络暴露面
- **简单性**：避免服务器复杂性

---

## 4. 不同点对比

### 4.1 远程访问能力

| Agent | 远程访问 | 实现方式 |
|-------|----------|----------|
| SWE-agent | 否 | - |
| Codex | 否 | - |
| Gemini CLI | 否 | - |
| Kimi CLI | 否 | - |
| OpenCode | 是 | HTTP + WebSocket |

### 4.2 多客户端支持

| Agent | 多客户端 | 实现方式 |
|-------|----------|----------|
| SWE-agent | 否 | - |
| Codex | 否 | - |
| Gemini CLI | 否 | - |
| Kimi CLI | 否 | - |
| OpenCode | 是 | Session 隔离 |

### 4.3 集成方式

| Agent | 集成方式 | 特点 |
|-------|----------|------|
| SWE-agent | 命令行 | 批处理 |
| Codex | TUI | 交互式 |
| Gemini CLI | IDE 扩展 | 编辑器集成 |
| Kimi CLI | 命令行 | 交互式 |
| OpenCode | HTTP/WebSocket | Web 集成 |

---

## 5. 源码索引

### 5.1 Server 实现

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| OpenCode | `packages/opencode/src/server/` | - | 服务器目录 |
| OpenCode | `packages/opencode/src/server/http.ts` | 1 | HTTP 路由 |
| OpenCode | `packages/opencode/src/server/ws.ts` | 1 | WebSocket |

### 5.2 IDE 集成

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Gemini CLI | `packages/core/src/ide/` | - | IDE 集成 |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| Web 集成 | OpenCode | 唯一支持 HTTP/WebSocket |
| IDE 集成 | Gemini CLI | 官方 IDE 扩展 |
| 本地使用 | Codex/Kimi CLI | 优秀的 TUI 体验 |
| 批处理 | SWE-agent | 专注批处理任务 |

---

## 7. 补充：其他 Agent 的 Web 方案

虽然其他 Agent 不内置 Web Server，但可以通过以下方式实现类似功能：

### 7.1 代理方案

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web UI    │────▶│   Proxy     │────▶│   Agent     │
│  (自定义)   │     │  (WebSocket)│     │  (stdin)    │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 7.2 示例：为 Codex 添加 Web 界面

```typescript
// 使用 Node.js 包装 Codex
import { spawn } from 'child_process';
import { WebSocketServer } from 'ws';

const wss = new WebSocketServer({ port: 8080 });

wss.on('connection', (ws) => {
    const codex = spawn('codex', ['--repl']);

    ws.on('message', (data) => {
        codex.stdin.write(data + '\n');
    });

    codex.stdout.on('data', (data) => {
        ws.send(data.toString());
    });
});
```
