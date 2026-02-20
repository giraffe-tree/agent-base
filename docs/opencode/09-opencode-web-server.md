# Web Server（opencode）

本文基于 `packages/opencode/src/server/` 源码，解释 opencode 的全功能 Web 服务器如何设计和实现。该服务器基于 Hono 框架和 Bun 运行时，支持 WebSocket、SSE、MCP 和 ACP 多种协议。

---

## 1. 先看全局（流程图）

### 1.1 Hono + Bun 架构流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 启动 Web 服务器                                          │
│  ┌─────────────────┐                                            │
│  │ opencode serve  │ ◄──── headless 模式（仅 API）              │
│  │ opencode web    │ ◄──── 带 Web UI 模式                       │
│  │ opencode acp    │ ◄──── ACP 协议模式                         │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  配置阶段                                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ ServerConfig                           │                     │
│  │  ├── port: number = 8080               │ ──► 服务端口       │
│  │  ├── host: string = "0.0.0.0"          │ ──► 绑定地址       │
│  │  ├── mode: "serve|web|acp"             │ ──► 运行模式       │
│  │  ├── mcp: MCPConfig                    │ ──► MCP 服务器配置 │
│  │  └── acp: ACPConfig                    │ ──► ACP 协议配置   │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  启动 Hono + Bun.serve                                           │
│  ┌────────────────────────────────────────┐                     │
│  │ createServer(config)                   │                     │
│  │  ├── createHonoApp()                   │ ──► Hono 实例      │
│  │  │   ├── 中间件: logger               │                     │
│  │  │   ├── 中间件: cors                 │                     │
│  │  │   └── 中间件: errorHandler         │                     │
│  │  ├── 注册模块化路由                    │                     │
│  │  │   ├── /global/*                    │                     │
│  │  │   ├── /session/*                   │                     │
│  │  │   ├── /project/*                   │                     │
│  │  │   ├── /mcp/*                       │                     │
│  │  │   └── /pty/*                       │                     │
│  │  └── Bun.serve({                      │ ──► Bun 原生服务器 │
│  │       fetch: app.fetch,               │                     │
│  │       websocket: wsHandler            │ ──► WebSocket 支持 │
│  │     })                                │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  运行时（多协议支持）                                              │
│  ┌────────────────────────────────────────┐                     │
│  │ 路由分发:                              │                     │
│  │                                        │                     │
│  │  HTTP REST API  ◄── Hono Router        │                     │
│  │  WebSocket PTY  ◄── Bun WebSocket      │                     │
│  │  SSE Event      ◄── /event 端点        │                     │
│  │  MCP Protocol   ◄── /mcp/* 路由        │                     │
│  │  ACP Protocol   ◄── acp 模式专用       │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块/配置  ──► 流程  ──► 数据流向
```

### 1.2 路由组织结构图

```text
┌────────────────────────────────────────────────────────────────────┐
│                          Hono Router                                │
└────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │  请求入口       │
                    │  app.fetch()    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  全局中间件     │
                    │  logger/cors    │
                    └────────┬────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
   ┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
   │ /global/*       │ │ /session/*  │ │ /project/*      │
   │                 │ │             │ │                 │
   │ • /health       │ │ • GET /     │ │ • GET /         │
   │ • /config       │ │ • POST /    │ │ • PUT /         │
   │ • /event (SSE)  │ │ • /:id/msg  │ │ • /files/*      │
   │                 │ │ • /:id/exit │ │                 │
   └─────────────────┘ └─────────────┘ └─────────────────┘
            │                │                │
            │                │                │
            ▼                ▼                ▼
   ┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
   │ /mcp/*          │ │ /pty/*      │ │ /acp/* (模式)   │
   │                 │ │ (WebSocket) │ │                 │
   │ • /servers      │ │             │ │ • ACP 协议端点  │
   │ • /tools        │ │ • /:id/     │ │ • Agent 注册    │
   │ • /resources    │ │   connect   │ │ • 任务协商      │
   │ • /prompts      │ │             │ │                 │
   └─────────────────┘ └─────────────┘ └─────────────────┘

图例: ┌─┐ 路由模块  • 端点  ──► 请求分发
```

### 1.3 通信协议栈图

```text
┌────────────────────────────────────────────────────────────────────┐
│                       通信协议分层架构                                │
└────────────────────────────────────────────────────────────────────┘

应用层
┌────────────────────────────────────────────────────────────────────┐
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐   │
│  │ REST API      │  │ MCP Protocol  │  │ ACP Protocol          │   │
│  │ (CRUD)        │  │ (工具/资源)   │  │ (Agent 协作)          │   │
│  └───────┬───────┘  └───────┬───────┘  └───────────┬───────────┘   │
└──────────┼──────────────────┼──────────────────────┼───────────────┘
           │                  │                      │
传输层     ▼                  ▼                      ▼
┌────────────────────────────────────────────────────────────────────┐
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Bun.serve()                              │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │ HTTP/1.1    │  │ WebSocket   │  │ Server-Sent Events  │  │   │
│  │  │ (REST)      │  │ (PTY/实时)  │  │ (Event Stream)      │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘

图例: 分层架构，上层协议基于下层传输
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道这是一个基于 Hono + Bun 的多协议 Web 服务器）。
- **3 分钟版**：看 `1.1` + `1.2` + `1.3` + `4` + `5`（知道路由结构和通信协议）。
- **10 分钟版**：通读 `3~10`（能定位路由、WebSocket、MCP/ACP 问题）。

### 2.1 一句话定义

opencode 的 Web Server 是一个**基于 Hono 框架和 Bun 运行时的全功能服务器**，支持**WebSocket PTY**、**SSE 事件流**、**MCP 协议**和**ACP 协议**，提供模块化路由组织和多协议通信能力。

---

## 3. 核心组件

### 3.1 Hono 框架

```typescript
// packages/opencode/src/server/server.ts
import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { logger } from 'hono/logger';

export function createHonoApp(config: ServerConfig): Hono {
  const app = new Hono();

  // 全局中间件
  app.use(logger());
  app.use(cors({
    origin: '*',
    allowMethods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
    allowHeaders: ['Content-Type', 'Authorization'],
  }));

  // 错误处理
  app.onError((err, c) => {
    console.error('Server error:', err);
    return c.json({ error: err.message }, 500);
  });

  // 注册模块化路由
  app.route('/global', globalRoutes);
  app.route('/session', sessionRoutes);
  app.route('/project', projectRoutes);
  app.route('/mcp', mcpRoutes);
  app.route('/pty', ptyRoutes);

  return app;
}
```

**关键设计决策**：
- Hono：轻量级、高性能、Edge 运行时友好
- 模块化路由：各功能域独立文件，便于维护
- Bun 原生：利用 Bun 的高性能 HTTP 和 WebSocket 实现

### 3.2 Bun.serve 启动

```typescript
// packages/opencode/src/server/server.ts
import type { ServeOptions, WebSocketHandler } from 'bun';

export function createServer(config: ServerConfig) {
  const app = createHonoApp(config);

  const server = Bun.serve({
    port: config.port,
    hostname: config.host,

    // HTTP 请求处理
    fetch: (req, server) => {
      // WebSocket 升级处理
      if (req.url.includes('/pty/')) {
        const success = server.upgrade(req);
        if (success) return undefined;
      }
      return app.fetch(req, server);
    },

    // WebSocket 处理器
    websocket: createWebSocketHandler(config),
  });

  return server;
}
```

### 3.3 模块化路由组织

```typescript
// packages/opencode/src/server/routes/session.ts
import { Hono } from 'hono';

const app = new Hono();

// GET /session/ - 列出所有 sessions
app.get('/', async (c) => {
  const sessions = await sessionManager.list();
  return c.json(sessions);
});

// POST /session/ - 创建新 session
app.post('/', async (c) => {
  const body = await c.req.json();
  const session = await sessionManager.create(body);
  return c.json(session, 201);
});

// POST /session/:id/message - 发送消息
app.post('/:id/message', async (c) => {
  const id = c.req.param('id');
  const body = await c.req.json();
  const response = await sessionManager.sendMessage(id, body);
  return c.json(response);
});

// POST /session/:id/exit - 关闭 session
app.post('/:id/exit', async (c) => {
  const id = c.req.param('id');
  await sessionManager.close(id);
  return c.json({ success: true });
});

export const sessionRoutes = app;
```

---

## 4. API 路由详解

### 4.1 Global 路由

| 端点 | 方法 | 功能 |
|------|------|------|
| `/global/health` | GET | 健康检查 |
| `/global/config` | GET/PUT | 全局配置管理 |
| `/global/event` | GET | SSE 全局事件流 |

**SSE 事件流实现**：
```typescript
// packages/opencode/src/server/routes/global.ts
app.get('/event', async (c) => {
  const stream = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();

      // 订阅全局事件
      const unsubscribe = eventBus.subscribe((event) => {
        const data = `data: ${JSON.stringify(event)}\n\n`;
        controller.enqueue(encoder.encode(data));
      });

      // 清理函数
      c.req.raw.signal.addEventListener('abort', () => {
        unsubscribe();
        controller.close();
      });
    }
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
});
```

### 4.2 Session 路由

| 端点 | 方法 | 功能 |
|------|------|------|
| `/session/` | GET | 列出 sessions |
| `/session/` | POST | 创建 session |
| `/session/:id` | GET | 获取 session 详情 |
| `/session/:id/message` | POST | 发送消息 |
| `/session/:id/exit` | POST | 关闭 session |

### 4.3 Project 路由

| 端点 | 方法 | 功能 |
|------|------|------|
| `/project/` | GET | 获取项目信息 |
| `/project/` | PUT | 更新项目配置 |
| `/project/files/*` | GET/PUT | 文件操作 |

### 4.4 MCP 路由

| 端点 | 方法 | 功能 |
|------|------|------|
| `/mcp/servers` | GET/POST | MCP 服务器管理 |
| `/mcp/tools` | GET | 列出可用工具 |
| `/mcp/tools/:name` | POST | 调用工具 |
| `/mcp/resources` | GET | 列出资源 |
| `/mcp/prompts` | GET | 列出提示词模板 |

---

## 5. WebSocket (PTY)

### 5.1 PTY 终端实现

```typescript
// packages/opencode/src/server/routes/pty.ts
import type { ServerWebSocket } from 'bun';
import { Pty } from '../pty/pty';

interface PTYSession {
  socket: ServerWebSocket<unknown>;
  pty: Pty;
}

const ptySessions = new Map<string, PTYSession>();

export function createWebSocketHandler(config: ServerConfig): WebSocketHandler {
  return {
    open(ws) {
      const sessionId = extractSessionId(ws.data);

      // 创建 PTY 实例
      const pty = new Pty({
        command: 'bash',
        args: [],
        cwd: config.projectPath,
        onData: (data) => {
          ws.send(JSON.stringify({ type: 'output', data }));
        },
        onExit: (code) => {
          ws.send(JSON.stringify({ type: 'exit', code }));
          ws.close();
        },
      });

      ptySessions.set(sessionId, { socket: ws, pty });
    },

    message(ws, message) {
      const sessionId = extractSessionId(ws.data);
      const session = ptySessions.get(sessionId);

      if (session) {
        const data = JSON.parse(message.toString());

        switch (data.type) {
          case 'input':
            session.pty.write(data.data);
            break;
          case 'resize':
            session.pty.resize(data.cols, data.rows);
            break;
          case 'signal':
            session.pty.kill(data.signal);
            break;
        }
      }
    },

    close(ws) {
      const sessionId = extractSessionId(ws.data);
      const session = ptySessions.get(sessionId);

      if (session) {
        session.pty.kill();
        ptySessions.delete(sessionId);
      }
    },
  };
}
```

### 5.2 前端 PTY 客户端

```typescript
// 前端 WebSocket PTY 连接
const ws = new WebSocket('ws://localhost:8080/pty/terminal-001');

// 发送输入
function sendInput(input: string) {
  ws.send(JSON.stringify({ type: 'input', data: input }));
}

// 调整终端大小
function resize(cols: number, rows: number) {
  ws.send(JSON.stringify({ type: 'resize', cols, rows }));
}

// 接收输出
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'output') {
    terminal.write(msg.data);
  }
};
```

---

## 6. SSE 事件流

### 6.1 全局事件总线

```typescript
// packages/opencode/src/server/events/bus.ts
type EventHandler = (event: Event) => void;

class EventBus {
  private handlers = new Set<EventHandler>();

  subscribe(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  emit(event: Event) {
    this.handlers.forEach(handler => handler(event));
  }
}

export const eventBus = new EventBus();
```

### 6.2 事件类型

```typescript
// packages/opencode/src/server/events/types.ts
interface Event {
  type: 'session.started' | 'session.message' | 'session.ended' |
        'mcp.tool_called' | 'mcp.server_connected' |
        'file.changed' | 'config.updated';
  timestamp: number;
  data: unknown;
}
```

---

## 7. MCP 协议支持

### 7.1 MCP 服务器管理

```typescript
// packages/opencode/src/server/routes/mcp.ts
import { McpServerManager } from '../mcp/manager';

const mcpManager = new McpServerManager();

// 列出已连接的 MCP 服务器
app.get('/servers', async (c) => {
  const servers = await mcpManager.listServers();
  return c.json(servers);
});

// 添加 MCP 服务器
app.post('/servers', async (c) => {
  const config = await c.req.json();
  const server = await mcpManager.addServer(config);
  return c.json(server, 201);
});

// 调用 MCP 工具
app.post('/tools/:name', async (c) => {
  const name = c.req.param('name');
  const args = await c.req.json();
  const result = await mcpManager.callTool(name, args);
  return c.json(result);
});

// 获取 MCP 资源
app.get('/resources/:uri', async (c) => {
  const uri = c.req.param('uri');
  const resource = await mcpManager.getResource(uri);
  return c.json(resource);
});
```

### 7.2 MCP 协议实现

```typescript
// packages/opencode/src/mcp/manager.ts
export class McpServerManager {
  private servers = new Map<string, McpClient>();

  async addServer(config: McpServerConfig): Promise<McpServer> {
    const client = new McpClient();

    // 使用 stdio 或 sse 传输连接
    const transport = config.transport === 'stdio'
      ? new StdioTransport(config.command, config.args)
      : new SseTransport(config.url);

    await client.connect(transport);

    // 获取服务器能力
    const capabilities = await client.initialize();

    this.servers.set(config.name, client);

    return {
      name: config.name,
      capabilities,
      status: 'connected',
    };
  }

  async callTool(name: string, args: Record<string, unknown>) {
    const [serverName, toolName] = name.split('.');
    const client = this.servers.get(serverName);

    if (!client) {
      throw new Error(`MCP server not found: ${serverName}`);
    }

    return await client.callTool(toolName, args);
  }
}
```

---

## 8. ACP 协议支持

### 8.1 ACP 模式启动

```typescript
// packages/opencode/src/server/acp.ts
import { AcpAgent } from './acp/agent';

export function startAcpServer(config: ACPConfig) {
  const app = new Hono();

  const agent = new AcpAgent({
    name: config.name,
    description: config.description,
    capabilities: config.capabilities,
    onTask: handleAcpTask,
  });

  // ACP 协议端点
  app.get('/.well-known/agent-card.json', (c) => {
    return c.json(agent.getAgentCard());
  });

  app.post('/', async (c) => {
    const message = await c.req.json();
    const response = await agent.handleMessage(message);
    return c.json(response);
  });

  // SSE 流式响应支持
  app.post('/stream', async (c) => {
    const message = await c.req.json();
    const stream = agent.handleMessageStream(message);

    return new Response(streamToSSE(stream), {
      headers: { 'Content-Type': 'text/event-stream' },
    });
  });

  return Bun.serve({ port: config.port, fetch: app.fetch });
}
```

### 8.2 ACP Agent 实现

```typescript
// packages/opencode/src/acp/agent.ts
export class AcpAgent {
  constructor(private config: AcpAgentConfig) {}

  getAgentCard(): AgentCard {
    return {
      name: this.config.name,
      description: this.config.description,
      url: this.config.url,
      version: '1.0.0',
      capabilities: this.config.capabilities,
      skills: this.config.skills,
    };
  }

  async handleMessage(message: AcpMessage): Promise<AcpResponse> {
    switch (message.method) {
      case 'tasks/send':
        return this.handleTask(message.params);
      case 'tasks/get':
        return this.getTaskStatus(message.params);
      default:
        throw new Error(`Unknown method: ${message.method}`);
    }
  }

  async *handleMessageStream(message: AcpMessage): AsyncGenerator<AcpEvent> {
    const task = await this.createTask(message.params);

    yield { type: 'tasks/status', data: { id: task.id, state: 'working' } };

    for await (const update of this.executeTask(task)) {
      yield { type: 'tasks/artifact', data: update };
    }

    yield { type: 'tasks/status', data: { id: task.id, state: 'completed' } };
  }
}
```

---

## 9. 前端 SDK 集成

### 9.1 TypeScript SDK

```typescript
// packages/opencode-sdk/src/client.ts
export class OpencodeClient {
  private baseUrl: string;

  constructor(options: { baseUrl: string }) {
    this.baseUrl = options.baseUrl;
  }

  // Session API
  async listSessions(): Promise<Session[]> {
    const res = await fetch(`${this.baseUrl}/session/`);
    return res.json();
  }

  async createSession(body: CreateSessionBody): Promise<Session> {
    const res = await fetch(`${this.baseUrl}/session/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  // Event SSE
  subscribeEvents(): EventSource {
    return new EventSource(`${this.baseUrl}/global/event`);
  }

  // PTY WebSocket
  connectPTY(sessionId: string): WebSocket {
    return new WebSocket(`ws://${this.baseUrl}/pty/${sessionId}`);
  }
}
```

---

## 10. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| 端口被占用 | Bun.serve | 更换端口或检查占用进程 |
| WebSocket 升级失败 | fetch 处理器 | 确认 `server.upgrade(req)` 调用 |
| CORS 错误 | cors 中间件 | 检查 `allowOrigin` 配置 |
| MCP 连接失败 | 传输层配置 | 验证 stdio 命令或 SSE URL |
| SSE 不流式 | 响应头 | 确认 `Content-Type: text/event-stream` |
| PTY 无响应 | 伪终端配置 | 检查 shell 路径和权限 |

---

## 11. 架构特点总结

- **Hono + Bun 高性能**：利用 Bun 的极速运行时和原生 WebSocket
- **模块化路由**：清晰的域分离（global/session/project/mcp/pty）
- **多协议统一**：HTTP REST、WebSocket、SSE、MCP、ACP 统一架构
- **原生 PTY 支持**：基于 Bun 的伪终端实现，支持完整终端功能
- **MCP 协议原生**：内置 Model Context Protocol 支持，工具即服务
- **ACP Agent 协作**：支持 Agent-to-Agent 协议，实现多 agent 协作
- **边缘友好**：Hono 框架 Cloudflare Workers / Deno 兼容
