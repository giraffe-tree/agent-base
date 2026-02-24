# CLI Entry（codex）

本文基于 `codex/codex-rs/cli` 和 `codex/codex-rs/tui` 源码，解释 Codex 的 CLI 入口设计——从命令行参数解析到 TUI 启动的完整链路。

---

## 1. 先看全局（流程图）

```text
┌─────────────────────────────────────────────────────────────────┐
│  启动入口：main()                                                 │
│  ┌────────────────────────────────────────┐                     │
│  │ codex/codex-rs/tui/src/main.rs         │                     │
│  │  └── fn main()                         │                     │
│  │       └── codex_tui::cli_main().await  │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CLI 参数解析：MultitoolCli                                       │
│  ┌────────────────────────────────────────┐                     │
│  │ codex/codex-rs/tui/src/cli.rs          │                     │
│  │  ├── config_overrides: 配置覆盖        │                     │
│  │  ├── feature_toggles: 功能开关         │                     │
│  │  └── subcommand: 子命令分发            │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  模式分支：交互式 TUI vs 非交互式执行                             │
│  ┌─────────────────┐  ┌─────────────────┐                       │
│  │ 无子命令        │  │ Subcommand::Exec│                       │
│  │ └── 启动 TUI    │  │ └── 执行命令    │                       │
│  │                 │  │                 │                       │
│  │ Subcommand::Mcp │  │ Subcommand::    │                       │
│  │ └── MCP 管理    │  │    Review       │                       │
│  │                 │  │ └── 代码审查    │                       │
│  └─────────────────┘  └─────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件详解

### 2.1 入口点层次

| 文件 | 函数 | 职责 |
|------|------|------|
| `tui/src/main.rs` | `main()` | 程序入口，调用 `cli_main()` |
| `tui/src/lib.rs` | `cli_main()` | 初始化日志、配置、运行 TUI |
| `tui/src/cli.rs` | `MultitoolCli` | 命令行参数定义 |

### 2.2 CLI 参数结构

```rust
// codex/codex-rs/tui/src/cli.rs
#[derive(Debug, Parser)]
pub struct MultitoolCli {
    #[clap(flatten)]
    pub config_overrides: CliConfigOverrides,  // --model, --sandbox 等
    
    #[clap(flatten)]
    pub feature_toggles: FeatureToggles,       // --full-auto, --no-approval 等
    
    #[clap(subcommand)]
    pub subcommand: Option<Subcommand>,        // exec, review, mcp, ...
}
```

**设计意图**：使用 `clap` 的 flatten 特性，将相关参数分组，避免单个结构体过于庞大。

### 2.3 子命令枚举

```rust
// codex/codex-rs/tui/src/cli.rs
pub enum Subcommand {
    /// 非交互式执行模式
    Exec(ExecArgs),
    /// 代码审查模式
    Review(ReviewArgs),
    /// MCP 服务器管理
    Mcp(McpArgs),
    /// 恢复历史会话
    Resume(ResumeArgs),
    /// 分叉会话
    Fork(ForkArgs),
    /// ... 更多命令
}
```

### 2.4 TUI 启动流程

```rust
// codex/codex-rs/tui/src/lib.rs:cli_main()
pub async fn cli_main() -> anyhow::Result<AppExit> {
    // 1. 解析命令行参数
    let cli = MultitoolCli::parse();
    
    // 2. 加载配置（ layered config: default + file + env + cli ）
    let config = load_config(&cli).await?;
    
    // 3. 初始化日志系统（file + SQLite + OpenTelemetry）
    init_logging(&config).await?;
    
    // 4. 根据子命令分发
    match cli.subcommand {
        Some(Subcommand::Exec(args)) => run_exec_mode(args, config).await,
        Some(Subcommand::Review(args)) => run_review_mode(args, config).await,
        None => run_interactive_tui(config).await,  // 默认：交互式 TUI
    }
}
```

---

## 3. 配置加载机制（Layered Config）

Codex 采用**分层配置**设计，优先级从低到高：

```
1. 内置默认值
2. ~/.codex/config.toml（用户配置文件）
3. 环境变量（如 CODEX_MODEL）
4. 命令行参数（--model, --sandbox 等）
```

### 3.1 配置合并逻辑

```rust
// 伪代码示意
let config = default_config()
    .merge_file("~/.codex/config.toml")?
    .merge_env()?
    .merge_cli(&cli.config_overrides)?;
```

**工程 Trade-off**：
- ✅ 灵活性：用户可在多个层面覆盖配置
- ✅ 可预测性：明确的优先级顺序
- ⚠️ 复杂度：需要处理配置冲突和验证

---

## 4. 日志初始化细节

```rust
// codex/codex-rs/tui/src/lib.rs:325-421
async fn init_logging(config: &Config) -> Result<()> {
    // 1. 创建日志目录
    let log_dir = codex_core::config::log_dir(&config)?;
    std::fs::create_dir_all(&log_dir)?;
    
    // 2. Unix 权限控制（600 = 仅所有者可读写）
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        log_file_opts.mode(0o600);
    }
    
    // 3. 多层 subscriber
    tracing_subscriber::registry()
        .with(file_layer)           // 文件日志
        .with(log_db_layer)         // SQLite 存储
        .with(otel_layer)           // OpenTelemetry
        .try_init()?;
}
```

**安全设计**：日志文件默认 `600` 权限，防止敏感信息泄露。

---

## 5. 交互式 vs 非交互式模式

| 模式 | 触发条件 | 适用场景 |
|------|----------|----------|
| **TUI 模式** | 无子命令 | 日常开发，多轮对话 |
| **Exec 模式** | `codex exec "prompt"` | CI/CD，脚本集成 |
| **Review 模式** | `codex review` | 代码审查，PR 检查 |
| **MCP 模式** | `codex mcp` | MCP 服务器管理 |

### 5.1 Exec 模式（非交互式）

```bash
# 单次执行，适合自动化
codex exec "fix the bug in src/main.rs" --model gpt-5.2

# 输出到 stdout，可管道处理
codex exec "generate test cases" > tests.rs
```

### 5.2 TUI 模式（交互式）

```bash
# 启动交互式界面
codex

# 加载指定目录
codex /path/to/project
```

---

## 6. 证据索引

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 程序入口 | `tui/src/main.rs` | `main()` 函数 |
| CLI 主逻辑 | `tui/src/lib.rs` | `cli_main()`, 日志初始化 |
| 参数定义 | `tui/src/cli.rs` | `MultitoolCli`, `Subcommand` |
| 配置管理 | `core/src/config/` | 分层配置加载 |
| 非交互执行 | `exec/src/lib.rs` | Exec 模式实现 |

---

## 7. 架构特点总结

- **分层配置**：内置默认值 → 配置文件 → 环境变量 → 命令行参数
- **子命令架构**：清晰的命令分离（exec/review/mcp/...）
- **安全日志**：600 权限控制，多层输出（file/SQLite/OTel）
- **模式分离**：交互式 TUI 与非交互式执行完全分离
