# UI Interaction（codex）

本文基于 `codex/codex-rs/tui` 源码，解释 Codex TUI（终端用户界面）的交互设计，包括语法高亮系统、`/clear` 命令实现等。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        TUI 架构分层                                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   ChatWidget    │ ◄── 主聊天界面组件
│                 │     - 消息历史显示
│     ┌───────────┤     - 用户输入处理
│     │ 消息列表  │     - Slash 命令解析
│     ├───────────┤
│     │ 输入区域  │
│     ├───────────┤
│     │ 底部面板  │ ◄── 主题选择器、确认弹窗
│     └───────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  SlashCommand   │ ◄── 斜杠命令枚举
│  - Clear        │
│  - Theme        │
│  - New, Quit... │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  highlight.rs   │ ◄── 语法高亮引擎 (syntect)
└─────────────────┘
```

---

## 2. 语法高亮系统

### 2.1 一句话定义

Codex 使用 **syntect** 库结合 **two_face** 主题包，提供支持 250+ 语言和 32 款内置主题的语法高亮，同时支持自定义 `.tmTheme` 文件。

### 2.2 技术栈

| 组件 | 用途 | 版本 |
|------|------|------|
| syntect | 语法高亮引擎 | 核心依赖 |
| two_face | 语法定义和主题包 | ~250 语言, 32 主题 |
| ratatui | 终端渲染 | UI 框架 |

### 2.3 核心架构

```rust
// codex/codex-rs/tui/src/render/highlight.rs

// 全局单例
static SYNTAX_SET: OnceLock<SyntaxSet> = OnceLock::new();      // 语法数据库
static THEME: OnceLock<RwLock<Theme>> = OnceLock::new();       // 当前主题
static THEME_OVERRIDE: OnceLock<Option<String>> = OnceLock::new(); // 用户偏好
static CODEX_HOME: OnceLock<Option<PathBuf>> = OnceLock::new();    // 自定义主题路径
```

### 2.4 32 款内置主题

```rust
// 完整主题列表 (highlight.rs:331-364)
const BUILTIN_THEME_NAMES: &[&str] = &[
    "ansi", "base16", "base16-256",
    "base16-eighties-dark", "base16-mocha-dark",
    "base16-ocean-dark", "base16-ocean-light",
    "catppuccin-frappe", "catppuccin-latte",
    "catppuccin-macchiato", "catppuccin-mocha",
    "coldark-cold", "coldark-dark", "dark-neon",
    "dracula", "github", "gruvbox-dark", "gruvbox-light",
    "inspired-github", "1337",
    "monokai-extended", "monokai-extended-bright",
    "monokai-extended-light", "monokai-extended-origin",
    "nord", "one-half-dark", "one-half-light",
    "solarized-dark", "solarized-light",
    "sublime-snazzy", "two-dark", "zenburn",
];
```

### 2.5 安全防护限制

```rust
// highlight.rs:437-442
const MAX_HIGHLIGHT_BYTES: usize = 512 * 1024;  // 512 KB 上限
const MAX_HIGHLIGHT_LINES: usize = 10_000;       // 10000 行上限

pub(crate) fn exceeds_highlight_limits(total_bytes: usize, total_lines: usize) -> bool {
    total_bytes > MAX_HIGHLIGHT_BYTES || total_lines > MAX_HIGHLIGHT_LINES
}
```

超过限制的输入将回退到纯文本显示，防止 CPU/内存过度使用。

### 2.6 主题选择器 (/theme)

```rust
// theme_picker.rs 功能特性

1. **实时预览**: 光标移动时即时切换主题
2. **取消恢复**: Esc/Ctrl+C 恢复原始主题
3. **持久化**: 确认后写入 `config.toml`
4. **自定义主题**: 支持 `{CODEX_HOME}/themes/*.tmTheme`
```

**预览布局**:
- 宽屏模式: 左右分栏，右侧预览 diff 代码
- 窄屏模式: 上下堆叠，4 行精简预览

### 2.7 语言支持

```rust
// highlight.rs:788-846 支持的语言示例
[
    "javascript", "typescript", "tsx", "python", "ruby", "rust",
    "go", "c", "cpp", "yaml", "bash", "kotlin", "markdown",
    "sql", "lua", "zig", "swift", "java", "elixir", "haskell",
    // 别名映射
    "golang" -> "go", "python3" -> "python", "shell" -> "bash",
]
```

---

## 3. /clear 命令

### 3.1 功能定义

`/clear` 命令清除终端屏幕和滚动缓冲区，但**保留当前对话上下文**。

```rust
// slash_command.rs:71
SlashCommand::Clear => "clear the terminal screen and scrollback",
```

### 3.2 实现细节

```rust
// chatwidget.rs:3312-3314
SlashCommand::Clear => {
    self.app_event_tx.send(AppEvent::ClearUi);
}
```

### 3.3 状态检查

```rust
// slash_command.rs:136-144
// Clear 命令在任务运行时禁用
| SlashCommand::Clear => false,  // requires_idle = false
```

### 3.4 Terminal.app 特殊处理

Codex 的 `/clear` 针对 macOS Terminal.app 做了特殊处理，确保滚动缓冲区被正确清除（不同于简单的 `clear` 命令）。

### 3.5 与其他命令对比

| 命令 | 功能 | 保留上下文 |
|------|------|----------|
| `/clear` | 清屏 | ✅ 是 |
| `/new` | 新建会话 | ❌ 否 |
| `/quit` | 退出程序 | ❌ 否 |

---

## 4. 证据索引

| 组件 | 文件路径 | 关键职责 |
|------|----------|----------|
| 语法高亮引擎 | `tui/src/render/highlight.rs` | syntect 封装, 250+ 语言, 32 主题 |
| 主题选择器 | `tui/src/theme_picker.rs` | /theme 命令, 实时预览 |
| 斜杠命令 | `tui/src/slash_command.rs` | Clear, Theme 等命令定义 |
| 聊天组件 | `tui/src/chatwidget.rs` | 命令分发, UI 更新 |
| TUI 应用 | `tui/src/app.rs` | 事件处理, 配置管理 |

---

## 5. 总结

- **语法高亮**: 基于 syntect，32 款内置主题，支持自定义 `.tmTheme`
- **安全防护**: 512KB / 10000 行上限，超限回退到纯文本
- **/clear 命令**: 清屏但保留上下文，任务运行时禁用
- **实时预览**: 主题选择器支持即时预览和取消恢复
