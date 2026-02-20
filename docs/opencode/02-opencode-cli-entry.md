# CLI Entry（opencode）

本文基于 `packages/opencode/src/` 源码，解释 opencode 的命令行接口设计、参数解析机制和命令分发流程。

---

## 1. 先看全局（流程图）

### 1.1 CLI 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│  ENTRY: opencode [command] [options]                            │
│  ┌─────────────────┐                                            │
│  │ bin/opencode    │ ◄──── 平台检测 + 二进制分发                 │
│  │ (Node wrapper)  │                                            │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CORE: packages/opencode/src/index.ts                           │
│  ┌────────────────────────────────────────┐                     │
│  │ yargs(hideBin(process.argv))           │                     │
│  │  ├── parserConfiguration()             │ ◄── "populate--"    │
│  │  ├── middleware()                      │ ◄── 日志 + 数据库迁移│
│  │  └── 注册所有命令                      │                     │
│  │       ├── AcpCommand                   │                     │
│  │       ├── McpCommand                   │                     │
│  │       ├── RunCommand (default)         │                     │
│  │       ├── ThreadCommand/TuiCommand     │                     │
│  │       ├── GenerateCommand              │                     │
│  │       ├── DebugCommand                 │                     │
│  │       └── ...                          │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  命令执行                                                        │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │ CommandModule<T, U>                                     │     │
│  │  ├── command   → 命令名                                │     │
│  │  ├── describe  → 描述                                  │     │
│  │  ├── builder   → yargs 配置                            │     │
│  │  └── handler   → 执行逻辑                              │     │
│  └─────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ yargs 模块  ──┤ 配置  ▼ 执行流向
```

### 1.2 命令树结构

```text
opencode
│
├── acp              # Agent Communication Protocol
├── mcp              # Model Context Protocol
├── thread / tui     # TUI 线程管理
├── attach           # 附加到现有会话
├── run (default)    # 运行 agent（默认命令）
├── generate         # 代码生成
├── debug            # 调试工具集
│   ├── config
│   ├── file
│   ├── lsp
│   ├── ripgrep
│   ├── skill
│   ├── agent
│   ├── paths
│   └── wait
├── auth             # 认证管理
├── agent            # Agent 管理
├── upgrade          # 自升级
├── uninstall        # 卸载
├── serve            # 启动服务器
├── web              # Web 界面
├── models           # 模型管理
├── stats            # 统计信息
├── export           # 数据导出
├── import           # 数据导入
├── github           # GitHub 集成
├── pr               # PR 管理
├── session          # 会话管理
└── db               # 数据库操作

图例: run 为默认命令，无显式命令时执行
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道入口结构和主要命令）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道命令结构和初始化流程）。
- **10 分钟版**：通读 `3~8`（能添加新命令和修改参数解析）。

### 2.1 一句话定义

opencode CLI 采用「**yargs + Command Module + 平台二进制分发**」设计：使用 yargs 处理命令路由，Command Module 模式组织子命令，支持跨平台二进制分发和自升级。

---

## 3. 核心组件

### 3.1 平台二进制包装器

**文件**: `packages/opencode/bin/opencode`

```javascript
#!/usr/bin/env node
const platform = process.platform;      // darwin, linux, win32
const arch = process.arch;              // x64, arm64, arm

// 检查 AVX2 支持 (x64 系统)
function checkAVX2() { ... }

// 检测 libc 类型 (Linux)
function detectLibc() { ... }

// 构建二进制文件名
const binaryName = `opencode-${platform}-${arch}${libc}${exe}`;

// 执行对应二进制文件
require('child_process').spawnSync(...)
```

特性：
- 跨平台支持（Windows、macOS、Linux）
- 多架构支持（x64、arm64、arm）
- AVX2 检测（x64 系统）
- libc 检测（Linux musl vs glibc）
- 环境变量覆盖 `OPENCODE_BIN_PATH`

### 3.2 yargs 配置

**文件**: `packages/opencode/src/index.ts:47-156`

```typescript
const cli = yargs(hideBin(process.argv))
  .parserConfiguration({ "populate--": true })  // 支持 -- 语法
  .scriptName("opencode")
  .wrap(100)
  .help("help", "show help")
  .alias("help", "h")
  .version("version", "show version number", Installation.VERSION)
  .alias("version", "v")
  .option("print-logs", {
    describe: "print logs to stderr",
    type: "boolean",
  })
  .option("log-level", {
    describe: "log level",
    type: "string",
    choices: ["DEBUG", "INFO", "WARN", "ERROR"],
  })
  .middleware(async (opts) => {
    // 初始化日志
    await Log.init({ ... })

    // 数据库迁移（首次运行）
    if (!(await Bun.file(marker).exists())) {
      await JsonMigration.run(Database.Client().$client, { ... })
    }
  })
  .completion("completion", "generate shell completion script")
  .command(AcpCommand)
  .command(McpCommand)
  .command(TuiThreadCommand)
  .command(AttachCommand)
  .command(RunCommand)
  // ... 更多命令
```

### 3.3 Command Module 结构

**文件**: `packages/opencode/src/cli/cmd/cmd.ts`

```typescript
export function cmd<T, U>(input: CommandModule<T, WithDoubleDash<U>>) {
  return input
}

// 使用示例
export const RunCommand = cmd({
  command: "run [prompt]",
  describe: "Run the agent",
  builder: (yargs) => {
    return yargs
      .option("dir", { type: "string", alias: "d" })
      .option("model", { type: "string", alias: "m" })
      // ...
  },
  handler: async (argv) => {
    // 执行逻辑
  },
})
```

### 3.4 全局中间件

**文件**: `packages/opencode/src/index.ts:64-119`

```typescript
.middleware(async (opts) => {
  // 初始化日志系统
  await Log.init({
    print: process.argv.includes("--print-logs"),
    dev: Installation.isLocal(),
    level: opts.logLevel || (Installation.isLocal() ? "DEBUG" : "INFO"),
  })

  // 设置环境变量
  process.env.AGENT = "1"
  process.env.OPENCODE = "1"

  // 首次运行：数据库迁移
  const marker = path.join(Global.Path.data, "opencode.db")
  if (!(await Bun.file(marker).exists())) {
    // 显示进度条并执行迁移
    await JsonMigration.run(Database.Client().$client, {
      progress: (event) => { ... }
    })
  }
})
```

---

## 4. 命令详解

### 4.1 opencode / opencode run（默认命令）

**文件**: `packages/opencode/src/cli/cmd/run.ts`

运行 agent（默认命令）：

```bash
# 交互式运行
opencode

# 带提示运行
opencode run "解释这段代码"

# 指定目录
opencode run -d /path/to/project

# 指定模型
opencode run -m claude-sonnet-4-6

# 禁用工具
opencode run --no-tools

# 接受所有操作
opencode run --yolo

# 附加文件
opencode run --attach file.txt

# 等待输入后退出
opencode run --wait
```

### 4.2 opencode thread / tui（TUI 模式）

**文件**: `packages/opencode/src/cli/cmd/tui/thread.ts`

```bash
# 启动 TUI
opencode thread
opencode tui

# 指定线程
opencode thread --id <thread-id>

# 创建新线程
opencode thread --new
```

### 4.3 opencode attach（附加会话）

**文件**: `packages/opencode/src/cli/cmd/tui/attach.ts`

```bash
# 附加到现有会话
opencode attach <session-id>

# 附加到指定目录的会话
opencode attach --dir /path/to/project
```

### 4.4 opencode mcp（MCP 管理）

**文件**: `packages/opencode/src/cli/cmd/mcp.ts`

```bash
# 列出 MCP 服务器
opencode mcp list

# 添加 MCP 服务器
opencode mcp add <name> <command>

# 移除 MCP 服务器
opencode mcp remove <name>

# 测试 MCP 服务器
opencode mcp test <name>
```

### 4.5 opencode acp（ACP 服务器）

**文件**: `packages/opencode/src/cli/cmd/acp.ts`

```bash
# 启动 ACP 服务器
opencode acp

# 指定传输方式
opencode acp --transport stdio
opencode acp --transport http
```

### 4.6 opencode debug（调试工具）

**文件**: `packages/opencode/src/cli/cmd/debug/index.ts`

```bash
# 调试配置
opencode debug config

# 调试文件操作
opencode debug file <path>

# 调试 LSP
opencode debug lsp

# 调试 ripgrep
opencode debug ripgrep <pattern>

# 调试技能
opencode debug skill <name>

# 调试 agent
opencode debug agent

# 显示全局路径
opencode debug paths

# 等待（用于调试）
opencode debug wait
```

### 4.7 opencode generate（代码生成）

**文件**: `packages/opencode/src/cli/cmd/generate.ts`

```bash
# 生成代码
opencode generate <prompt>

# 指定输出文件
opencode generate -o output.ts <prompt>

# 使用特定模型
opencode generate -m claude-opus-4 <prompt>
```

### 4.8 opencode serve（启动服务器）

**文件**: `packages/opencode/src/cli/cmd/serve.ts`

```bash
# 启动 API 服务器
opencode serve

# 指定端口
opencode serve -p 8080

# 允许网络访问
opencode serve --host 0.0.0.0
```

### 4.9 opencode web（Web 界面）

**文件**: `packages/opencode/src/cli/cmd/web.ts`

```bash
# 启动 Web 界面
opencode web

# 指定端口
opencode web -p 3000

# 禁用自动打开浏览器
opencode web --no-open
```

### 4.10 opencode auth（认证）

**文件**: `packages/opencode/src/cli/cmd/auth.ts`

```bash
# 登录
opencode auth login

# 登出
opencode auth logout

# 查看认证状态
opencode auth status
```

### 4.11 opencode upgrade（自升级）

**文件**: `packages/opencode/src/cli/cmd/upgrade.ts`

```bash
# 检查更新
opencode upgrade

# 升级到预览版
opencode upgrade --channel preview

# 强制重新安装
opencode upgrade --force
```

### 4.12 opencode agent（Agent 管理）

**文件**: `packages/opencode/src/cli/cmd/agent.ts`

```bash
# 列出 agents
opencode agent list

# 创建 agent
opencode agent new <name>

# 查看 agent 详情
opencode agent show <name>
```

### 4.13 opencode session（会话管理）

**文件**: `packages/opencode/src/cli/cmd/session.ts`

```bash
# 列出会话
opencode session list

# 查看会话详情
opencode session show <id>

# 删除会话
opencode session delete <id>
```

### 4.14 opencode db（数据库操作）

**文件**: `packages/opencode/src/cli/cmd/db.ts`

```bash
# 数据库迁移
opencode db migrate

# 重置数据库
opencode db reset
```

### 4.15 opencode github（GitHub 集成）

**文件**: `packages/opencode/src/cli/cmd/github.ts`

```bash
# 克隆仓库
opencode github clone <repo>

# 查看 issues
opencode github issues

# 创建 PR
opencode github pr create
```

### 4.16 opencode pr（PR 管理）

**文件**: `packages/opencode/src/cli/cmd/pr.ts`

```bash
# 查看 PR 列表
opencode pr list

# 查看 PR 详情
opencode pr show <number>

# 检出 PR
opencode pr checkout <number>
```

---

## 5. 配置与状态

### 5.1 全局路径

**文件**: `packages/opencode/src/global/index.ts`

遵循 XDG Base Directory 规范：

```typescript
Global.Path.data    // ~/.local/share/opencode
Global.Path.config  // ~/.config/opencode
Global.Path.cache   // ~/.cache/opencode
Global.Path.state   // ~/.local/state/opencode
Global.Path.logs    // ~/.local/state/opencode/logs
```

环境变量覆盖：
- `XDG_DATA_HOME`
- `XDG_CONFIG_HOME`
- `XDG_CACHE_HOME`
- `XDG_STATE_HOME`

### 5.2 安装信息

**文件**: `packages/opencode/src/installation/index.ts`

```typescript
Installation.VERSION              // 当前版本
Installation.isLocal()            // 是否本地开发
Installation.detect()             // 安装方式检测
Installation.upgrade(channel)     // 升级
```

支持安装方式：
- npm / yarn / pnpm / bun
- Homebrew
- Chocolatey
- Scoop
- curl 安装脚本

---

## 6. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| 命令不存在 | 二进制路径 | 检查 `OPENCODE_BIN_PATH` |
| AVX2 警告 | CPU 支持 | 使用兼容版本或忽略警告 |
| 数据库错误 | 迁移状态 | 运行 `opencode db migrate` |
| 日志不输出 | 日志级别 | 使用 `--print-logs` 和 `--log-level DEBUG` |
| 补全不工作 | Shell 配置 | 运行 `opencode completion` 生成脚本 |
| 升级失败 | 安装方式 | 根据安装方式手动更新 |
| MCP 连接失败 | 配置 | 检查 `~/.config/opencode/mcp.json` |
| 会话丢失 | 会话列表 | 使用 `opencode session list` 查找 |

### 6.1 诊断命令

```bash
# 查看全局路径
opencode debug paths

# 查看配置
opencode debug config

# 查看日志文件位置
ls ~/.local/state/opencode/logs/

# 生成 shell 补全
opencode completion bash > /path/to/completions/opencode
```

### 6.2 调试模式

```bash
# 启用详细日志
opencode --print-logs --log-level DEBUG run

# 调试特定功能
opencode debug lsp
opencode debug ripgrep "pattern"
```

---

## 7. 架构特点总结

- **平台二进制分发**：Node wrapper 检测平台/架构，分发对应二进制
- **yargs Command Module**：标准化命令结构（command/describe/builder/handler）
- **全局中间件**：统一日志初始化和数据库迁移
- **XDG 规范**：遵循 XDG Base Directory 管理配置和数据
- **自升级能力**：检测安装方式，支持多渠道升级
- **Shell 补全**：内置 completion 命令生成补全脚本
- **丰富调试工具**：debug 命令集提供多维度诊断
- **错误格式化**：统一错误处理和用户友好提示

---

## 8. 参考文件

| 文件 | 职责 |
|------|------|
| `packages/opencode/bin/opencode` | 平台检测 + 二进制分发 |
| `packages/opencode/src/index.ts` | yargs 配置、命令注册、中间件 |
| `packages/opencode/src/cli/cmd/cmd.ts` | Command Module 包装器 |
| `packages/opencode/src/cli/cmd/run.ts` | 默认 run 命令 |
| `packages/opencode/src/cli/cmd/mcp.ts` | MCP 命令 |
| `packages/opencode/src/cli/cmd/acp.ts` | ACP 命令 |
| `packages/opencode/src/cli/cmd/debug/index.ts` | Debug 命令组 |
| `packages/opencode/src/cli/ui.ts` | UI 工具函数 |
| `packages/opencode/src/cli/error.ts` | 错误格式化 |
| `packages/opencode/src/global/index.ts` | 全局路径管理 |
| `packages/opencode/src/installation/index.ts` | 安装信息管理 |
