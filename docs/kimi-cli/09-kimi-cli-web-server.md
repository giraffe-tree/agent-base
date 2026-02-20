# Web Server（kimi-cli）

本文基于 `src/kimi_cli/web/` 源码，解释 kimi-cli 的全功能 Web 服务器如何设计和实现。该服务器基于 FastAPI，提供 REST API 和 WebSocket 实时通信，支持 Session 管理和子进程执行。

---

## 1. 先看全局（流程图）

### 1.1 FastAPI 服务器架构流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 启动 Web 服务器                                          │
│  ┌─────────────────┐                                            │
│  │ kimi web        │ ◄──── CLI 命令启动服务器                   │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  配置阶段                                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ WebConfig                              │                     │
│  │  ├── host: str = "127.0.0.1"          │ ──► 绑定地址       │
│  │  ├── port: int = 8080                 │ ──► 服务端口       │
│  │  ├── web_root: Path                   │ ──► 前端静态文件   │
│  │  └── log_level: str = "info"          │ ──► 日志级别       │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  启动 FastAPI + Uvicorn                                          │
│  ┌────────────────────────────────────────┐                     │
│  │ create_app()                           │                     │
│  │  ├── FastAPI 实例                      │                     │
│  │  │   ├── title="Kimi CLI Web"         │                     │
│  │  │   └── version=VERSION              │                     │
│  │  ├── 注册中间件                        │                     │
│  │  │   ├── CORS                         │                     │
│  │  │   ├── 错误处理                     │                     │
│  │  │   └── 请求日志                     │                     │
│  │  └── 注册路由                          │                     │
│  │       ├── /api/sessions/*              │                     │
│  │       ├── /api/config/*                │                     │
│  │       └── / (静态文件)                  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Uvicorn 运行                                                    │
│  ┌────────────────────────────────────────┐                     │
│  │ uvicorn.run(app, host, port)           │                     │
│  │  └── HTTP + WebSocket 服务器           │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块/配置  ──► 流程  ──► 数据流向
```

### 1.2 WebSocket 通信流程图

```text
┌────────────────────────────────────────────────────────────────────┐
│                     Session WebSocket 连接                          │
└────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │ 前端连接        │
                    │ /api/sessions/  │
                    │ {id}/stream     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ 验证 session    │
                    │ 存在性检查      │
                    └────────┬────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ session 不存在      │        │ session 存在        │
   └──────────┬──────────┘        └──────────┬──────────┘
              │                              │
              ▼                              ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ 返回 404            │        │ 建立 WebSocket      │
   │                     │        │ 连接                │
   └─────────────────────┘        └──────────┬──────────┘
                                             │
                                             ▼
                    ┌─────────────────────────────────────┐
                    │ 检查 KimiCLI 子进程状态             │
                    │                                     │
                    │ ┌─────────────┐   ┌─────────────┐  │
                    │ │ 未启动      │   │ 运行中      │  │
                    │ └──────┬──────┘   └──────┬──────┘  │
                    │        │                 │         │
                    │        ▼                 ▼         │
                    │ ┌─────────────┐   ┌─────────────┐  │
                    │ │启动子进程   │   │直接连接     │  │
                    │ │SessionProcess│  │已有 stream  │  │
                    │ └──────┬──────┘   └──────┬──────┘  │
                    └────────┼─────────────────┼─────────┘
                             │                 │
                             └────────┬────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 开始 JSON-RPC 2.0 通信              │
                    │                                     │
                    │  Client ◄────► Server              │
                    │  (browser)      (SessionProcess)   │
                    │                                     │
                    │  消息类型:                          │
                    │  - input (用户输入)                 │
                    │  - output (AI 输出)                 │
                    │  - tool_call (工具调用)             │
                    │  - tool_result (工具结果)           │
                    │  - error (错误)                     │
                    └─────────────────────────────────────┘


┌────────────────────────────────────────────────────────────────────┐
│                     子进程模型 (SessionProcess)                     │
└────────────────────────────────────────────────────────────────────┘

┌─────────────────┐         ┌─────────────────────────────────────────┐
│   Web Server    │         │         SessionProcess                  │
│   (FastAPI)     │         │  ┌─────────────────────────────────┐    │
│                 │         │  │ KimiCLIRunner                   │    │
│ ┌─────────────┐ │         │  │  ┌───────────────────────────┐  │    │
│ │ WebSocket   │ │◄───────►│  │  │ subprocess.Popen(         │  │    │
│ │ 管理器      │ │  stdin  │  │  │   ["kimi", "--jsonrpc"],  │  │    │
│ └─────────────┘ │ stdout  │  │  │   stdin=PIPE,             │  │    │
│                 │ stderr  │  │  │   stdout=PIPE,            │  │    │
│                 │         │  │  │   stderr=PIPE             │  │    │
│                 │         │  │  │ )                         │  │    │
│                 │         │  │  └───────────────────────────┘  │    │
│                 │         │  │                                 │    │
│                 │         │  │  ┌───────────────────────────┐  │    │
│                 │         │  │  │ 线程: stdin_writer        │  │    │
│                 │         │  │  │      (接收 WebSocket      │  │    │
│                 │         │  │  │       写入子进程 stdin)   │  │    │
│                 │         │  │  └───────────────────────────┘  │    │
│                 │         │  │  ┌───────────────────────────┐  │    │
│                 │         │  │  │ 线程: stdout_reader       │  │    │
│                 │         │  │  │      (读取子进程 stdout   │  │    │
│                 │         │  │  │       转发 WebSocket)     │  │    │
│                 │         │  │  └───────────────────────────┘  │    │
│                 │         │  └─────────────────────────────────┘    │
└─────────────────┘         └─────────────────────────────────────────┘

图例: ┌─┐ 组件  ──► 数据流  ◄──► 双向通信
```

### 1.3 历史重放模式流程图

```text
┌────────────────────────────────────────────────────────────────────┐
│                     新客户端连接（历史重放）                          │
└────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │ WebSocket       │
                    │ 连接建立        │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ 查询 session    │
                    │ 历史消息        │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ replay_history  │
                    │ = true ?        │
                    └────────┬────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ Yes                 │        │ No                  │
   │                     │        │                     │
   │ 发送历史消息        │        │ 直接开始            │
   │ (JSON-RPC 格式)     │        │ 实时通信            │
   │                     │        │                     │
   │ 每条消息:           │        │                     │
   │ {                   │        │                     │
   │   method: "output", │        │                     │
   │   params: {...},    │        │                     │
   │   replay: true      │        │                     │
   │ }                   │        │                     │
   └──────────┬──────────┘        └─────────────────────┘
              │
              ▼
   ┌─────────────────────┐
   │ 发送 "replay_done"  │
   │ 标记历史结束        │
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │ 切换到实时模式      │
   │ 转发子进程输出      │
   └─────────────────────┘

图例: replay: true 标记表示历史重放消息
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道这是一个基于 FastAPI 的全功能 Web 服务器，支持 WebSocket 实时通信）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道 Session 管理和 WebSocket 通信流程）。
- **10 分钟版**：通读 `3~8`（能定位子进程管理和 WebSocket 连接问题）。

### 2.1 一句话定义

kimi-cli 的 Web Server 是一个**基于 FastAPI + Uvicorn 的全功能 Web 服务器**，通过**WebSocket + JSON-RPC 2.0** 实现与浏览器前端的实时通信，使用**子进程模型**管理 KimiCLI 实例的生命周期。

---

## 3. 核心组件

### 3.1 FastAPI + Uvicorn 架构

```python
# src/kimi_cli/web/app.py
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动: 初始化全局状态
    app.state.sessions = {}  # session_id -> SessionProcess
    app.state.config = load_config()
    yield
    # 关闭: 清理所有子进程
    for session in app.state.sessions.values():
        await session.terminate()
    app.state.sessions.clear()

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kimi CLI Web",
        version=VERSION,
        lifespan=lifespan
    )

    # 中间件
    app.add_middleware(CORSMiddleware, ...)
    app.add_middleware(ErrorHandlerMiddleware)

    # API 路由
    app.include_router(sessions_router, prefix="/api/sessions")
    app.include_router(config_router, prefix="/api/config")

    # 静态文件（前端）
    app.mount("/", StaticFiles(directory="web/dist", html=True))

    return app

# 启动入口
def start_server(host: str, port: int):
    app = create_app()
    uvicorn.run(app, host=host, port=port)
```

### 3.2 SessionProcess 子进程管理

```python
# src/kimi_cli/web/runner/process.py
class SessionProcess:
    """管理一个 KimiCLI 子进程的生命周期"""

    def __init__(self, session_id: str, config: Config):
        self.session_id = session_id
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self.websockets: Set[WebSocket] = set()
        self.history: List[Dict] = []
        self._lock = asyncio.Lock()

    async def start(self):
        """启动 KimiCLI 子进程"""
        self.process = await asyncio.create_subprocess_exec(
            "kimi", "--jsonrpc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 启动读写线程
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def _read_stdout(self):
        """读取子进程 stdout 并广播到所有 WebSocket"""
        while self.process and self.process.stdout:
            line = await self.process.stdout.readline()
            if not line:
                break
            message = json.loads(line.decode())
            self.history.append(message)
            await self._broadcast(message)

    async def send_input(self, message: Dict):
        """发送消息到子进程 stdin"""
        if self.process and self.process.stdin:
            data = json.dumps(message).encode() + b"\n"
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def attach_websocket(self, ws: WebSocket, replay_history: bool = True):
        """附加 WebSocket 连接"""
        self.websockets.add(ws)
        if replay_history:
            for msg in self.history:
                await ws.send_json({**msg, "replay": True})
            await ws.send_json({"method": "replay_done"})
```

---

## 4. REST API 详解

### 4.1 Session CRUD

| 端点 | 方法 | 功能 | 响应 |
|------|------|------|------|
| `/api/sessions/` | GET | 列出所有 sessions | `[{id, status, created_at}]` |
| `/api/sessions/` | POST | 创建新 session | `{id, status}` |
| `/api/sessions/{id}` | GET | 获取 session 详情 | `{id, status, history}` |
| `/api/sessions/{id}` | DELETE | 删除 session | 204 |
| `/api/sessions/{id}/fork` | POST | 分叉 session | `{new_id, parent_id}` |

**创建 Session**：
```python
@sessions_router.post("/")
async def create_session(app: FastAPI) -> SessionInfo:
    session_id = generate_id()
    process = SessionProcess(session_id, app.state.config)
    await process.start()
    app.state.sessions[session_id] = process
    return SessionInfo(id=session_id, status="running")
```

**Session 分叉**：
```python
@sessions_router.post("/{id}/fork")
async def fork_session(id: str, app: FastAPI) -> SessionInfo:
    parent = app.state.sessions.get(id)
    if not parent:
        raise HTTPException(404, "Session not found")

    new_id = generate_id()
    new_process = SessionProcess(new_id, app.state.config)
    await new_process.start()

    # 复制父 session 的历史和状态
    await new_process.load_state(parent.export_state())

    app.state.sessions[new_id] = new_process
    return SessionInfo(id=new_id, parent_id=id, status="running")
```

### 4.2 Config API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/config/` | GET | 获取当前配置 |
| `/api/config/` | PATCH | 更新配置（部分） |

---

## 5. WebSocket 实时通信

### 5.1 WebSocket 端点

```python
# src/kimi_cli/web/api/sessions.py
@sessions_router.websocket("/{id}/stream")
async def session_websocket(ws: WebSocket, id: str, app: FastAPI):
    await ws.accept()

    session = app.state.sessions.get(id)
    if not session:
        await ws.close(code=1008, reason="Session not found")
        return

    # 附加到 session，可选择重放历史
    await session.attach_websocket(ws, replay_history=True)

    try:
        while True:
            # 接收前端消息
            message = await ws.receive_json()

            # 转发到子进程
            await session.send_input(message)
    except WebSocketDisconnect:
        await session.detach_websocket(ws)
```

### 5.2 JSON-RPC 2.0 协议

kimi-cli 使用 JSON-RPC 2.0 作为 WebSocket 通信协议：

**请求格式**：
```json
{
  "jsonrpc": "2.0",
  "method": "input",
  "params": {
    "text": "Hello, can you help me?"
  },
  "id": 1
}
```

**响应格式**：
```json
{
  "jsonrpc": "2.0",
  "method": "output",
  "params": {
    "text": "I'd be happy to help!",
    "finished": false
  },
  "id": 1
}
```

**通知格式**（服务端主动推送）：
```json
{
  "jsonrpc": "2.0",
  "method": "tool_call",
  "params": {
    "name": "read_file",
    "args": {"path": "/tmp/test.txt"}
  }
}
```

### 5.3 消息类型对照表

| method | 方向 | 说明 |
|--------|------|------|
| `input` | Client → Server | 用户输入 |
| `output` | Server → Client | AI 输出（流式） |
| `tool_call` | Server → Client | 工具调用通知 |
| `tool_result` | Client → Server | 工具执行结果 |
| `error` | 双向 | 错误通知 |
| `interrupt` | Client → Server | 中断当前生成 |
| `replay_done` | Server → Client | 历史重放完成标记 |

---

## 6. 前端 SDK

### 6.1 useSessionStream Hook

```typescript
// web/src/hooks/useSessionStream.ts
export function useSessionStream(sessionId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8080/api/sessions/${sessionId}/stream`);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);

      // 处理历史重放
      if (msg.replay) {
        setMessages(prev => [...prev, msg]);
        return;
      }

      // 处理历史重放完成
      if (msg.method === 'replay_done') {
        console.log('History replay complete');
        return;
      }

      // 处理实时消息
      if (msg.method === 'output') {
        setMessages(prev => appendOrUpdate(prev, msg));
      }
    };

    ws.onclose = () => setIsConnected(false);

    return () => ws.close();
  }, [sessionId]);

  const sendInput = (text: string) => {
    wsRef.current?.send(JSON.stringify({
      jsonrpc: '2.0',
      method: 'input',
      params: { text },
      id: Date.now()
    }));
  };

  return { messages, isConnected, sendInput };
}
```

---

## 7. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| WebSocket 连接失败 | session 存在性 | 确认 session id 正确 |
| 历史未重放 | replay_history 参数 | 检查 attach_websocket 调用 |
| 子进程无响应 | stdin/stdout 管道 | 检查进程是否阻塞 |
| 内存增长 | history 累积 | 实现 history 裁剪策略 |
| 端口占用 | Uvicorn 启动 | 更换端口或关闭占用进程 |

---

## 8. 架构特点总结

- **FastAPI 现代化**：完整的类型提示、自动文档、异步支持
- **WebSocket 全双工**：比 SSE 更灵活，支持双向实时通信
- **JSON-RPC 2.0 标准**：结构化通信协议，易于扩展
- **子进程隔离**：每个 session 独立进程，故障隔离
- **历史重放**：新连接可同步完整会话历史
- **Session 分叉**：支持从任意点创建分支会话
