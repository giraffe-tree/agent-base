# 日志记录机制

## TL;DR

Agent 日志比普通应用复杂：多轮循环、并发工具执行、长时运行，日志需要能回答"某次工具调用花了多久"、"哪步开始行为异常"。核心选择是：结构化 JSON（可查询）vs 可读文本（可调试），以及是否引入 Span 追踪（Codex/tracing）。

---

## 引子：当 Agent 崩溃时，你在看什么？

想象一下这个场景：凌晨 2 点，你负责的 AI Agent 在生产环境崩溃了。用户反馈说"它突然就不动了"。你登录服务器，面对几千行 console 输出，却不知道从何看起...

这不是你的问题。这是**日志系统**的问题。

当你从简单的脚本转向复杂的 AI Agent 系统时，日志不再是"打印一句话"那么简单：
- **并发场景**：10 个 tool 调用同时进行，日志混成一团乱麻
- **分布式追踪**：用户请求经过 5 个服务，如何串联起来？
- **性能开销**：高频日志会不会拖慢 Agent 响应？
- **持久化查询**：3 个月前的某次执行记录，还能找到吗？

这就是为什么成熟的 AI Coding Agent 都有复杂的日志架构。理解它们，能帮助你在自己的项目中做出正确选择。

---

## 一句话总结

本文对比 5 个主流 AI Coding Agent（Codex、Gemini CLI、Kimi CLI、OpenCode、SWE-agent）的日志实现，从架构设计、存储方式、结构化日志、性能优化等维度分析差异，为技术选型提供参考。

---

## 技术类比：日志系统像什么？

理解日志系统的核心概念，可以借助熟悉的 Linux/系统编程概念：

| 技术概念 | 类比 | 核心思想 |
|---------|------|----------|
| **Logger** | Linux 内核的 `printk` / 驱动的 `dev_dbg` | 不同模块有不同的日志需求 |
| **Handler/Sink** | 文件系统的 **VFS 层** | 统一接口，多种后端实现（文件/控制台/网络） |
| **Formatter** | **协议序列化**（JSON/Protobuf） | 结构化 vs 文本格式的权衡 |
| **Log Level** | Linux Kernel 日志级别（0-7 KERN_EMERG ~ KERN_DEBUG） | 分级过滤，按需输出 |
| **Span（Rust tracing）** | **ftrace** 的函数图 + 时间轴 | 追踪调用链和耗时 |
| **OpenTelemetry** | **dtrace / eBPF** | 跨进程、跨机器的追踪能力 |
| **Loguru（Python）** | Python 的 **requests vs urllib** | 封装更好，但底层仍是 stdlib |

### 事件驱动 vs 请求-响应的日志差异

```
请求-响应模型（传统 Web 服务）：
┌────────┐  请求   ┌────────┐  响应   ┌────────┐
│ Client │ ──────▶ │ Server │ ──────▶ │ Client │
└────────┘         └────────┘         └────────┘
     │                  │
     │  一条日志: "GET /api/users 200 15ms"  │
     ▼                  ▼

事件驱动模型（Agent Loop）：
┌────────┐  用户输入  ┌────────┐  tool调用  ┌────────┐
│  User  │ ─────────▶ │ Agent  │ ─────────▶ │ Tool   │
└────────┘            │  Loop  │◀────────── │ Server │
     ▲                └────┬───┘   结果     └────────┘
     │                     │
     │    多种事件: LLM调用 │  思考过程 │  tool执行 │  状态变更
     └─────────────────────┘
```

Agent 的日志更复杂，因为：
1. **生命周期长**：一个任务可能持续数分钟，经历多次迭代
2. **异步并发**：多个 tool 并行执行
3. **状态机复杂**：思考 → 执行 → 观察 → 思考的循环

---

## 1. 概念定义

**日志记录（Logging）** 是 Agent CLI 用于记录运行时信息、调试问题、追踪执行流程的重要机制，对可观测性和问题排查至关重要。

### 核心挑战

- **性能影响**：高频日志不能阻塞主流程
- **可观测性**：结构化日志便于分析
- **持久化存储**：日志保留与轮转策略
- **分级控制**：不同场景需要不同详细程度
- **多目标输出**：文件、控制台、远程等

---

## 2. 各 Agent 实现

### 2.1 Codex (Rust) —— 企业级追踪方案

**类比**：Linux `ftrace` + `systemd-journald` 的组合

Codex 的日志系统像 Linux 内核的追踪基础设施：
- `tracing` = `ftrace`（零开销的静态探针）
- `LogDbLayer` = `journald`（结构化存储，可查询）
- `OpenTelemetry` = `eBPF`（跨系统的可观测性）

**实现概述**

Codex 使用 Rust 生态中最成熟的 `tracing` 框架，构建了多层 subscriber 架构，支持文件日志、SQLite 存储和 OpenTelemetry 导出。

**架构图**

```
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
| 初始化 | `codex-rs/tui/src/lib.rs` | 325-420 | 多层 subscriber 初始化 |
| SQLite | `codex-rs/state/src/log_db.rs` | 1 | LogDbLayer 实现 |
| 配置 | `codex-rs/core/src/config/` | - | 日志目录配置 |

**代码示例**

```rust
// 初始化多层日志系统
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
// LogDbLayer 核心参数
const LOG_QUEUE_CAPACITY: usize = 512;  // 队列容量
const LOG_BATCH_SIZE: usize = 64;       // 批处理大小
const LOG_FLUSH_INTERVAL: Duration = Duration::from_millis(250);  // 刷新间隔
const LOG_RETENTION_DAYS: i64 = 90;     // 保留天数

// LogEntry 字段
struct LogEntry {
    ts: i64,            // 秒级时间戳
    ts_nanos: i64,      // 纳秒部分
    level: String,      // 日志级别
    target: String,     // 目标模块
    message: String,    // 消息内容
    thread_id: Option<String>,  // 线程ID
    process_uuid: Option<String>,  // 进程UUID
    module_path: Option<String>,
    file: Option<String>,
    line: Option<i64>,
}
```

**特点**

- ✅ 多层 subscriber 架构（文件 + SQLite + OTel）
- ✅ 非阻塞 I/O，不影响主流程性能
- ✅ span 追踪，支持分布式链路追踪
- ✅ 90天自动清理策略
- ✅ RUST_LOG 环境变量配置

---

### 2.2 Gemini CLI (TypeScript) —— 双模式生产方案

**类比**：服务端 `rsyslog` + 开发时 `strace`

Gemini CLI 的日志设计体现了**环境区分**的思想：
- A2A Server 用 `winston` = 生产环境的 `rsyslog`（稳定、结构化）
- Core 用 `DebugLogger` = 开发时的 `strace`（详细、实时）

**实现概述**

Gemini CLI 采用双模式设计：A2A Server 使用 `winston` 生产级日志库，Core 使用自定义 `DebugLogger` 进行开发调试。

**架构图**

```
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
| Winston | `packages/a2a-server/src/utils/logger.ts` | 1 | A2A Server 日志 |
| DebugLogger | `packages/core/src/utils/debugLogger.ts` | 1 | 调试日志实现 |

**代码示例**

```typescript
// A2A Server - Winston
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

// Core - DebugLogger
class DebugLogger {
  private logStream: fs.WriteStream | undefined;

  constructor() {
    this.logStream = process.env['GEMINI_DEBUG_LOG_FILE']
      ? fs.createWriteStream(process.env['GEMINI_DEBUG_LOG_FILE'], { flags: 'a' })
      : undefined;
  }

  log(...args: unknown[]): void {
    this.writeToFile('LOG', args);
    console.log(...args);  // 被 ConsolePatcher 拦截到 UI
  }
}
```

**特点**

- ✅ 双模式设计（生产级 winston + 轻量 DebugLogger）
- ✅ ESLint 禁止直接使用 `console.*`，强制使用 DebugLogger
- ✅ 调试抽屉 UI 展示日志
- ✅ 可选文件输出，便于问题排查

---

### 2.3 Kimi CLI (Python) —— 库友好方案

**类比**：Python 的 `requests` vs `urllib`

Kimi CLI 选择 `loguru` 而不是标准库 `logging`，就像你选择 `requests` 而不是 `urllib`：
- **同样的底层能力**：最终都基于 Python 的日志基础设施
- **更好的 API 设计**：更直观、更少的样板代码
- **开箱即用的功能**：结构化、彩色输出、自动异常捕获

**实现概述**

Kimi CLI 使用 `loguru` 作为核心日志库，提供简洁的 API 和强大的结构化日志能力。特别实现了 `StderrRedirector` 捕获子进程 stderr 输出。

**架构图**

```
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
| 初始化 | `src/kimi_cli/__init__.py` | 1 | 日志禁用/启用 |
| StderrRedirector | `src/kimi_cli/utils/logging.py` | 15 | stderr 重定向 |

**代码示例**

```python
# __init__.py - 库友好设计
from loguru import logger

# 默认禁用，避免作为库使用时污染日志
logger.disable("kimi_cli")
# 应用入口点启用: logger.enable("kimi_cli")

# StderrRedirector - 捕获子进程输出
class StderrRedirector:
    def install(self) -> None:
        # 复制原始 stderr fd
        self._original_fd = os.dup(2)
        # 创建管道
        read_fd, write_fd = os.pipe()
        # 重定向 stderr 到管道
        os.dup2(write_fd, 2)
        os.close(write_fd)
        # 启动线程读取
        self._thread = threading.Thread(
            target=self._drain, name="kimi-stderr-redirect", daemon=True
        )
        self._thread.start()

    def _drain(self) -> None:
        # 读取管道输出并写入 logger
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            # 按行分割并记录
```

**特点**

- ✅ `loguru` 库友好（默认禁用，应用启用）
- ✅ `{var}` 插值语法
- ✅ `StderrRedirector` 捕获子进程输出
- ✅ 结构化日志支持

---

### 2.4 OpenCode (TypeScript) —— Bun-native 零依赖方案

**类比**：用 `BTF` 替代传统调试符号

OpenCode 选择自定义日志实现，就像 Linux 内核用 BTF（BPF Type Format）替代传统调试符号：
- **原生支持**：与运行时（Bun）深度集成
- **零外部依赖**：不依赖第三方库的版本兼容性
- **类型内嵌**：Zod 类型定义就像 BTF 信息，自描述、可验证

**实现概述**

OpenCode 使用 Bun-native 自定义日志实现，不依赖第三方日志库。支持 Key=value 结构化格式、Zod 类型安全和内置 timing 工具。

**架构图**

```
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
| 日志实现 | `packages/opencode/src/util/log.ts` | 1 | 完整日志系统 |

**代码示例**

```typescript
// Key=value 结构化格式
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

// 使用示例
const logger = Log.create({ service: "agent" })
logger.info("处理请求", { requestId: "123", userId: "456" })
// 输出: 2026-02-21T12:00:00 +0ms service=agent requestId=123 userId=456 处理请求

// Timing 工具
const timer = logger.time("API调用", { endpoint: "/chat" })
// ... 执行代码
timer.stop()  // 自动记录 duration
```

**日志轮转策略**

```typescript
async function cleanup(dir: string) {
  const glob = new Bun.Glob("????-??-??T??????.log")
  const files = await Array.fromAsync(glob.scan({ cwd: dir, absolute: true }))
  if (files.length <= 5) return
  // 保留最近的10个日志文件
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

### 2.5 SWE-agent (Python) —— 标准库极简方案

**类比**：用 `stdio` + `grep` 的组合

SWE-agent 的日志哲学像 Unix 的"小而美"工具链：
- `logging` = `stdio`（简单、通用、无处不在）
- `rich` = `colorgrep`（增强可读性，但不改变本质）
- `TRACE` 级别 = `grep -v` 的反向操作（比 DEBUG 更细粒度）

**实现概述**

SWE-agent 使用 Python 标准库 `logging` 配合 `rich` 实现彩色输出，提供自定义 TRACE 级别和线程感知功能。

**架构图**

```
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
| 日志实现 | `sweagent/utils/log.py` | 1 | 完整日志系统 |

**代码示例**

```python
# 自定义 TRACE 级别
logging.TRACE = 5
logging.addLevelName(logging.TRACE, "TRACE")

# 带 Emoji 的 RichHandler
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

# 线程感知 logger
def get_logger(name: str, *, emoji: str = "") -> logging.Logger:
    thread_name = threading.current_thread().name
    if thread_name != "MainThread":
        name = name + "-" + _THREAD_NAME_TO_LOG_SUFFIX.get(thread_name, thread_name)
    logger = logging.getLogger(name)
    # ...

# 使用示例
logger = get_logger("swe-agent", emoji="🤖")
logger.trace("详细追踪信息")
logger.info("普通信息")
```

**动态文件处理器**

```python
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

    handler.my_filter = filter
    _ADDITIONAL_HANDLERS[id_] = handler
    return id_
```

**特点**

- ✅ 标准库实现，无额外依赖（除 rich）
- ✅ 自定义 TRACE 级别（比 DEBUG 更详细）
- ✅ Rich 彩色输出，带 Emoji 前缀
- ✅ 线程感知，自动添加线程名后缀
- ✅ 动态文件处理器管理

---

## 3. 相同点总结

### 3.1 通用日志级别

| 级别 | 说明 | 通用性 |
|------|------|--------|
| ERROR | 错误，需要处理 | 5/5 |
| WARN | 警告，需要注意 | 5/5 |
| INFO | 普通信息 | 5/5 |
| DEBUG | 调试信息 | 5/5 |
| TRACE | 最详细追踪 | 2/5 (Codex, SWE-agent) |

### 3.2 异步/非阻塞处理

| Agent | 实现方式 | 目的 |
|-------|----------|------|
| Codex | `non_blocking` + channel | 避免 I/O 阻塞主线程 |
| Gemini CLI | Winston 内置异步 | 生产级性能 |
| Kimi CLI | loguru 默认异步 | 简化使用 |
| OpenCode | Bun.writer 异步 | 原生异步 I/O |
| SWE-agent | 标准库 Handler | 简单直接 |

### 3.3 配置方式

| Agent | 环境变量 | 代码配置 | 文件配置 |
|-------|----------|----------|----------|
| Codex | RUST_LOG | 有 | 有 |
| Gemini CLI | GEMINI_DEBUG_LOG_FILE | 有 | 无 |
| Kimi CLI | 无 | 有 | 无 |
| OpenCode | 无 | 有 | 无 |
| SWE-agent | SWE_AGENT_LOG_STREAM_LEVEL | 有 | 无 |

---

## 4. 不同点对比

### 4.1 日志库选择

| Agent | 日志库 | 类型 | 特点 | 性能特点 | 适用规模 |
|-------|--------|------|------|----------|----------|
| Codex | tracing + tracing-subscriber | Rust 生态 | 结构化、span 追踪 | 零开销探针，异步批处理 | 企业级 |
| Gemini CLI | winston + 自定义 | Node.js | 双模式、生产级 | 异步流式 | 中大型 |
| Kimi CLI | loguru | Python 第三方 | 简洁、强大 | 异步文件 I/O | 中小型 |
| OpenCode | 自定义 | Bun-native | 零依赖、轻量 | 原生 Bun I/O，无序列化开销 | 小型到中型 |
| SWE-agent | logging (stdlib) + rich | Python 标准库 | 标准、彩色 | 同步 I/O，简单直接 | 小型到中型 |

### 4.2 存储方式

| Agent | 控制台 | 文件 | SQLite | OpenTelemetry | 备注 |
|-------|--------|------|--------|---------------|------|
| Codex | ✅ | ✅ | ✅ | ✅ | 多目标同时 |
| Gemini CLI | ✅ | ✅ | ❌ | ❌ | 可选文件 |
| Kimi CLI | ✅ | ✅ | ❌ | ❌ | 可配置 sinks |
| OpenCode | ✅ | ✅ | ❌ | ❌ | 自动轮转 |
| SWE-agent | ✅ | ✅ | ❌ | ❌ | 动态添加 |

### 4.3 结构化日志

| Agent | 结构化格式 | 字段化 | 类型安全 |
|-------|------------|--------|----------|
| Codex | ✅ (JSON) | ✅ | Rust 类型 |
| Gemini CLI | ✅ (可选 JSON) | ✅ | TypeScript |
| Kimi CLI | ✅ (loguru) | ✅ | Python |
| OpenCode | ✅ (key=value) | ✅ | Zod |
| SWE-agent | ❌ | ❌ | Python |

### 4.4 日志轮转

| Agent | 轮转策略 | 保留数量 | 自动清理 |
|-------|----------|----------|----------|
| Codex | 时间-based (90天) | 无限 | SQLite 清理 |
| Gemini CLI | 无 | 1 | 手动 |
| Kimi CLI | 可配置 | 可配置 | 可配置 |
| OpenCode | 数量-based | 10个 | 自动 |
| SWE-agent | 无 | 1 | 手动 |

### 4.5 线程安全

| Agent | 线程安全 | 并发处理 | 特殊功能 |
|-------|----------|----------|----------|
| Codex | ✅ | async/await | 进程 UUID |
| Gemini CLI | ✅ | Node.js 事件循环 | - |
| Kimi CLI | ✅ | StderrRedirector | 子进程捕获 |
| OpenCode | ✅ | Bun 运行时 | - |
| SWE-agent | ✅ | threading.Lock | 线程名后缀 |

### 4.6 特殊功能

| Agent | 特殊功能 | 说明 |
|-------|----------|------|
| Codex | Span 追踪、OpenTelemetry | 分布式追踪 |
| Gemini CLI | Debug 抽屉 UI | 开发体验 |
| Kimi CLI | StderrRedirector | 子进程输出捕获 |
| OpenCode | Timing 工具 | 性能测量 |
| SWE-agent | Emoji 前缀、自定义 TRACE | 视觉区分 |

---

## 5. 快速上手：5分钟配置指南

### 5.1 Codex —— 从环境变量开始

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

**常见问题**：
- 日志在哪？`~/.codex/logs/` 和 SQLite 数据库
- 如何过滤？使用 `RUST_LOG` 模块路径过滤

### 5.2 Gemini CLI —— 开发与生产切换

```bash
# 开发调试：输出到文件
export GEMINI_DEBUG_LOG_FILE="/tmp/gemini.log"
npx @google/gemini-cli

# 实时查看
tail -f /tmp/gemini.log | jq -R '. as $line | try fromjson catch $line'

# 生产环境：Winston 自动配置，无需额外操作
```

**与 DevTools 集成**：
调试抽屉 UI 支持实时过滤和搜索，按 `Ctrl+D` 打开。

### 5.3 Kimi CLI —— 库友好模式

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

**粒度控制**：
```python
# 只启用特定模块
logger.enable("kimi_cli.agent")
logger.disable("kimi_cli.utils")
```

### 5.4 OpenCode —— Bun-native 体验

```bash
# 开发模式（带颜色输出到控制台）
opencode --dev

# 查看日志文件
ls ~/.opencode/logs/
cat ~/.opencode/logs/2026-02-21T120000.log

# 结构化解析
jq -R 'split(" ")' ~/.opencode/logs/*.log
```

**与 OpenTelemetry Collector 集成**：
```typescript
// 配置 OTLP 端点
const logger = Log.create({
  service: "agent",
  otlpEndpoint: "http://localhost:4317"
})
```

### 5.5 SWE-agent —— 动态调试

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

**动态调试技巧**：
```python
# 在多进程场景中为特定线程添加日志
register_thread_name("worker-1")
logger = get_logger("agent", emoji="🤖")
# 输出: 2024-02-21 ... agent-worker-1 🤖 INFO ...
```

---

## 6. 源码索引

### 6.1 日志初始化

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/tui/src/lib.rs` | 325-420 | subscriber 初始化 |
| Gemini CLI | `packages/a2a-server/src/utils/logger.ts` | 1 | winston 配置 |
| Gemini CLI | `packages/core/src/utils/debugLogger.ts` | 1 | DebugLogger |
| Kimi CLI | `src/kimi_cli/__init__.py` | 1 | loguru 启用/禁用 |
| OpenCode | `packages/opencode/src/util/log.ts` | 58-74 | init() |
| SWE-agent | `sweagent/utils/log.py` | 57-91 | get_logger() |

### 6.2 日志核心实现

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/state/src/log_db.rs` | 1 | LogDbLayer |
| Gemini CLI | `packages/a2a-server/src/utils/logger.ts` | 9-28 | winston 配置 |
| Kimi CLI | `src/kimi_cli/utils/logging.py` | 15-96 | StderrRedirector |
| OpenCode | `packages/opencode/src/util/log.ts` | 98-179 | Log.create() |
| SWE-agent | `sweagent/utils/log.py` | 44-56 | _RichHandlerWithEmoji |

### 6.3 配置管理

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| Codex | `codex/codex-rs/core/src/config/` | - | 日志目录配置 |
| SWE-agent | `sweagent/utils/log.py` | 31 | 环境变量读取 |

---

## 7. 选型建议

### 7.1 按场景推荐

| 场景 | 推荐方案 | 理由 |
|------|----------|------|
| Rust 项目 | tracing + tracing-subscriber | 生态标准，功能强大 |
| Python 项目 | loguru | 简洁强大，库友好 |
| TypeScript/Node | winston | 生产级，生态成熟 |
| Bun 项目 | 自定义 (参考 OpenCode) | 零依赖，性能好 |
| 需要分布式追踪 | tracing + OpenTelemetry | 链路追踪集成 |
| 需要子进程捕获 | Kimi CLI 方案 | StderrRedirector |

### 7.2 按团队规模推荐

| 团队规模 | 推荐方案 | 理由 |
|----------|----------|------|
| 小型团队 | 标准库/简单方案 | 维护成本低 |
| 中型团队 | 成熟第三方库 | 功能与成本平衡 |
| 大型团队 | 完整 tracing 方案 | 可观测性要求高 |

### 7.3 关键决策点

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

## 8. 日志最佳实践

### 8.1 性能建议

```
高吞吐场景：
- 使用非阻塞 I/O (Codex non_blocking)
- 批处理写入 (Codex batch size 64)
- 异步 flush (Codex 250ms 间隔)

避免：
- 同步文件写入
- 单条 flush
- 高频字符串拼接
```

### 8.2 结构化日志建议

```
推荐格式：
- Key=value 便于解析 (OpenCode)
- JSON 便于机器处理 (Gemini CLI 可选)
- 包含时间戳、级别、服务名、追踪ID

包含字段：
- timestamp (ISO 8601)
- level (日志级别)
- service (服务名)
- trace_id (追踪ID)
- message (消息)
- context (上下文)
```

### 8.3 安全建议

```
日志脱敏：
- 敏感信息不记录
- Token/密码打码
- 用户数据匿名化

文件权限：
- 日志文件 600 (Codex 实现)
- 定期清理旧日志
- 审计日志独立存储
```

---

## 9. 边界与不确定性

- **⚠️ Inferred**: OpenTelemetry 的具体导出端点配置依赖于 `config` 中的遥测设置
- **⚠️ Inferred**: Feedback Layer 的具体实现位于 `codex_feedback` crate，本分析未深入
- **⚠️ Inferred**: `Global.Path.log` 的具体值未确认，可能在 `~/.opencode/logs/` 或项目目录
- **✅ Verified**: 所有核心实现代码已确认

---

## 10. 附录：Logging 101

### 核心概念速查

| 术语 | 解释 | 类比 |
|------|------|------|
| **Logger** | 日志记录器，应用程序直接调用的接口 | `printk` |
| **Handler/Sink** | 日志处理器，决定日志输出到哪里 | VFS 层 |
| **Formatter** | 格式化器，决定日志长什么样 | 序列化器 |
| **Level** | 日志级别，控制输出详细程度 | 内核日志级别 |
| **Span** | 上下文追踪单元，记录一段代码的执行 | 函数调用栈 + 时间轴 |
| **Structured Log** | 结构化日志，机器可解析的格式 | JSON/Protobuf |

### 日志级别对照表

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
