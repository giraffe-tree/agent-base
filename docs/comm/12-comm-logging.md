# 日志记录机制

> **文档类型说明**：本文档为**跨项目对比分析**，对比 5 个主流 AI Coding Agent（Codex、Gemini CLI、Kimi CLI、OpenCode、SWE-agent）的日志实现，采用横向对比结构而非单一项目深度分析。

---

## TL;DR（结论先行）

**一句话定义**：日志记录机制是 Agent CLI 用于记录运行时信息、调试问题、追踪执行流程的重要机制，对可观测性和问题排查至关重要。

5 个项目的核心取舍：
- **Codex**：**企业级 tracing 方案**（tracing + SQLite + OpenTelemetry）（对比 Gemini CLI 的双模式、Kimi CLI 的库友好方案）
- **Gemini CLI**：**双模式生产方案**（winston + DebugLogger）（对比 Codex 的 span 追踪、OpenCode 的零依赖）
- **Kimi CLI**：**库友好方案**（loguru + StderrRedirector）（对比 SWE-agent 的标准库方案）
- **OpenCode**：**Bun-native 零依赖方案**（自定义实现）（对比 Gemini CLI 的 winston 依赖）
- **SWE-agent**：**标准库极简方案**（logging + rich）（对比 Kimi CLI 的 loguru）

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

想象一下这个场景：凌晨 2 点，你负责的 AI Agent 在生产环境崩溃了。用户反馈说"它突然就不动了"。你登录服务器，面对几千行 console 输出，却不知道从何看起...

这不是你的问题。这是**日志系统**的问题。

当你从简单的脚本转向复杂的 AI Agent 系统时，日志不再是"打印一句话"那么简单：

**没有良好日志系统**：
```
用户问"修复这个 bug" → Agent 执行 → 崩溃 → 你面对混乱的 console 输出无从下手
```

**有良好日志系统**：
```
用户问"修复这个 bug" → Agent 执行
  → 日志: [INFO] 开始分析代码结构
  → 日志: [DEBUG] 调用 read_file 工具
  → 日志: [TRACE] LLM 响应耗时 2.3s
  → 日志: [ERROR] 工具执行失败: exit code 1
  → 崩溃 → 你通过 trace_id 快速定位问题
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| **并发场景** | 10 个 tool 调用同时进行，日志混成一团乱麻 |
| **分布式追踪** | 用户请求经过 5 个服务，无法串联起来 |
| **性能开销** | 高频日志拖慢 Agent 响应 |
| **持久化查询** | 3 个月前的某次执行记录，无法找到 |
| **多目标输出** | 需要同时输出到文件、控制台、远程服务 |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────────┐
│ Agent Loop / Session Runtime                                     │
│ 各项目核心循环逻辑                                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 调用/事件
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ ▓▓▓ 日志记录机制 ▓▓▓                                              │
│                                                                  │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│ │   Codex      │ │ Gemini CLI   │ │  Kimi CLI    │               │
│ │  tracing     │ │  winston     │ │  loguru      │               │
│ │  + SQLite    │ │  + DebugLogger│ │  + Redirector│               │
│ └──────────────┘ └──────────────┘ └──────────────┘               │
│ ┌──────────────┐ ┌──────────────┐                                │
│ │  OpenCode    │ │ SWE-agent    │                                │
│ │  自定义实现   │ │  logging     │                                │
│ │  Bun-native  │ │  + rich      │                                │
│ └──────────────┘ └──────────────┘                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 写入
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   文件日志    │   │   SQLite      │   │ OpenTelemetry │
│   控制台      │   │   结构化存储   │   │   链路追踪    │
└───────────────┘   └───────────────┘   └───────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `tracing_subscriber` | Rust 日志注册中心，接收所有日志事件 | `codex/codex-rs/tui/src/lib.rs:354-421` |
| `LogDbLayer` | SQLite 存储层，批处理插入日志 | `codex/codex-rs/state/src/log_db.rs:47-62` |
| `winston` | Node.js 生产级日志库 | `gemini-cli/packages/a2a-server/src/utils/logger.ts:9-28` |
| `DebugLogger` | 开发调试日志，支持文件输出 | `gemini-cli/packages/core/src/utils/debugLogger.ts:23-69` |
| `loguru` | Python 简洁日志库，库友好设计 | `kimi-cli/src/kimi_cli/__init__.py:1-6` |
| `StderrRedirector` | 子进程 stderr 捕获 | `kimi-cli/src/kimi_cli/utils/logging.py:15-125` |
| `Log.create()` | Bun-native 自定义日志实现 | `opencode/packages/opencode/src/util/log.ts:100-181` |
| `get_logger()` | 带 Emoji 的 RichHandler | `sweagent/sweagent/utils/log.py:57-91` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent Loop
    participant L as Logger
    participant H as Handler/Sink
    participant S as Storage

    A->>L: 1. 记录日志 (info/debug/trace)
    Note over L: 日志级别过滤
    L->>H: 2. 传递给处理器
    Note over H: 格式化/结构化
    H->>S: 3. 写入存储
    Note over S: 文件/SQLite/OTel
    S-->>H: 4. 确认写入
    H-->>L: 5. 完成
    L-->>A: 6. 返回（异步）
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | Agent 调用日志接口 | 解耦业务逻辑与日志实现 |
| 2 | Logger 级别过滤 | 避免不必要的处理开销 |
| 3 | Handler 格式化 | 统一格式，支持多种后端 |
| 4 | 异步写入存储 | 不阻塞主流程 |

---

## 3. 各 Agent 实现对比

### 3.1 Codex (Rust) —— 企业级追踪方案

**类比**：Linux `ftrace` + `systemd-journald` 的组合

Codex 的日志系统像 Linux 内核的追踪基础设施：
- `tracing` = `ftrace`（零开销的静态探针）
- `LogDbLayer` = `journald`（结构化存储，可查询）
- `OpenTelemetry` = `eBPF`（跨系统的可观测性）

**架构图**

```text
┌─────────────────────────────────────────────────────────┐
│  Tracing Registry (日志注册中心)                          │
│  └── 接收所有 tracing::info!/debug! 等事件              │
└─────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  File Layer   │  │  LogDbLayer   │  │  OpenTelemetry│
│  文件日志     │  │  SQLite 存储  │  │  链路追踪     │
│               │  │               │  │               │
│ • 非阻塞 I/O  │  │ • 批处理插入  │  │ • tracing     │
│ • span 追踪   │  │ • 90天保留    │  │ • metrics     │
│ • RUST_LOG    │  │ • thread_id   │  │ • logs        │
└───────────────┘  └───────────────┘  └───────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| 初始化 | `codex/codex-rs/tui/src/lib.rs` | 326-421 | 多层 subscriber 初始化 |
| SQLite | `codex/codex-rs/state/src/log_db.rs` | 42-62 | LogDbLayer 实现 |

**代码示例**

```rust
// codex/codex-rs/tui/src/lib.rs:354-421
let file_layer = tracing_subscriber::fmt::layer()
    .with_writer(non_blocking)
    .with_target(true)
    .with_ansi(false)
    .with_span_events(FmtSpan::NEW | FmtSpan::CLOSE)
    .with_filter(env_filter());

let log_db_layer = log_db::start(state_db)
    .with_filter(env_filter());

let _ = tracing_subscriber::registry()
    .with(file_layer)
    .with(log_db_layer)
    .with(otel_logger_layer)
    .with(otel_tracing_layer)
    .try_init();
```

**SQLite 存储结构**

```rust
// codex/codex-rs/state/src/log_db.rs:42-46
const LOG_QUEUE_CAPACITY: usize = 512;  // 队列容量
const LOG_BATCH_SIZE: usize = 64;       // 批处理大小
const LOG_FLUSH_INTERVAL: Duration = Duration::from_millis(250);  // 刷新间隔
const LOG_RETENTION_DAYS: i64 = 90;     // 保留天数
```

**特点**

- ✅ 多层 subscriber 架构（文件 + SQLite + OTel）
- ✅ 非阻塞 I/O，不影响主流程性能
- ✅ span 追踪，支持分布式链路追踪
- ✅ 90天自动清理策略
- ✅ RUST_LOG 环境变量配置

---

### 3.2 Gemini CLI (TypeScript) —— 双模式生产方案

**类比**：服务端 `rsyslog` + 开发时 `strace`

Gemini CLI 的日志设计体现了**环境区分**的思想：
- A2A Server 用 `winston` = 生产环境的 `rsyslog`（稳定、结构化）
- Core 用 `DebugLogger` = 开发时的 `strace`（详细、实时）

**架构图**

```text
┌─────────────────────────────────────────────────────────┐
│  A2A Server (服务端)                                      │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Winston Logger                                      ││
│  │ ├── timestamp (YYYY-MM-DD HH:mm:ss.SSS A)          ││
│  │ ├── level (INFO/WARN/ERROR)                        ││
│  │ └── Console transport                            ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
┌─────────────────────────────────────────────────────────┐
│  Core (客户端)                                            │
│  ┌─────────────────────────────────────────────────────┐│
│  │ DebugLogger (自定义)                                ││
│  │ ├── console.log 输出到 UI                           ││
│  │ ├── 可选文件输出 (GEMINI_DEBUG_LOG_FILE)            ││
│  │ └── ISO timestamp                                   ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Winston | `gemini-cli/packages/a2a-server/src/utils/logger.ts` | 9-28 | A2A Server 日志 |
| DebugLogger | `gemini-cli/packages/core/src/utils/debugLogger.ts` | 23-69 | 调试日志实现 |

**代码示例**

```typescript
// gemini-cli/packages/a2a-server/src/utils/logger.ts:9-28
const logger = winston.createLogger({
  level: 'info',
  format: winston.format.combine(
    winston.format.timestamp({
      format: 'YYYY-MM-DD HH:mm:ss.SSS A',
    }),
    winston.format.printf((info) => {
      const { level, timestamp, message, ...rest } = info;
      return `[${level.toUpperCase()}] ${timestamp} -- ${message}` +
        `${Object.keys(rest).length > 0 ? `\n${JSON.stringify(rest, null, 2)}` : ''}`;
    }),
  ),
  transports: [new winston.transports.Console()],
});
```

**特点**

- ✅ 双模式设计（生产级 winston + 轻量 DebugLogger）
- ✅ ESLint 禁止直接使用 `console.*`，强制使用 DebugLogger
- ✅ 调试抽屉 UI 展示日志
- ✅ 可选文件输出，便于问题排查

---

### 3.3 Kimi CLI (Python) —— 库友好方案

**类比**：Python 的 `requests` vs `urllib`

Kimi CLI 选择 `loguru` 而不是标准库 `logging`，就像你选择 `requests` 而不是 `urllib`：
- **同样的底层能力**：最终都基于 Python 的日志基础设施
- **更好的 API 设计**：更直观、更少的样板代码
- **开箱即用的功能**：结构化、彩色输出、自动异常捕获

**架构图**

```text
┌─────────────────────────────────────────────────────────┐
│  Loguru Logger (库友好设计)                               │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 默认禁用 (logger.disable("kimi_cli"))                ││
│  │ 入口点启用 (logger.enable("kimi_cli"))               ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        ▼                                      ▼
┌───────────────┐                  ┌───────────────────┐
│  常规日志     │                  │  StderrRedirector │
│  • {var} 插值 │                  │  子进程输出捕获   │
│  • 结构化     │                  │                   │
│  • 彩色输出   │                  │ • os.pipe()       │
│               │                  │ • 线程读取        │
│               │                  │ • 重定向到 logger │
└───────────────┘                  └───────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| 初始化 | `kimi-cli/src/kimi_cli/__init__.py` | 1-6 | 日志禁用/启用 |
| StderrRedirector | `kimi-cli/src/kimi_cli/utils/logging.py` | 15-125 | stderr 重定向 |

**代码示例**

```python
# kimi-cli/src/kimi_cli/__init__.py:1-6
from loguru import logger

# 默认禁用，避免作为库使用时污染日志
logger.disable("kimi_cli")
# 应用入口点启用: logger.enable("kimi_cli")

# kimi-cli/src/kimi_cli/utils/logging.py:25-45
class StderrRedirector:
    def install(self) -> None:
        self._original_fd = os.dup(2)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 2)
        os.close(write_fd)
        self._thread = threading.Thread(
            target=self._drain, name="kimi-stderr-redirect", daemon=True
        )
        self._thread.start()
```

**特点**

- ✅ `loguru` 库友好（默认禁用，应用启用）
- ✅ `{var}` 插值语法
- ✅ `StderrRedirector` 捕获子进程输出
- ✅ 结构化日志支持

---

### 3.4 OpenCode (TypeScript) —— Bun-native 零依赖方案

**类比**：用 `BTF` 替代传统调试符号

OpenCode 选择自定义日志实现，就像 Linux 内核用 BTF（BPF Type Format）替代传统调试符号：
- **原生支持**：与运行时（Bun）深度集成
- **零外部依赖**：不依赖第三方库的版本兼容性
- **类型内嵌**：Zod 类型定义就像 BTF 信息，自描述、可验证

**架构图**

```text
┌─────────────────────────────────────────────────────────┐
│  Log Namespace (自定义实现)                               │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Zod 类型安全                                         ││
│  │ Level = "DEBUG" | "INFO" | "WARN" | "ERROR"         ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  文件输出     │  │  标签系统     │  │  Timing 工具  │
│               │  │               │  │               │
│ • Bun.file    │  │ • service 标签│  │ • time()      │
│ • 自动轮转    │  │ • tag() 链式  │  │ • stop()      │
│ • 保留10个    │  │ • clone()     │  │ • dispose     │
└───────────────┘  └───────────────┘  └───────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| 日志实现 | `opencode/packages/opencode/src/util/log.ts` | 1-183 | 完整日志系统 |

**代码示例**

```typescript
// opencode/packages/opencode/src/util/log.ts:111-128
function build(message: any, extra?: Record<string, any>) {
  const prefix = Object.entries({ ...tags, ...extra })
    .filter(([_, value]) => value !== undefined && value !== null)
    .map(([key, value]) => {
      const prefix = `${key}=`
      if (value instanceof Error) return prefix + formatError(value)
      if (typeof value === "object") return prefix + JSON.stringify(value)
      return prefix + value
    })
    .join(" ")
  const next = new Date()
  const diff = next.getTime() - last
  last = next.getTime()
  return [next.toISOString().split(".")[0], "+" + diff + "ms", prefix, message]
    .filter(Boolean).join(" ") + "\n"
}
```

**日志轮转策略**

```typescript
// opencode/packages/opencode/src/util/log.ts:80-90
async function cleanup(dir: string) {
  const files = await Glob.scan("????-??-??T??????.log", {
    cwd: dir,
    absolute: true,
    include: "file",
  })
  if (files.length <= 5) return
  const filesToDelete = files.slice(0, -10)
  await Promise.all(filesToDelete.map((file) => fs.unlink(file).catch(() => {})))
}
```

**特点**

- ✅ Bun-native 实现，无第三方依赖
- ✅ Key=value 结构化格式便于解析
- ✅ Zod 类型安全
- ✅ 内置 timing 工具
- ✅ 服务标签系统

---

### 3.5 SWE-agent (Python) —— 标准库极简方案

**类比**：用 `stdio` + `grep` 的组合

SWE-agent 的日志哲学像 Unix 的"小而美"工具链：
- `logging` = `stdio`（简单、通用、无处不在）
- `rich` = `colorgrep`（增强可读性，但不改变本质）
- `TRACE` 级别 = `grep -v` 的反向操作（比 DEBUG 更细粒度）

**架构图**

```text
┌─────────────────────────────────────────────────────────┐
│  logging (stdlib)                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 自定义 TRACE 级别 (level=5)                         ││
│  │ logging.addLevelName(logging.TRACE, "TRACE")        ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  RichHandler  │  │  FileHandler  │  │  线程感知     │
│  彩色输出     │  │  文件日志     │  │               │
│               │  │               │  │ • 线程名后缀  │
│ • Emoji 前缀  │  │ • 动态添加    │  │ • 线程注册    │
│ • 时间戳可选  │  │ • 过滤支持    │  │               │
└───────────────┘  └───────────────┘  └───────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| 日志实现 | `sweagent/sweagent/utils/log.py` | 1-176 | 完整日志系统 |

**代码示例**

```python
# sweagent/sweagent/utils/log.py:17-18
logging.TRACE = 5
logging.addLevelName(logging.TRACE, "TRACE")

# sweagent/sweagent/utils/log.py:44-54
class _RichHandlerWithEmoji(RichHandler):
    def __init__(self, emoji: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not emoji.endswith(" "):
            emoji += " "
        self.emoji = emoji

    def get_level_text(self, record: logging.LogRecord) -> Text:
        level_name = record.levelname.replace("WARNING", "WARN")
        return Text.styled(
            (self.emoji + level_name).ljust(10),
            f"logging.level.{level_name.lower()}"
        )
```

**动态文件处理器**

```python
# sweagent/sweagent/utils/log.py:93-131
def add_file_handler(
    path: PurePath | str,
    *,
    filter: str | Callable[[str], bool] | None = None,
    level: int | str = logging.TRACE,
    id_: str = "",
) -> str:
    """动态添加文件处理器到所有已创建的 logger"""
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    handler.setFormatter(formatter)

    with _LOG_LOCK:
        for name in _SET_UP_LOGGERS:
            if filter is not None:
                if isinstance(filter, str) and filter not in name:
                    continue
            logger = logging.getLogger(name)
            logger.addHandler(handler)
```

**特点**

- ✅ 标准库实现，无额外依赖（除 rich）
- ✅ 自定义 TRACE 级别（比 DEBUG 更详细）
- ✅ Rich 彩色输出，带 Emoji 前缀
- ✅ 线程感知，自动添加线程名后缀
- ✅ 动态文件处理器管理

---

## 4. 相同点总结

### 4.1 通用日志级别

| 级别 | 说明 | 通用性 |
|------|------|--------|
| ERROR | 错误，需要处理 | 5/5 |
| WARN | 警告，需要注意 | 5/5 |
| INFO | 普通信息 | 5/5 |
| DEBUG | 调试信息 | 5/5 |
| TRACE | 最详细追踪 | 2/5 (Codex, SWE-agent) |

### 4.2 异步/非阻塞处理

| Agent | 实现方式 | 目的 |
|-------|----------|------|
| Codex | `non_blocking` + channel | 避免 I/O 阻塞主线程 |
| Gemini CLI | Winston 内置异步 | 生产级性能 |
| Kimi CLI | loguru 默认异步 | 简化使用 |
| OpenCode | Bun.writer 异步 | 原生异步 I/O |
| SWE-agent | 标准库 Handler | 简单直接 |

### 4.3 配置方式

| Agent | 环境变量 | 代码配置 | 文件配置 |
|-------|----------|----------|----------|
| Codex | RUST_LOG | 有 | 有 |
| Gemini CLI | GEMINI_DEBUG_LOG_FILE | 有 | 无 |
| Kimi CLI | 无 | 有 | 无 |
| OpenCode | 无 | 有 | 无 |
| SWE-agent | SWE_AGENT_LOG_STREAM_LEVEL | 有 | 无 |

---

## 5. 不同点对比

### 5.1 日志库选择

| Agent | 日志库 | 类型 | 特点 | 性能特点 | 适用规模 |
|-------|--------|------|------|----------|----------|
| Codex | tracing + tracing-subscriber | Rust 生态 | 结构化、span 追踪 | 零开销探针，异步批处理 | 企业级 |
| Gemini CLI | winston + 自定义 | Node.js | 双模式、生产级 | 异步流式 | 中大型 |
| Kimi CLI | loguru | Python 第三方 | 简洁、强大 | 异步文件 I/O | 中小型 |
| OpenCode | 自定义 | Bun-native | 零依赖、轻量 | 原生 Bun I/O，无序列化开销 | 小型到中型 |
| SWE-agent | logging (stdlib) + rich | Python 标准库 | 标准、彩色 | 同步 I/O，简单直接 | 小型到中型 |

### 5.2 存储方式

| Agent | 控制台 | 文件 | SQLite | OpenTelemetry | 备注 |
|-------|--------|------|--------|---------------|------|
| Codex | ✅ | ✅ | ✅ | ✅ | 多目标同时 |
| Gemini CLI | ✅ | ✅ | ❌ | ❌ | 可选文件 |
| Kimi CLI | ✅ | ✅ | ❌ | ❌ | 可配置 sinks |
| OpenCode | ✅ | ✅ | ❌ | ❌ | 自动轮转 |
| SWE-agent | ✅ | ✅ | ❌ | ❌ | 动态添加 |

### 5.3 结构化日志

| Agent | 结构化格式 | 字段化 | 类型安全 |
|-------|------------|--------|----------|
| Codex | ✅ (JSON) | ✅ | Rust 类型 |
| Gemini CLI | ✅ (可选 JSON) | ✅ | TypeScript |
| Kimi CLI | ✅ (loguru) | ✅ | Python |
| OpenCode | ✅ (key=value) | ✅ | Zod |
| SWE-agent | ❌ | ❌ | Python |

### 5.4 日志轮转

| Agent | 轮转策略 | 保留数量 | 自动清理 |
|-------|----------|----------|----------|
| Codex | 时间-based (90天) | 无限 | SQLite 清理 |
| Gemini CLI | 无 | 1 | 手动 |
| Kimi CLI | 可配置 | 可配置 | 可配置 |
| OpenCode | 数量-based | 10个 | 自动 |
| SWE-agent | 无 | 1 | 手动 |

### 5.5 线程安全

| Agent | 线程安全 | 并发处理 | 特殊功能 |
|-------|----------|----------|----------|
| Codex | ✅ | async/await | 进程 UUID |
| Gemini CLI | ✅ | Node.js 事件循环 | - |
| Kimi CLI | ✅ | StderrRedirector | 子进程捕获 |
| OpenCode | ✅ | Bun 运行时 | - |
| SWE-agent | ✅ | threading.Lock | 线程名后缀 |

### 5.6 特殊功能

| Agent | 特殊功能 | 说明 |
|-------|----------|------|
| Codex | Span 追踪、OpenTelemetry | 分布式追踪 |
| Gemini CLI | Debug 抽屉 UI | 开发体验 |
| Kimi CLI | StderrRedirector | 子进程输出捕获 |
| OpenCode | Timing 工具 | 性能测量 |
| SWE-agent | Emoji 前缀、自定义 TRACE | 视觉区分 |

---

## 6. 设计意图与 Trade-off

### 6.1 各项目的选择

| 维度 | Codex | Gemini CLI | Kimi CLI | OpenCode | SWE-agent |
|-----|-------|------------|----------|----------|-----------|
| **日志库** | tracing | winston | loguru | 自定义 | stdlib |
| **存储** | 多目标 | 文件+控制台 | 可配置 | 文件 | 动态添加 |
| **结构化** | JSON | 可选 JSON | loguru格式 | key=value | 无 |
| **性能** | 零开销探针 | 异步流式 | 异步 I/O | 原生 Bun | 同步 I/O |

### 6.2 为什么这样设计？

**核心问题**：如何在性能、可观测性、复杂度之间取舍？

**Codex 的解决方案**：
- 代码依据：`codex/codex-rs/tui/src/lib.rs:354-421`
- 设计意图：企业级可观测性，支持分布式追踪
- 带来的好处：
  - 零开销探针，不影响性能
  - span 追踪支持链路分析
  - SQLite 存储支持查询
- 付出的代价：
  - 依赖复杂
  - 学习成本高

**SWE-agent 的解决方案**：
- 代码依据：`sweagent/sweagent/utils/log.py:1-176`
- 设计意图：简单、够用、易维护
- 带来的好处：
  - 无额外依赖
  - 代码简单易懂
  - 动态添加 handler 灵活
- 付出的代价：
  - 无结构化日志
  - 无分布式追踪

### 6.3 与其他项目的对比

```mermaid
flowchart LR
    subgraph Enterprise["企业级"]
        C[Codex<br/>tracing+OTel]
    end
    subgraph Production["生产级"]
        G[Gemini CLI<br/>winston]
    end
    subgraph Library["库友好"]
        K[Kimi CLI<br/>loguru]
    end
    subgraph Minimal["极简"]
        O[OpenCode<br/>自定义]
        S[SWE-agent<br/>stdlib]
    end

    Enterprise --> Production --> Library --> Minimal

    style C fill:#90EE90
    style G fill:#87CEEB
    style K fill:#FFD700
    style O fill:#FFB6C1
    style S fill:#FFB6C1
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| Codex | 完整 tracing 方案 | 企业级、需要分布式追踪 |
| Gemini CLI | 双模式设计 | 需要区分开发与生产环境 |
| Kimi CLI | 库友好设计 | 需要作为库被其他项目使用 |
| OpenCode | 零依赖 | 追求极致性能、控制依赖 |
| SWE-agent | 极简标准库 | 快速原型、简单需求 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 日志队列满 | Codex 队列超过 512 条 | `codex/codex-rs/state/src/log_db.rs:42` |
| 文件写入失败 | 磁盘满或权限不足 | `codex/codex-rs/tui/src/lib.rs:333-340` |
| 处理器移除 | 动态移除 file handler | `sweagent/sweagent/utils/log.py:134-141` |

### 7.2 超时/资源限制

**Codex 批处理配置**：
```rust
// codex/codex-rs/state/src/log_db.rs:42-45
const LOG_QUEUE_CAPACITY: usize = 512;   // 队列容量上限
const LOG_BATCH_SIZE: usize = 64;        // 每批处理数量
const LOG_FLUSH_INTERVAL: Duration = Duration::from_millis(250);  // 刷新间隔
```

**OpenCode 日志保留**：
```typescript
// opencode/packages/opencode/src/util/log.ts:86-89
if (files.length <= 5) return
const filesToDelete = files.slice(0, -10)  // 保留最近10个
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 文件写入失败 | 忽略错误，继续运行 | `opencode/packages/opencode/src/util/log.ts:89` |
| SQLite 写入失败 | 异步任务隔离，不影响主流程 | `codex/codex-rs/state/src/log_db.rs:55-56` |
| 日志流错误 | 回退到 console.error | `gemini-cli/packages/core/src/utils/debugLogger.ts:33-36` |

---

## 8. 关键代码索引

### 8.1 日志初始化

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/tui/src/lib.rs` | 326-421 | subscriber 初始化 |
| Gemini CLI | `gemini-cli/packages/a2a-server/src/utils/logger.ts` | 9-28 | winston 配置 |
| Gemini CLI | `gemini-cli/packages/core/src/utils/debugLogger.ts` | 23-69 | DebugLogger |
| Kimi CLI | `kimi-cli/src/kimi_cli/__init__.py` | 1-6 | loguru 启用/禁用 |
| OpenCode | `opencode/packages/opencode/src/util/log.ts` | 60-78 | init() |
| SWE-agent | `sweagent/sweagent/utils/log.py` | 57-91 | get_logger() |

### 8.2 日志核心实现

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/state/src/log_db.rs` | 42-62 | LogDbLayer |
| Gemini CLI | `gemini-cli/packages/a2a-server/src/utils/logger.ts` | 9-28 | winston 配置 |
| Kimi CLI | `kimi-cli/src/kimi_cli/utils/logging.py` | 15-125 | StderrRedirector |
| OpenCode | `opencode/packages/opencode/src/util/log.ts` | 100-181 | Log.create() |
| SWE-agent | `sweagent/sweagent/utils/log.py` | 44-54 | _RichHandlerWithEmoji |

### 8.3 配置管理

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/tui/src/lib.rs` | 347-352 | RUST_LOG 配置 |
| SWE-agent | `sweagent/sweagent/utils/log.py` | 31 | 环境变量读取 |

---

## 9. 快速上手：5分钟配置指南

### 9.1 Codex —— 从环境变量开始

```bash
# 基础级别
export RUST_LOG="info"

# 调试特定模块
export RUST_LOG="codex_core::agent=debug,codex_tui=info"

# 查看日志文件
tail -f ~/.codex/logs/codex-tui.log

# 查询 SQLite 日志（如果有 sqlite3）
sqlite3 ~/.codex/state.db "SELECT * FROM logs ORDER BY ts DESC LIMIT 10;"
```

### 9.2 Gemini CLI —— 开发与生产切换

```bash
# 开发调试：输出到文件
export GEMINI_DEBUG_LOG_FILE="/tmp/gemini.log"
npx @google/gemini-cli

# 实时查看
tail -f /tmp/gemini.log | jq -R '. as $line | try fromjson catch $line'

# 生产环境：Winston 自动配置，无需额外操作
```

### 9.3 Kimi CLI —— 库友好模式

```python
# 作为 CLI 使用（自动启用）
kimi chat

# 作为库使用（手动控制）
from loguru import logger
from kimi_cli import SomeTool

# 启用日志
logger.enable("kimi_cli")
logger.add("output.log", rotation="10 MB")

# 使用工具
tool = SomeTool()
```

### 9.4 OpenCode —— Bun-native 体验

```bash
# 开发模式（带颜色输出到控制台）
opencode --dev

# 查看日志文件
ls ~/.opencode/logs/
cat ~/.opencode/logs/2026-02-21T120000.log
```

### 9.5 SWE-agent —— 动态调试

```bash
# 基础运行
python -m sweagent run --config config.yaml

# 启用详细日志
export SWE_AGENT_LOG_STREAM_LEVEL=DEBUG
export SWE_AGENT_LOG_TIME=true

# 运行时添加文件日志（在代码中）
from sweagent.utils.log import add_file_handler
handler_id = add_file_handler("/tmp/debug.log", level=logging.TRACE)

# 之后移除
from sweagent.utils.log import remove_file_handler
remove_file_handler(handler_id)
```

---

## 10. 选型建议

### 10.1 按场景推荐

| 场景 | 推荐方案 | 理由 |
|------|----------|------|
| Rust 项目 | tracing + tracing-subscriber | 生态标准，功能强大 |
| Python 项目 | loguru | 简洁强大，库友好 |
| TypeScript/Node | winston | 生产级，生态成熟 |
| Bun 项目 | 自定义 (参考 OpenCode) | 零依赖，性能好 |
| 需要分布式追踪 | tracing + OpenTelemetry | 链路追踪集成 |
| 需要子进程捕获 | Kimi CLI 方案 | StderrRedirector |

### 10.2 按团队规模推荐

| 团队规模 | 推荐方案 | 理由 |
|----------|----------|------|
| 小型团队 | 标准库/简单方案 | 维护成本低 |
| 中型团队 | 成熟第三方库 | 功能与成本平衡 |
| 大型团队 | 完整 tracing 方案 | 可观测性要求高 |

### 10.3 关键决策点

```
是否需要分布式追踪？
├── 是 → 选择 tracing + OpenTelemetry (Codex 方案)
└── 否 → 是否需要子进程捕获？
    ├── 是 → 选择 loguru + StderrRedirector (Kimi CLI 方案)
    └── 否 → 项目规模？
        ├── 大型 → tracing / winston
        └── 小型 → 标准库 / 自定义
```

---

## 11. 附录

### 11.1 核心概念速查

| 术语 | 解释 | 类比 |
|------|------|------|
| **Logger** | 日志记录器，应用程序直接调用的接口 | `printk` |
| **Handler/Sink** | 日志处理器，决定日志输出到哪里 | VFS 层 |
| **Formatter** | 格式化器，决定日志长什么样 | 序列化器 |
| **Level** | 日志级别，控制输出详细程度 | 内核日志级别 |
| **Span** | 上下文追踪单元，记录一段代码的执行 | 函数调用栈 + 时间轴 |
| **Structured Log** | 结构化日志，机器可解析的格式 | JSON/Protobuf |

### 11.2 日志级别对照表

```
Python/Rust/通用    数值    使用场景
─────────────────────────────────────────
TRACE               5       最详细的函数调用追踪
DEBUG              10       开发调试信息
INFO               20       正常运行状态
WARNING/WARN       30       需要注意的异常
ERROR              40       操作失败，需要处理
CRITICAL/FATAL     50       系统无法继续运行
```

---

## 12. 边界与不确定性

- **⚠️ Inferred**: OpenTelemetry 的具体导出端点配置依赖于 `config` 中的遥测设置
- **⚠️ Inferred**: Feedback Layer 的具体实现位于 `codex_feedback` crate，本分析未深入
- **⚠️ Inferred**: `Global.Path.log` 的具体值未确认，可能在 `~/.opencode/logs/` 或项目目录
- **✅ Verified**: 所有核心实现代码已确认

---

*✅ Verified: 基于 codex/codex-rs/tui/src/lib.rs:326-421、gemini-cli/packages/a2a-server/src/utils/logger.ts:9-28、kimi-cli/src/kimi_cli/__init__.py:1-6、opencode/packages/opencode/src/util/log.ts:1-183、sweagent/sweagent/utils/log.py:1-176 等源码分析*
*基于版本：2026-02-08 | 最后更新：2026-02-25*
