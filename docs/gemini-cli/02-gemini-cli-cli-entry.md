# CLI Entry（gemini-cli）

本文基于 `packages/cli/` 源码，解释 gemini-cli 的命令行接口设计、参数解析机制和命令分发流程。

---

## 1. 先看全局（流程图）

### 1.1 CLI 架构（Command 模式）

```text
┌─────────────────────────────────────────────────────────────────┐
│  ENTRY: gemini [options] [command]                              │
│  ┌─────────────────┐                                            │
│  │ packages/cli/   │ ◄──── 入口文件                            │
│  │   index.ts      │                                            │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CORE: packages/cli/src/gemini.tsx                              │
│  ┌────────────────────────────────────────┐                     │
│  │ main()                                 │                     │
│  │  ├── parseArguments()                  │ ──► yargs 解析      │
│  │  ├── loadCliConfig()                   │ ──► 加载配置        │
│  │  └── 模式选择                           │                     │
│  │       ├── Interactive ──► startInteractiveUI()              │
│  │       │                    └── React/Ink TUI                 │
│  │       └── Non-Interactive ──► runNonInteractive()            │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  COMMANDS: packages/cli/src/commands/                           │
│  ┌─────────┐ ┌────────────┐ ┌─────────┐ ┌─────────┐            │
│  │ mcp.ts  │ │extensions │ │skills   │ │hooks    │            │
│  │         │ │.tsx       │ │.tsx     │ │.tsx     │            │
│  ├─────────┤ ├────────────┤ ├─────────┤ ├─────────┤            │
│  │ mcp add │ │install     │ │list     │ │migrate  │            │
│  │ mcp rm  │ │uninstall   │ │enable   │ │         │            │
│  │ mcp ls  │ │update      │ │disable  │ │         │            │
│  │ ...     │ │...         │ │...      │ │         │            │
│  └─────────┘ └────────────┘ └─────────┘ └─────────┘            │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块  ──┤ 流程步骤  ▼ 执行流向
```

### 1.2 命令分发流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│ [A] 主流程分支 —— 交互式 vs 非交互式                              │
└─────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │  gemini [args]  │
                    └────────┬────────┘
                             │
            ┌────────────────┴────────────────┐
            │ --prompt 或管道输入?              │
            └───────────────┬─────────────────┘
                            │
           ┌────────────────┼────────────────┐
           ▼ Yes                               ▼ No
  ┌─────────────────────┐           ┌─────────────────────┐
  │  非交互式模式        │           │  交互式 TUI 模式     │
  ├─────────────────────┤           ├─────────────────────┤
  │ runNonInteractive() │           │ startInteractiveUI()│
  │                     │           │                     │
  │ • 单轮执行          │           │ • React/Ink 渲染    │
  │ • 输出后退出        │           │ • 实时交互          │
  │ • 支持 JSON 输出    │           │ • 支持斜杠命令      │
  └─────────────────────┘           └─────────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [B] 子命令注册流程                                               │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ yargs()         │
    │  .command()     │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ defer() 包装    │ ◄── 延迟执行，先检查 admin 设置
    │                 │
    │ argv['isCommand']
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 子命令处理      │
    │ ├─ mcp add      │
    │ ├─ mcp remove   │
    │ ├─ mcp list     │
    │ └─ ...          │
    └─────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [C] 错误处理流程                                                 │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ 未捕获异常       │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ uncaughtException│
    │                 │
    │ Windows node-pty │
    │ 特殊处理        │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ FatalError?     │
    ├─────────────────┤
    │ 是 → 格式化输出  │
    │ 否 → 打印堆栈    │
    └─────────────────┘

图例: 交互式模式使用 React/Ink 构建 TUI
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道两种运行模式和主要命令）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道命令结构和配置机制）。
- **10 分钟版**：通读 `3~8`（能添加新命令和修改参数解析）。

### 2.1 一句话定义

gemini-cli 采用「**双模式架构 + yargs 命令系统**」设计：交互模式用 React/Ink 构建 TUI，非交互模式直接执行命令；子命令使用 yargs 的 Command Module 模式组织。

---

## 3. 核心组件

### 3.1 入口文件

**文件**: `packages/cli/index.ts`

```typescript
#!/usr/bin/env node
import { main } from './src/gemini.js';
import { FatalError, writeToStderr } from '@google/gemini-cli-core';
import { runExitCleanup } from './src/utils/cleanup.js';

// 全局异常处理
process.on('uncaughtException', (error) => {
  // Windows node-pty 特殊处理
  if (process.platform === 'win32' &&
      error.message === 'Cannot resize a pty that has already exited') {
    return;  // 忽略已知 race condition
  }
  // ... 其他错误处理
});

main().catch(async (error) => {
  await runExitCleanup();
  if (error instanceof FatalError) {
    process.exit(error.exitCode);
  }
  process.exit(1);
});
```

### 3.2 参数解析配置

**文件**: `packages/cli/src/config/config.ts`

核心参数：

```typescript
// 运行模式
--prompt, -p          // 非交互式执行后退出
--prompt-interactive, -i  // 执行后继续交互
--model, -m           // 指定模型
--debug, -d           // 调试模式
--yolo, -y            // 自动接受所有操作
--approval-mode       // 审批模式 (default|auto_edit|yolo|plan)

// 扩展与配置
--extensions, -e      // 指定扩展列表
--resume, -r          // 恢复会话
--list-sessions       // 列出会话
--output-format, -o   // 输出格式 (text|json|stream-json)
--sandbox, -s         // 沙箱运行
```

### 3.3 Command Module 模式

**文件示例**: `packages/cli/src/commands/mcp.ts`

```typescript
export const mcpCommand: CommandModule = {
  command: 'mcp',
  describe: 'Manage MCP servers',
  builder: (yargs: Argv) =>
    yargs
      .middleware((argv) => {
        initializeOutputListenersAndFlush();
        argv['isCommand'] = true;
      })
      .command(defer(addCommand, 'mcp'))
      .command(defer(removeCommand, 'mcp'))
      .command(defer(listCommand, 'mcp'))
      .command(defer(enableCommand, 'mcp'))
      .command(defer(disableCommand, 'mcp'))
      .demandCommand(1, 'You need at least one command before continuing.')
      .version(false),
  handler: () => {
    // 有子命令时不执行
  },
};
```

### 3.4 延迟执行包装器

**文件**: `packages/cli/src/deferred.ts`

```typescript
export function defer<T>(
  handler: (args: ArgumentsCamelCase<T>) => void | Promise<void>,
  commandName: string,
): (args: ArgumentsCamelCase<T>) => Promise<void> {
  return async (args: ArgumentsCamelCase<T>): Promise<void> => {
    // 检查 admin 设置
    await checkAdminSettingsBeforeRunningCommands(commandName);
    return handler(args);
  };
}
```

作用：在子命令执行前统一检查管理员设置。

---

## 4. 命令详解

### 4.1 mcp（MCP 服务器管理）

**文件**: `packages/cli/src/commands/mcp.ts`

子命令：

```bash
gemini mcp add <name> <command> [args...]     # 添加 MCP 服务器
gemini mcp remove <name>                       # 移除 MCP 服务器
gemini mcp list                                # 列出所有服务器
gemini mcp enable <name>                       # 启用服务器
gemini mcp disable <name>                      # 禁用服务器
```

### 4.2 extensions（扩展管理）

**文件**: `packages/cli/src/commands/extensions.tsx`

子命令：

```bash
gemini extensions install <source>     # 安装扩展
gemini extensions uninstall <name>     # 卸载扩展
gemini extensions list                 # 列出扩展
gemini extensions update [name]        # 更新扩展
gemini extensions disable <name>       # 禁用扩展
gemini extensions enable <name>        # 启用扩展
gemini extensions link <path>          # 链接本地扩展
gemini extensions new <name>           # 创建新扩展
gemini extensions validate <path>      # 验证扩展
gemini extensions configure <name>     # 配置扩展
```

### 4.3 skills（技能管理）

**文件**: `packages/cli/src/commands/skills.tsx`

```bash
gemini skills list                     # 列出技能
gemini skills enable <name>            # 启用技能
gemini skills disable <name>           # 禁用技能
gemini skills install <source>         # 安装技能
gemini skills link <path>              # 链接本地技能
gemini skills uninstall <name>         # 卸载技能
```

### 4.4 hooks（钩子管理）

**文件**: `packages/cli/src/commands/hooks.tsx`

```bash
gemini hooks migrate                   # 迁移钩子
```

### 4.5 默认模式（chat）

无子命令时进入主 chat 模式：

```bash
# 交互式 TUI
gemini

# 非交互式，单条命令
gemini --prompt "解释这段代码"

# 非交互式，从管道读取
echo "问题" | gemini

# 指定模型
gemini --model gemini-2.0-pro
```

---

## 5. 配置管理

### 5.1 配置加载

**文件**: `packages/cli/src/config/config.ts`

配置层级：
1. 命令行参数（最高优先级）
2. 项目本地配置
3. 用户全局配置
4. 默认值

### 5.2 设置管理

**文件**: `packages/cli/src/config/settings.ts`

- 用户设置存储
- 扩展配置
- MCP 服务器配置

---

## 6. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| TUI 不渲染 | 终端兼容性 | 检查终端是否支持 TTY |
| 子命令不识别 | yargs 配置 | 确认命令名拼写 |
| 扩展加载失败 | 扩展配置 | 运行 `gemini extensions validate` |
| Windows 下异常 | node-pty | 已知问题，已自动忽略部分错误 |
| 输出格式异常 | output-format | 检查 `--output-format` 参数 |

### 6.1 调试模式

```bash
# 启用调试输出
gemini --debug

# 非交互式调试
gemini --debug --prompt "测试命令"
```

### 6.2 常用诊断命令

```bash
# 列出会话
gemini --list-sessions

# 恢复特定会话
gemini --resume <session-id>

# 查看扩展状态
gemini extensions list

# 查看 MCP 服务器
gemini mcp list
```

---

## 7. 架构特点总结

- **双模式设计**：交互式 TUI（React/Ink）vs 非交互式（CLI）
- **yargs 命令系统**：Command Module 模式组织子命令
- **延迟执行**：`defer()` 包装器统一前置检查
- **中间件模式**：yargs middleware 处理通用逻辑
- **全局异常处理**：统一捕获和格式化错误
- **平台适配**：Windows node-pty 特殊处理
- **配置分层**：命令行 > 项目 > 用户 > 默认

---

## 8. 参考文件

| 文件 | 职责 |
|------|------|
| `packages/cli/index.ts` | 入口文件、全局异常处理 |
| `packages/cli/src/gemini.tsx` | 主逻辑、模式选择 |
| `packages/cli/src/config/config.ts` | 参数解析、配置加载 |
| `packages/cli/src/config/settings.ts` | 设置管理 |
| `packages/cli/src/commands/mcp.ts` | MCP 命令 |
| `packages/cli/src/commands/extensions.tsx` | 扩展命令 |
| `packages/cli/src/commands/skills.tsx` | 技能命令 |
| `packages/cli/src/commands/hooks.tsx` | 钩子命令 |
| `packages/cli/src/deferred.ts` | 延迟执行包装器 |
| `packages/cli/src/utils/cleanup.ts` | 退出清理 |
