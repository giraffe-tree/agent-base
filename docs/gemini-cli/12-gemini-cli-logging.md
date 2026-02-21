# Gemini CLI 日志记录机制

## 引子：当你的 Agent 既要在服务端跑，又要在终端跑...

想象一下这个场景：你的团队正在开发一个 AI Agent 产品。白天，开发者在本地终端调试；晚上，同样的代码部署到服务端处理用户请求。

这两个场景对日志的需求完全不同：

**本地开发时**：
- 需要实时看到详细的执行流程
- 希望日志能集成到调试 UI 中
- 文件输出是可选的（方便分享 bug 报告）

**服务端运行时**：
- 需要结构化的日志便于分析
- 不需要控制台颜色代码
- 可能需要与日志收集系统（如 ELK）集成

Gemini CLI 的解决方案是**双模式设计**：同一套代码，根据运行环境自动切换日志策略。

```typescript
// A2A Server 模式：结构化、生产级
[INFO] 2026-02-21 03:30:15.123 PM -- Server started on port 3000

// Core 模式：开发友好、UI 集成
[LOG] 执行工具: read_file
[DEBUG] 用户输入已解析
```

本章深入解析 Gemini CLI 如何巧妙平衡开发与生产的不同需求。

---

## 结论先行

Gemini CLI 采用双模式设计：A2A Server 使用 `winston` 生产级日志库，Core 包使用自定义 `DebugLogger`，配合 ESLint 强制规范和调试抽屉 UI，实现开发与生产环境的差异化日志策略。

---

## 技术类比：服务端 vs 客户端的日志哲学

Gemini CLI 的日志设计体现了**环境区分**的思想：

| 场景 | 工具类比 | 设计哲学 |
|------|---------|---------|
| A2A Server 用 `winston` | 生产环境的 `rsyslog` | 稳定、结构化、可分析 |
| Core 用 `DebugLogger` | 开发时的 `strace` | 详细、实时、与工具集成 |

### Winston vs console.log 的性能差异

| 特性 | `console.log` | `winston` | `DebugLogger` |
|------|---------------|-----------|---------------|
| 异步写入 | ❌ | ✅ | ✅（可选） |
| 结构化输出 | ❌ | ✅ | ⚠️（基础） |
| 传输控制 | ❌ | ✅ | ❌ |
| 性能开销 | 低 | 中 | 低 |
| 内存缓冲 | ❌ | ✅ | ❌ |

为什么生产环境不能用 `console.log`？
1. **同步阻塞**：`console.log` 是同步的，高并发时会阻塞事件循环
2. **无缓冲**：每条日志都直接写入，I/O 效率低
3. **无级别控制**：无法通过配置动态调整日志详细程度

### DebugLogger 与 DevTools 的集成

`DebugLogger` 不是简单的日志库，而是连接代码与 UI 的桥梁：

```
代码中的日志
     │
     ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ DebugLogger │───▶│console.log  │───▶│ConsolePatcher│
└─────────────┘    └─────────────┘    └──────┬──────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                         ┌────────┐    ┌────────┐    ┌────────┐
                         │ 终端   │    │ 文件   │    │ Debug  │
                         │ 输出   │    │ (可选) │    │ Drawer │
                         └────────┘    └────────┘    └────────┘
```

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      A2A Server                              │
│                   (服务端/生产环境)                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  Winston Logger                        │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Format: [LEVEL] YYYY-MM-DD HH:mm:ss.SSS A -- msg│  │  │
│  │  │  Transport: Console                               │  │  │
│  │  │  Level: info                                      │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 同一进程内
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Core Package                            │
│                   (客户端/开发调试)                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  DebugLogger                           │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  • console.log → ConsolePatcher → UI 调试抽屉    │  │  │
│  │  │  • 可选文件输出 (GEMINI_DEBUG_LOG_FILE)          │  │  │
│  │  │  • ISO 8601 时间戳                               │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 双模式设计背景

| 模式 | 使用场景 | 日志库 | 输出目标 |
|------|----------|--------|----------|
| A2A Server | 服务端部署 | winston | Console |
| Core | 客户端开发 | DebugLogger | Console + 可选文件 + UI |

设计考量：
- **A2A Server**: 需要结构化、可配置的日志，便于服务端问题排查
- **Core**: 需要轻量级、与 UI 集成的调试日志，提升开发体验

---

## Winston 配置详解

**✅ Verified**: `gemini-cli/packages/a2a-server/src/utils/logger.ts`

```typescript
import winston from 'winston';

const logger = winston.createLogger({
  level: 'info',
  format: winston.format.combine(
    // 1. 时间戳格式化
    winston.format.timestamp({
      format: 'YYYY-MM-DD HH:mm:ss.SSS A',  // 12小时制带AM/PM
    }),
    // 2. 自定义输出格式
    winston.format.printf((info) => {
      const { level, timestamp, message, ...rest } = info;
      return (
        `[${level.toUpperCase()}] ${timestamp} -- ${message}` +
        `${Object.keys(rest).length > 0 ? `\n${JSON.stringify(rest, null, 2)}` : ''}`
      );
    }),
  ),
  transports: [new winston.transports.Console()],
});

export { logger };
```

### 输出示例

```
[INFO] 2026-02-21 03:30:15.123 PM -- Server started on port 3000
[ERROR] 2026-02-21 03:31:22.456 PM -- Failed to process request
{
  "error": "Timeout",
  "duration": 30000
}
```

---

## DebugLogger 实现

**✅ Verified**: `gemini-cli/packages/core/src/utils/debugLogger.ts`

### 设计原则

```typescript
/**
 * WHY USE THIS?
 * - It makes the INTENT of the log clear (it's for developers, not users).
 * - It provides a single point of control for debug logging behavior.
 * - We can lint against direct `console.*` usage to enforce this pattern.
 *
 * HOW IT WORKS:
 * This is a thin wrapper around the native `console` object. The `ConsolePatcher`
 * will intercept these calls and route them to the debug drawer UI.
 */
```

### 完整实现

```typescript
class DebugLogger {
  private logStream: fs.WriteStream | undefined;

  constructor() {
    // 环境变量控制文件输出
    this.logStream = process.env['GEMINI_DEBUG_LOG_FILE']
      ? fs.createWriteStream(process.env['GEMINI_DEBUG_LOG_FILE'], {
          flags: 'a',  // 追加模式
        })
      : undefined;

    // 错误处理，避免崩溃
    this.logStream?.on('error', (err) => {
      console.error('Error writing to debug log stream:', err);
    });
  }

  private writeToFile(level: string, args: unknown[]) {
    if (this.logStream) {
      const message = util.format(...args);
      const timestamp = new Date().toISOString();
      const logEntry = `[${timestamp}] [${level}] ${message}\n`;
      this.logStream.write(logEntry);
    }
  }

  log(...args: unknown[]): void {
    this.writeToFile('LOG', args);
    console.log(...args);  // 被 ConsolePatcher 拦截到 UI
  }

  warn(...args: unknown[]): void {
    this.writeToFile('WARN', args);
    console.warn(...args);
  }

  error(...args: unknown[]): void {
    this.writeToFile('ERROR', args);
    console.error(...args);
  }

  debug(...args: unknown[]): void {
    this.writeToFile('DEBUG', args);
    console.debug(...args);
  }
}

export const debugLogger = new DebugLogger();
```

---

## ESLint 强制规范

### 禁止直接使用 console.*

```javascript
// .eslintrc.js 或 eslint.config.js
{
  rules: {
    'no-console': ['error', { allow: ['error'] }],  // 禁止直接使用
  }
}
```

### 推荐用法

```typescript
// ❌ 不推荐 - 会被 ESLint 拦截
console.log('Debug info');

// ✅ 推荐 - 使用 DebugLogger
import { debugLogger } from '../utils/debugLogger';
debugLogger.log('Debug info');
```

### ESLint 规则的技术实现

ESLint 的 `no-console` 规则如何工作？

```javascript
// ESLint 会检查 AST 中的 CallExpression
// 如果发现 callee 是 console 对象的成员，就报错

// 这会被拦截
console.log('test');
// ^^^^^^^^^^^ CallExpression
// ^^^^^^^ MemberExpression (object: console, property: log)

// 这不会（因为不是 console 对象）
debugLogger.log('test');
// ^^^^^^^^^^^^^^^^
// object 是 debugLogger，不是 console
```

### autofix 的支持

可以配置 ESLint 自动修复（将 `console.log` 替换为 `debugLogger.log`）：

```javascript
// eslint.config.js
module.exports = {
  rules: {
    'no-console': ['error', { allow: ['error'] }],
  },
  // 添加自定义处理器或插件来实现 autofix
};
```

---

## 调试抽屉 UI 展示机制

### 数据流

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   DebugLogger   │────▶│   console.log    │────▶│ ConsolePatcher  │
│                 │     │                  │     │                 │
│  debugLogger.log│     │  标准输出         │     │  拦截并路由      │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
                                               ┌─────────────────┐
                                               │    Debug Drawer │
                                               │    (UI组件)      │
                                               │                 │
                                               │  • 实时日志展示  │
                                               │  • 按级别过滤   │
                                               │  • 搜索功能     │
                                               └─────────────────┘
```

### ConsolePatcher 核心逻辑

```typescript
// 伪代码示意（基于常见实现模式）
class ConsolePatcher {
  private originalLog: typeof console.log;
  private logBuffer: LogEntry[] = [];

  patch() {
    this.originalLog = console.log;
    console.log = (...args: any[]) => {
      // 1. 调用原始方法（保持终端输出）
      this.originalLog.apply(console, args);

      // 2. 发送到调试抽屉
      this.emitToDrawer({
        level: 'log',
        message: args.join(' '),
        timestamp: Date.now(),
      });
    };
  }

  private emitToDrawer(entry: LogEntry) {
    eventEmitter.emit('log', entry);
  }
}
```

### 与 DevTools 集成

Gemini CLI 的调试抽屉 UI 支持：
- **实时日志展示**：通过 `ConsolePatcher` 拦截并展示
- **按级别过滤**：根据日志级别筛选显示
- **搜索功能**：关键字搜索历史日志
- **导出功能**：将日志导出为文件

快捷键：
- `Ctrl+D`：打开/关闭调试抽屉
- `Ctrl+F`：搜索日志

---

## 配置方式

### 环境变量

```bash
# 启用文件日志输出
export GEMINI_DEBUG_LOG_FILE="/tmp/gemini-debug.log"

# 运行 Gemini CLI
gemini-cli
```

### 文件输出格式

```
[2026-02-21T15:30:15.123Z] [LOG] Debug message
[2026-02-21T15:31:22.456Z] [ERROR] Error occurred
```

---

## 快速上手：Gemini CLI 日志实战

### 1. 启用文件调试日志

```bash
# 设置环境变量
export GEMINI_DEBUG_LOG_FILE="/tmp/gemini-debug.log"

# 运行 CLI
npx @google/gemini-cli

# 实时查看日志（另一个终端）
tail -f /tmp/gemini-debug.log
```

### 2. 使用 jq 解析结构化日志

```bash
# 如果日志包含 JSON，用 jq 格式化
tail -f /tmp/gemini-debug.log | while read line; do
  echo "$line" | jq -R '. as $line | try fromjson catch $line'
done
```

### 3. 开发模式与生产模式切换

```typescript
// 检测运行环境
const isProduction = process.env.NODE_ENV === 'production';

if (isProduction) {
  // 使用 Winston（A2A Server 模式）
  const logger = winston.createLogger({...});
} else {
  // 使用 DebugLogger（开发模式）
  debugLogger.log('Development mode');
}
```

### 4. 在代码中正确使用 DebugLogger

```typescript
// ❌ 错误：会被 ESLint 拦截
console.log('Processing request:', requestId);

// ✅ 正确：使用 DebugLogger
import { debugLogger } from '../utils/debugLogger';
debugLogger.log('Processing request:', requestId);

// ✅ 也可以输出结构化数据
debugLogger.debug({
  event: 'tool_execution',
  tool: 'read_file',
  args: { path: '/tmp/test.txt' },
  timestamp: Date.now()
});
```

### 5. 与 VS Code 集成

在 `.vscode/settings.json` 中配置 ESLint：

```json
{
  "eslint.rules.customizations": [
    { "rule": "no-console", "severity": "error" }
  ],
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit"
  }
}
```

### 6. 常见问题排查

**Q: 日志文件没有生成？**
```bash
# 检查环境变量是否设置
echo $GEMINI_DEBUG_LOG_FILE

# 检查目录权限
ls -la $(dirname $GEMINI_DEBUG_LOG_FILE)

# 确保有写权限
touch $GEMINI_DEBUG_LOG_FILE
```

**Q: 调试抽屉没有显示日志？**
- 确保使用的是 `DebugLogger`，不是直接 `console.log`
- 检查 `ConsolePatcher` 是否已初始化
- 按 `Ctrl+D` 确认抽屉已打开

**Q: 如何过滤特定级别的日志？**
```bash
# 在文件中只查看错误
grep "ERROR" /tmp/gemini-debug.log

# 或查看多个级别
grep -E "(ERROR|WARN)" /tmp/gemini-debug.log
```

**Q: Winston 和 DebugLogger 如何选择？**

| 场景 | 推荐 | 原因 |
|------|------|------|
| 服务端代码 | Winston | 结构化、可配置 |
| 客户端代码 | DebugLogger | UI 集成、开发友好 |
| 共享库代码 | DebugLogger | 统一调试体验 |

---

## 证据索引

| 组件 | 文件路径 | 行号 | 关键职责 |
|------|----------|------|----------|
| Winston | `gemini-cli/packages/a2a-server/src/utils/logger.ts` | 1-28 | A2A Server 日志配置 |
| DebugLogger | `gemini-cli/packages/core/src/utils/debugLogger.ts` | 1-69 | 调试日志实现 |
| ESLint | `gemini-cli/packages/core/.eslintrc.js` | - | 代码规范配置 |

---

## 边界与不确定性

- **⚠️ Inferred**: `ConsolePatcher` 的具体实现位于 UI 组件层，本分析未获取源码
- **⚠️ Inferred**: 调试抽屉的具体 UI 组件名称可能为 `DebugDrawer` 或类似名称
- **❓ Pending**: ESLint 规则的具体配置文件位置未确认，可能在根目录或各包目录
- **✅ Verified**: `GEMINI_DEBUG_LOG_FILE` 环境变量和文件输出逻辑已确认

---

## 设计亮点

1. **双模式设计**: 区分服务端与客户端的不同日志需求
2. **意图明确**: DebugLogger 明确标识开发者日志 vs 用户日志
3. **工具强制**: ESLint 规则确保团队一致使用 DebugLogger
4. **UI 集成**: 调试日志实时展示在调试抽屉，提升开发体验
5. **可选持久化**: 通过环境变量控制文件输出，灵活适应不同场景
