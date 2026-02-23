# Web Server（kimi-cli）

> 面向新手：Web 不是把 Soul 直接塞进 FastAPI，而是“API 网关 + session runner + worker 子进程”的分层执行模型。

本文基于：
- `kimi-cli/src/kimi_cli/cli/web.py`
- `kimi-cli/src/kimi_cli/web/app.py`
- `kimi-cli/src/kimi_cli/web/api/sessions.py`
- `kimi-cli/src/kimi_cli/web/runner/process.py`
- `kimi-cli/src/kimi_cli/web/runner/worker.py`

---

## 1. 全局架构图

```text
kimi web (CLI)
  -> run_web_server(host, port, auth, lan_only...)
  -> create_app() (FastAPI + middleware + routers)
  -> app lifespan 启动 KimiCLIRunner
  -> per session: SessionProcess 管理 worker 子进程
  -> worker 里跑 KimiCLI.run_wire_stdio()
```

代码锚点：
- web 参数入口：`kimi-cli/src/kimi_cli/web/app.py:286`
- 默认端口 `5494`：`kimi-cli/src/kimi_cli/web/app.py:52`
- `create_app()`：`kimi-cli/src/kimi_cli/web/app.py:137`
- lifespan 启动 runner：`kimi-cli/src/kimi_cli/web/app.py:168`
- worker 执行 `run_wire_stdio()`：`kimi-cli/src/kimi_cli/web/runner/worker.py:61`

---

## 2. 会话流（WebSocket）完整流程

```text
WS /api/sessions/{id}/stream
  -> token/origin/lan 校验
  -> websocket.accept()
  -> 检查 session + 是否有 wire 历史
  -> attach websocket(replay mode)
  -> replay wire.jsonl
  -> history_complete
  -> SessionProcess.start() 启动 worker
  -> 双向转发实时消息
  -> disconnect 时 remove_websocket
```

代码锚点：
- WebSocket 入口：`kimi-cli/src/kimi_cli/web/api/sessions.py:1044`
- 鉴权与来源检查：`kimi-cli/src/kimi_cli/web/api/sessions.py:1061`
- `accept()`：`kimi-cli/src/kimi_cli/web/api/sessions.py:1085`
- 历史回放与结束标记：`kimi-cli/src/kimi_cli/web/api/sessions.py:1108`
- 启动 worker 与状态快照：`kimi-cli/src/kimi_cli/web/api/sessions.py:1133`
- 断连清理：`kimi-cli/src/kimi_cli/web/api/sessions.py:1203`

---

## 3. 进程模型图（为什么要 worker）

```text
FastAPI 进程
  -> SessionProcess.start()
     -> create_subprocess_exec(python -m ...worker)
     -> 维护 in-flight prompt / ws fanout / status

Worker 进程
  -> load session + create KimiCLI
  -> run_wire_stdio()
```

设计意图：
- 把 HTTP 生命周期和 agent 执行生命周期隔离，降低单进程崩溃半径。

工程 trade-off：
- 优点：多会话并发管理清晰，可重启 worker。
- 代价：需要处理进程间消息转发、重放缓冲、状态同步。

代码锚点：
- `SessionProcess` 职责说明：`kimi-cli/src/kimi_cli/web/runner/process.py:54`
- 子进程启动命令：`kimi-cli/src/kimi_cli/web/runner/process.py:195`
- worker 读取会话并创建 KimiCLI：`kimi-cli/src/kimi_cli/web/runner/worker.py:29`

---

## 4. 安全控制流程（Web）

```text
run_web_server()
  -> public_mode? 决定 token 生成策略
  -> 设置 ENV_SESSION_TOKEN / ALLOWED_ORIGINS / LAN_ONLY
  -> create_app() 注入 AuthMiddleware + CORS
  -> session_stream 再做 token/origin/lan 二次检查
```

代码锚点：
- public 模式与 token 策略：`kimi-cli/src/kimi_cli/web/app.py:340`
- token 写入环境变量：`kimi-cli/src/kimi_cli/web/app.py:373`
- 中间件注入：`kimi-cli/src/kimi_cli/web/app.py:201`
- WS 二次校验：`kimi-cli/src/kimi_cli/web/api/sessions.py:1066`

---

## 5. 关键结论

- Web 层是“网关 + 调度器”，真正执行在 worker。
- 历史回放与实时流在同一 WebSocket 通道串接，保证前端可重建完整会话。
- 安全并非只靠 middleware，WS 入口还做了显式校验。
