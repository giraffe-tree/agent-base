# Codex 日志记录机制

## 引子：当你需要同时调试 10 个并发 tool 调用时...

想象一下这个场景：你的 Agent 正在并行执行 10 个 tool 调用，突然其中一个失败了。你查看日志，却发现所有输出混在一起，根本无法分辨哪个日志属于哪个调用。

这就是**并发日志**的挑战。传统的 `println!` 在这样的场景下完全失效：

```rust
// 问题：10 个并发调用，日志混在一起
[2024-01-20 10:00:01] 开始调用 tool: read_file
[2024-01-20 10:00:01] 开始调用 tool: write_file  // 哪个是哪个？
[2024-01-20 10:00:02] 调用完成                   // 又是哪个的完成？
```

Codex 的解决方案是**Span 追踪**：为每个并发操作创建一个上下文，所有相关日志都附着在这个上下文中。

```rust
// Span 追踪让并发日志清晰可辨
[2024-01-20 10:00:01] [span=read_file] 开始调用
[2024-01-20 10:00:01] [span=write_file] 开始调用
[2024-01-20 10:00:02] [span=read_file] 调用完成  // 清晰归属
```

本章深入解析 Codex 如何使用 Rust 的 `tracing` 框架实现企业级日志系统。

---

## 结论先行

Codex 采用 Rust 生态的 `tracing` 框架构建多层 subscriber 架构，同时支持文件日志、SQLite 持久化存储和 OpenTelemetry 分布式追踪，实现企业级可观测性。

---

## 技术类比：理解 tracing 生态

如果你熟悉 Linux 系统编程，可以把 `tracing` 比作以下组合：

| tracing 概念 | Linux 类比 | 核心思想 |
|-------------|-----------|---------|
| **Subscriber** | `ftrace` 的 tracer | 接收和处理事件的中心 |
| **Layer** | `ftrace` 的 filter + 多个输出管道 | 可组合的多目标输出 |
| **Span** | 函数图（function_graph）+ 时间戳 | 追踪调用链和耗时 |
| **OpenTelemetry** | `eBPF` + `perf` | 跨系统、跨进程的可观测性 |
| **LogDbLayer** | `systemd-journald` | 结构化存储，支持查询 |

### 为什么选择 SQLite 而不是纯文本？

| 特性 | 纯文本日志 | SQLite 日志 |
|------|-----------|-------------|
| 查询能力 | `grep`（基于正则） | `SELECT`（结构化查询） |
| 并发写入 | 需要锁机制 | 原生支持 |
| 存储效率 | 重复字段冗余 | 类型压缩 |
| 保留策略 | `logrotate` 外部配置 | SQL `DELETE` 语句 |
| 可观测性集成 | 需要解析 | 直接读取 |

Codex 的日志架构像 Linux 的追踪基础设施：
- `tracing` = `ftrace`（零开销的静态探针）
- `LogDbLayer` = `journald`（结构化存储，可查询）
- `OpenTelemetry` = `eBPF`（跨系统的可观测性）

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Code                         │
│         tracing::info! / tracing::debug!                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              tracing_subscriber::Registry                   │
│                  (中央事件分发器)                            │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌─────────────────┐   ┌─────────────────┐
│  File Layer   │   │   LogDbLayer    │   │ OpenTelemetry   │
│   文件日志     │   │   SQLite 存储   │   │   链路追踪       │
├───────────────┤   ├─────────────────┤   ├─────────────────┤
│ • non_blocking│   │ • 批处理插入     │   │ • traces        │
│ • with_target │   │ • 90天自动清理   │   │ • metrics       │
│ • FmtSpan     │   │ • thread_id 追踪 │   │ • logs          │
│ • chmod 600   │   │ • process_uuid  │   │                 │
└───────────────┘   └─────────────────┘   └─────────────────┘
```

---

## 核心概念：tracing 框架

### tracing vs log crate：编译期 vs 运行期

Rust 生态有两个主要的日志解决方案：

| 特性 | `log` crate | `tracing` |
|------|-------------|-----------|
| 过滤时机 | 运行时（日志产生后过滤） | 编译期（通过 feature flag） |
| 结构化 | 简单 key-value | 强大的 span 和 field |
| 异步友好 | 一般 | 原生支持 |
| 性能开销 | 中等 | 接近零（静态检查） |
| 学习曲线 | 平缓 | 较陡 |

Codex 选择 `tracing` 是因为 Agent 场景的特殊需求：
1. **异步并发**：大量 async/await 代码
2. **上下文追踪**：需要 span 来关联相关日志
3. **性能敏感**：不能容忍日志带来的额外开销

### Subscriber、Layer、Span 三要素

| 概念 | 说明 | 作用 |
|------|------|------|
| **Subscriber** | 日志订阅者 | 接收并处理所有 tracing 事件 |
| **Layer** | 分层装饰器 | 可组合的多目标输出（文件/SQLite/OTel） |
| **Span** | 上下文追踪 | 支持分布式链路追踪 |

---

## 初始化流程

**✅ Verified**: `codex/codex-rs/tui/src/lib.rs` 325-420行

```rust
// 1. 创建日志目录并设置文件权限
let log_dir = codex_core::config::log_dir(&config)?;
std::fs::create_dir_all(&log_dir)?;

let mut log_file_opts = OpenOptions::new();
log_file_opts.create(true).append(true);

// Unix 系统设置 600 权限（仅所有者可读写）
#[cfg(unix)]
{
    use std::os::unix::fs::OpenOptionsExt;
    log_file_opts.mode(0o600);
}

let log_file = log_file_opts.open(log_dir.join("codex-tui.log"))?;

// 2. 非阻塞文件写入器
let (non_blocking, _guard) = non_blocking(log_file);

// 3. RUST_LOG 环境变量过滤
let env_filter = || {
    EnvFilter::try_from_default_env().unwrap_or_else(|_| {
        EnvFilter::new("codex_core=info,codex_tui=info,codex_rmcp_client=info")
    })
};

// 4. 文件日志 Layer
let file_layer = tracing_subscriber::fmt::layer()
    .with_writer(non_blocking)
    .with_target(true)      // 保留模块路径便于过滤
    .with_ansi(false)       // 文件日志禁用 ANSI 颜色
    .with_span_events(
        tracing_subscriber::fmt::format::FmtSpan::NEW
            | tracing_subscriber::fmt::format::FmtSpan::CLOSE,
    )
    .with_filter(env_filter());

// 5. Feedback Layer（用户反馈收集）
let feedback = codex_feedback::CodexFeedback::new();
let feedback_layer = feedback.logger_layer();
let feedback_metadata_layer = feedback.metadata_layer();

// 6. OpenTelemetry Layer
let otel = codex_core::otel_init::build_provider(&config, ...);
let otel_logger_layer = otel.as_ref().and_then(|o| o.logger_layer());
let otel_tracing_layer = otel.as_ref().and_then(|o| o.tracing_layer());

// 7. SQLite LogDbLayer
let log_db_layer = codex_core::state_db::get_state_db(&config, None)
    .await
    .map(|db| log_db::start(db).with_filter(env_filter()));

// 8. 注册所有 Layer 到 Registry
tracing_subscriber::registry()
    .with(file_layer)
    .with(feedback_layer)
    .with(feedback_metadata_layer)
    .with(log_db_layer)
    .with(otel_logger_layer)
    .with(otel_tracing_layer)
    .try_init();
```

---

## LogDbLayer：SQLite 存储实现

**✅ Verified**: `codex/codex-rs/state/src/log_db.rs`

### 核心参数

```rust
const LOG_QUEUE_CAPACITY: usize = 512;      // 内存队列容量
const LOG_BATCH_SIZE: usize = 64;           // 批处理大小
const LOG_FLUSH_INTERVAL: Duration = Duration::from_millis(250);  // 刷新间隔
const LOG_RETENTION_DAYS: i64 = 90;         // 保留天数
```

### 存储结构

```rust
struct LogEntry {
    ts: i64,                    // 秒级时间戳
    ts_nanos: i64,              // 纳秒部分
    level: String,              // 日志级别
    target: String,             // 目标模块
    message: String,            // 消息内容
    thread_id: Option<String>,  // 线程ID
    process_uuid: Option<String>,  // 进程UUID
    module_path: Option<String>,
    file: Option<String>,
    line: Option<i64>,
}
```

### 批处理与清理机制

```rust
async fn run_inserter(
    state_db: Arc<StateRuntime>,
    mut receiver: mpsc::Receiver<LogEntry>,
) {
    let mut buffer = Vec::with_capacity(LOG_BATCH_SIZE);
    let mut ticker = tokio::time::interval(LOG_FLUSH_INTERVAL);
    loop {
        tokio::select! {
            maybe_entry = receiver.recv() => {
                match maybe_entry {
                    Some(entry) => {
                        buffer.push(entry);
                        if buffer.len() >= LOG_BATCH_SIZE {
                            flush(&state_db, &mut buffer).await;
                        }
                    }
                    None => {
                        flush(&state_db, &mut buffer).await;
                        break;
                    }
                }
            }
            _ = ticker.tick() => {
                flush(&state_db, &mut buffer).await;
            }
        }
    }
}

// 90天自动清理
async fn run_retention_cleanup(state_db: Arc<StateRuntime>) {
    let cutoff = Utc::now().checked_sub_signed(
        ChronoDuration::days(LOG_RETENTION_DAYS)
    );
    let _ = state_db.delete_logs_before(cutoff.timestamp()).await;
}
```

---

## Span 追踪在 Agent Loop 中的应用

### Span 创建与传播

```rust
// 在 Agent Loop 中创建 Span
let span = tracing::info_span!("agent_turn", thread_id = ?thread_id);
let _enter = span.enter();

// 子 Span 自动继承上下文
tracing::info!(parent: &span, "processing user request");
```

### Span 字段提取

```rust
impl<S> Layer<S> for LogDbLayer {
    fn on_new_span(&self, attrs: &Attributes<'_>, id: &Id, ctx: Context<'_, S>) {
        let mut visitor = SpanFieldVisitor::default();
        attrs.record(&mut visitor);

        if let Some(span) = ctx.span(id) {
            span.extensions_mut().insert(SpanLogContext {
                thread_id: visitor.thread_id,
            });
        }
    }
}
```

---

## 配置方式

### 环境变量

```bash
# 默认级别：info
export RUST_LOG="codex_core=debug,codex_tui=debug"

# 查看所有模块
codex --help 2>&1 | head -20
```

### 代码配置

```rust
// 动态调整过滤级别
let env_filter = EnvFilter::try_from_default_env()
    .unwrap_or_else(|_| EnvFilter::new("info"));
```

---

## 快速上手：5分钟掌握 Codex 日志

### 1. 基础环境变量配置

```bash
# 基础级别控制
export RUST_LOG="info"
codex

# 调试特定模块
export RUST_LOG="codex_core::agent=debug,codex_tui=info,codex_rmcp_client=trace"
codex

# 查看所有可用模块（从 help 输出推断）
codex --help 2>&1 | grep -E "^\s+\w+"
```

### 2. 实时查看日志文件

```bash
# 找到日志目录
ls ~/.codex/logs/

# 实时跟踪
tail -f ~/.codex/logs/codex-tui.log

# 过滤特定级别
grep "ERROR" ~/.codex/logs/codex-tui.log
```

### 3. 查询 SQLite 日志数据库

```bash
# 找到数据库文件
ls ~/.codex/*.db

# 使用 sqlite3 查询（如果已安装）
sqlite3 ~/.codex/state.db <<EOF
SELECT
    datetime(ts, 'unixepoch') as time,
    level,
    target,
    message
FROM logs
WHERE level = 'ERROR'
ORDER BY ts DESC
LIMIT 10;
EOF
```

### 4. 与 OpenTelemetry Collector 集成

```bash
# 配置环境变量
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_SERVICE_NAME="codex-agent"

# 运行 Codex（自动启用 OTel 导出）
codex
```

### 5. 常见问题排查

**Q: 日志文件在哪里？**
```bash
# 默认位置
~/.codex/logs/codex-tui.log
# 或者
~/.local/share/codex/logs/
```

**Q: 如何只查看 Agent 相关的详细日志？**
```bash
export RUST_LOG="codex_core::agent=trace,info"
# trace 级别只给 agent 模块，其他保持 info
```

**Q: 日志文件权限问题？**
```bash
# Codex 自动设置 600 权限（仅所有者可读写）
ls -l ~/.codex/logs/
# -rw------- 1 user group ... codex-tui.log
```

**Q: 如何导出特定时间段的日志？**
```bash
# 使用 SQLite 查询特定时间范围
sqlite3 ~/.codex/state.db "
SELECT * FROM logs
WHERE ts > strftime('%s', '2024-01-20')
  AND ts < strftime('%s', '2024-01-21')
ORDER BY ts;
"
```

---

## 证据索引

| 组件 | 文件路径 | 行号 | 关键职责 |
|------|----------|------|----------|
| 初始化 | `codex/codex-rs/tui/src/lib.rs` | 325-420 | 多层 subscriber 初始化 |
| LogDbLayer | `codex/codex-rs/state/src/log_db.rs` | 1-307 | SQLite 日志存储实现 |
| 状态数据库 | `codex/codex-rs/state/src/` | - | StateRuntime/LogEntry 定义 |
| OpenTelemetry | `codex/codex-rs/core/src/otel_init.rs` | - | OTel 初始化 |
| 配置 | `codex/codex-rs/core/src/config/` | - | 日志目录配置 |

---

## 边界与不确定性

- **⚠️ Inferred**: OpenTelemetry 的具体导出端点配置依赖于 `config` 中的遥测设置
- **⚠️ Inferred**: Feedback Layer 的具体实现位于 `codex_feedback` crate，本分析未深入
- **❓ Pending**: SQLite 表结构的具体 DDL 语句需查看 `state_db` 初始化代码
- **✅ Verified**: 文件权限设置为 600 的代码已确认（Unix 系统）

---

## 设计亮点

1. **多层架构**: 单一事件源分发到多个消费端（文件/SQLite/OTel）
2. **非阻塞 I/O**: `tracing_appender::non_blocking` 避免日志阻塞主流程
3. **批处理优化**: SQLite 写入采用 64条批量 + 250ms 定时刷新策略
4. **安全设计**: 日志文件默认 600 权限，防止敏感信息泄露
5. **可观测性**: 原生支持分布式链路追踪（OpenTelemetry）
