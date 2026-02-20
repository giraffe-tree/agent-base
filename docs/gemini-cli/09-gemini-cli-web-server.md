# Web Server（gemini-cli）

本文基于 `packages/a2a-server/` 源码，解释 gemini-cli 的 A2A（Agent-to-Agent）协议服务器如何设计和实现。该服务器提供生产级 API，支持 SSE 流式通信。

---

## 1. 先看全局（流程图）

### 1.1 A2A 服务器架构流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 启动 A2A 服务器                                          │
│  ┌─────────────────┐                                            │
│  │ gemini serve    │ ◄──── CLI 命令启动服务器                   │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  配置阶段                                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ loadConfig()                           │                     │
│  │  ├── A2A_SERVER_PORT                   │ ──► 默认 10000      │
│  │  ├── A2A_SERVER_HOST                   │ ──► 默认 0.0.0.0    │
│  │  ├── agentName / description           │ ──► Agent 元数据    │
│  │  └── capabilities                      │ ──► 支持的功能      │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  启动 Express.js 服务器                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ startServer()                          │                     │
│  │  ├── createExpressApp()                │ ──► 创建应用       │
│  │  │   ├── 中间件: cors, helmet         │ ──► 安全/跨域      │
│  │  │   ├── 中间件: bodyParser           │ ──► JSON 解析      │
│  │  │   └── 注册路由                     │ ──► API 端点       │
│  │  └── server.listen(port)               │ ──► 启动监听       │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  请求处理（A2A 协议）                                              │
│  ┌────────────────────────────────────────┐                     │
│  │ 路由分发:                              │                     │
│  │                                        │                     │
│  │  POST /              ──► handleA2AMessage()                   │
│  │       (A2A JSON-RPC over SSE)          │                     │
│  │                                        │                     │
│  │  GET /.well-known/   ──► Agent Card    │                     │
│  │       agent-card.json                   │                     │
│  │                                        │                     │
│  │  POST /tasks         ──► 创建任务      │                     │
│  │  GET  /tasks/metadata ──► 任务元数据   │                     │
│  │                                        │                     │
│  │  POST /executeCommand ──► 执行命令     │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块/函数  ──► 流程  ──► 数据流向
```

### 1.2 请求处理流程图

```text
┌────────────────────────────────────────────────────────────────────┐
│                     POST / (A2A 消息处理)                           │
└────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │  收到 A2A 请求  │
                    │ (JSON-RPC 2.0)  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ 验证消息格式    │
                    │ method/params   │
                    └────────┬────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ method = "execute"  │        │ method = "query"    │
   └──────────┬──────────┘        └──────────┬──────────┘
              │                              │
              ▼                              ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ CoderAgentExecutor  │        │ 直接返回状态        │
   │ .executeTask()      │        │                     │
   └──────────┬──────────┘        └──────────┬──────────┘
              │                              │
              ▼                              │
   ┌─────────────────────┐                   │
   │ 1. 建立 SSE 连接    │                   │
   │ 2. 调用 Gemini API  │                   │
   │ 3. 流式处理事件     │                   │
   │ 4. 发送 chunk 事件  │                   │
   └──────────┬──────────┘                   │
              │                              │
              ▼                              ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ 任务完成            │        │ 查询结果            │
   │ 发送完成事件        │        │                     │
   └─────────────────────┘        └─────────────────────┘


┌────────────────────────────────────────────────────────────────────┐
│                     SSE 流式事件类型                                │
└────────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ Gemini API 流式 │
    │ 响应事件        │
    └────────┬────────┘
             │
     ┌───────┴───────┐
     ▼               ▼
┌─────────┐    ┌─────────┐
│ text    │    │ tool    │
│ delta   │    │ call    │
└────┬────┘    └────┬────┘
     │              │
     ▼              ▼
┌─────────────────────────┐
│ A2A 协议事件封装        │
│ {                       │
│   "jsonrpc": "2.0",     │
│   "method": "",         │
│   "params": {           │
│     "state": "working", │
│     "message": {...}    │
│   }                     │
│ }                       │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│ 通过 SSE 发送给客户端   │
│ res.write(`data: {...}`)│
└─────────────────────────┘

图例: ┌─┐ 处理步骤  ──► 事件流  ──► 状态转换
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道这是一个基于 Express 的 A2A 协议服务器，使用 SSE 流式通信）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道 A2A 端点和 SSE 通信机制）。
- **10 分钟版**：通读 `3~8`（能定位 A2A 消息处理和 SSE 连接问题）。

### 2.1 一句话定义

gemini-cli 的 Web Server 是一个**基于 Express.js 的 A2A（Agent-to-Agent）协议实现**，使用**Server-Sent Events (SSE)** 实现流式响应，支持**JSON-RPC 2.0** 格式的 agent 间通信。

---

## 3. 核心组件

### 3.1 Express.js 应用架构

```typescript
// packages/a2a-server/src/http/server.ts
import express from 'express';
import { createApp } from './app';
import { CoderAgentExecutor } from '../agent/executor';

export async function startServer(config: ServerConfig) {
  const app = createApp(config);
  const executor = new CoderAgentExecutor(config);

  // 注册 A2A 消息处理器
  app.post('/', async (req, res) => {
    await handleA2AMessage(req, res, executor);
  });

  // Agent Card 端点（A2A 协议要求）
  app.get('/.well-known/agent-card.json', (req, res) => {
    res.json(generateAgentCard(config));
  });

  // 任务管理端点
  app.post('/tasks', createTaskHandler(executor));
  app.get('/tasks/metadata', getTaskMetadataHandler);

  // 命令执行端点
  app.post('/executeCommand', executeCommandHandler);

  const server = app.listen(config.port, config.host, () => {
    console.log(`A2A server listening on ${config.host}:${config.port}`);
  });

  return server;
}
```

**关键设计决策**：
- Express.js：成熟的 Node.js Web 框架，中间件生态丰富
- 模块化路由：各功能点独立处理器，便于维护
- 错误边界：统一的错误处理和日志记录

### 3.2 CoderAgentExecutor 执行器

```typescript
// packages/a2a-server/src/agent/executor.ts
export class CoderAgentExecutor {
  private geminiClient: GoogleGenAI;
  private sandboxManager: SandboxManager;

  async executeTask(
    task: Task,
    onEvent: (event: AgentEvent) => void
  ): Promise<TaskResult> {
    // 1. 构建系统提示词
    const systemPrompt = this.buildSystemPrompt(task);

    // 2. 调用 Gemini API（流式）
    const stream = await this.geminiClient.models.generateContentStream({
      model: this.config.model,
      contents: this.buildContents(task),
      config: {
        systemInstruction: systemPrompt,
        tools: this.buildTools(),
      },
    });

    // 3. 处理流式响应
    for await (const chunk of stream) {
      const event = this.parseChunk(chunk);
      onEvent(event); // 通过回调发送 SSE 事件
    }

    // 4. 返回最终结果
    return this.buildResult();
  }
}
```

---

## 4. A2A 协议端点

### 4.1 POST / - 主 A2A 消息端点

**功能**：接收 JSON-RPC 2.0 格式的 A2A 消息，返回 SSE 流式响应。

**请求格式**（JSON-RPC 2.0）：
```json
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-001",
    "message": {
      "role": "user",
      "parts": [{"text": "Fix the bug in src/index.js"}]
    }
  },
  "id": 1
}
```

**响应格式**（SSE）：
```
data: {"jsonrpc":"2.0","method":"tasks/status","params":{"id":"task-001","state":"working"}}

data: {"jsonrpc":"2.0","method":"tasks/artifact","params":{"id":"task-001","artifact":{"parts":[{"text":"I'll help you fix..."}]}}}

data: {"jsonrpc":"2.0","method":"tasks/status","params":{"id":"task-001","state":"completed"}}
```

**实现代码**：
```typescript
async function handleA2AMessage(
  req: Request,
  res: Response,
  executor: CoderAgentExecutor
) {
  // 设置 SSE 头
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  const { method, params } = req.body;

  if (method === 'tasks/send') {
    await executor.executeTask(params, (event) => {
      // 发送 SSE 事件
      res.write(`data: ${JSON.stringify({
        jsonrpc: '2.0',
        method: event.type,
        params: event.data
      })}\n\n`);
    });
  }

  res.end();
}
```

### 4.2 GET /.well-known/agent-card.json - Agent 元数据

**功能**：返回 Agent Card（A2A 协议标准），描述 agent 的能力和端点。

**响应格式**：
```json
{
  "name": "gemini-coder",
  "description": "A coding assistant powered by Gemini",
  "url": "http://localhost:10000",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "code-edit",
      "name": "Code Editing",
      "description": "Edit code files"
    }
  ]
}
```

### 4.3 任务管理端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/tasks` | POST | 创建新任务 |
| `/tasks/metadata` | GET | 获取任务元数据列表 |

---

## 5. SSE 通信详解

### 5.1 SSE 连接建立

```typescript
// 设置 SSE 响应头
function setupSSEResponse(res: Response) {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // 禁用 Nginx 缓冲
  res.flushHeaders();
}
```

### 5.2 事件类型映射

| Gemini API 事件 | A2A 事件 | 说明 |
|-----------------|----------|------|
| `text` delta | `tasks/artifact` | 文本输出 |
| `function_call` | `tasks/artifact` | 工具调用 |
| `function_response` | `tasks/artifact` | 工具结果 |
| 完成 | `tasks/status` (completed) | 任务完成 |
| 错误 | `tasks/status` (failed) | 任务失败 |

### 5.3 心跳机制

```typescript
// 定期发送心跳保持连接
const heartbeat = setInterval(() => {
  res.write(': heartbeat\n\n'); // SSE 注释，不会触发客户端事件
}, 30000);

// 连接关闭时清理
req.on('close', () => {
  clearInterval(heartbeat);
});
```

---

## 6. 与 Gemini API 集成

### 6.1 流式响应处理链

```text
Gemini API ──► GoogleGenAI.generateContentStream() ──► chunk parser
    │                                                    │
    ▼                                                    ▼
text/function_call                           A2A 事件格式化
    │                                                    │
    └────────────────┬───────────────────────────────────┘
                     ▼
              SSE write to client
```

### 6.2 工具调用处理

```typescript
async function handleToolCall(
  toolCall: FunctionCall,
  executor: CoderAgentExecutor
): Promise<ToolResult> {
  const { name, args } = toolCall;

  switch (name) {
    case 'editFile':
      return await executor.editFile(args);
    case 'readFile':
      return await executor.readFile(args);
    case 'executeCommand':
      return await executor.executeCommand(args);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}
```

---

## 7. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| SSE 连接断开 | Nginx 缓冲 | 添加 `X-Accel-Buffering: no` 头 |
| CORS 错误 | 中间件配置 | 检查 `cors()` 中间件 |
| Agent Card 404 | URL 路径 | 确认 `/.well-known/agent-card.json` |
| 流式不生效 | 响应头 | 确认 `Content-Type: text/event-stream` |
| 内存泄漏 | 连接清理 | 检查 `req.on('close')` 处理 |

---

## 8. 架构特点总结

- **A2A 标准兼容**：遵循 Google 的 Agent-to-Agent 协议规范
- **SSE 流式通信**：比 WebSocket 更简单，自动重连友好
- **JSON-RPC 2.0**：标准化的 RPC 格式，易于多语言集成
- **Agent Card 发现**：支持标准的服务发现机制
- **生产级 Express**：成熟的错误处理、中间件、日志
- **无状态设计**：每个请求独立，便于水平扩展
