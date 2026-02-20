# OpenCode Skill 执行超时机制

## 结论

OpenCode 采用**动态超时控制**策略：Bash 工具通过 `timeout` 参数（毫秒）控制执行时长，MCP 工具支持 `resetTimeoutOnProgress` 机制（有进度时自动重置超时），超时后通过 Zod schema 验证错误并返回给 LLM。

---

## 关键代码位置

| 层级 | 文件路径 | 关键职责 |
|-----|---------|---------|
| Bash 工具定义 | `src/tools/bash.ts` | Bash 工具参数定义与执行 |
| MCP 客户端 | `src/mcp/client.ts` | MCP 工具调用与超时控制 |
| 工具执行 | `src/tools/executor.ts` | 工具执行统一入口 |
| 错误处理 | `src/tools/errors.ts` | 超时错误类型定义 |
| Agent 循环 | `src/agent/loop.ts` | 工具结果处理 |

---

## 流程图

### 完整超时判断流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    Skill 执行超时流程                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │  LLM 工具调用 │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │   判断工具类型           │                                   │
│   └──────┬──────────┬───────┘                                   │
│          │          │                                           │
│     Bash │          │ MCP                                       │
│          ▼          ▼                                           │
│   ┌──────────┐ ┌──────────────────┐                            │
│   │参数解析   │ │ 参数解析          │                            │
│   │timeout:  │ │ timeout: 600000  │                            │
│   │300000    │ │ resetTimeoutOn   │                            │
│   │(5min)    │ │ Progress: true   │                            │
│   └────┬─────┘ └──────┬───────────┘                            │
│        │              │                                         │
│        ▼              ▼                                         │
│   ┌─────────────────────────────────────────┐                   │
│   │         本地 Bash 执行 (execa)           │                   │
│   │                                         │                   │
│   │   execa(command, {                      │                   │
│   │     timeout: 300000,                    │                   │
│   │     shell: true                         │                   │
│   │   })                                    │                   │
│   └──────────────────┬──────────────────────┘                   │
│                      │                                          │
│       ┌──────────────┴──────────────────┐                       │
│       │                                 │                       │
│       ▼                                 ▼                       │
│ ┌─────────────┐              ┌─────────────────────┐            │
│ │   成功完成   │              │   ExecaError        │            │
│ │             │              │   error.timedOut    │            │
│ │ 返回 output  │              │   === true          │            │
│ └──────┬──────┘              └──────────┬──────────┘            │
│        │                                │                       │
│        │                                ▼                       │
│        │                       ┌─────────────────────┐          │
│        │                       │ 构造 timeout 错误   │          │
│        │                       │ type: "timeout"     │          │
│        │                       └──────────┬──────────┘          │
│        │                                  │                     │
│        └──────────────────┬───────────────┘                     │
│                           │                                     │
│                           ▼                                     │
│              ┌────────────────────────┐                        │
│              │    ToolResult 包装      │                        │
│              │  success: true/false   │                        │
│              │  + executionTime       │                        │
│              └───────────┬────────────┘                        │
│                          │                                      │
│                          ▼                                      │
│              ┌────────────────────────┐                        │
│              │   AgentLoop 处理        │                        │
│              │  添加到对话上下文        │                        │
│              └────────────────────────┘                        │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │              MCP 动态超时重置机制                         │  │
│   │                                                         │  │
│   │   执行中 ──▶ 收到 progress 通知 ──▶ 重置 timeout 计时器  │  │
│   │                                                         │  │
│   │   适用于长时间运行的 MCP 工具（如文件索引、代码分析）      │  │
│   │                                                         │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### MCP 动态超时重置流程

```
┌─────────────────────────────────────────────────────────────────┐
│                 resetTimeoutOnProgress 机制                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   开始调用 MCP 工具                                               │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────┐                                       │
│   │ 设置初始超时 10 分钟  │                                       │
│   │ setTimeout(600000)  │                                       │
│   └──────────┬──────────┘                                       │
│              │                                                   │
│              ▼                                                   │
│   ┌─────────────────────┐                                       │
│   │    工具执行中...     │ ◀────────────────────┐                │
│   └──────────┬──────────┘                      │                │
│              │                                 │                │
│              │ 收到 notifications/progress     │                │
│              │ {progress: 50, total: 100}      │                │
│              │                                 │                │
│              ▼                                 │                │
│   ┌─────────────────────┐                     │                │
│   │  clearTimeout()     │                     │                │
│   │  清除旧计时器        │                     │                │
│   └──────────┬──────────┘                     │                │
│              │                                 │                │
│              ▼                                 │                │
│   ┌─────────────────────┐                     │                │
│   │  setTimeout(600000) │                     │                │
│   │  重新设置 10 分钟    │─────────────────────┘                │
│   └─────────────────────┘    循环直到完成                        │
│                                                                 │
│   效果：工具只要持续报告进度，就不会因总耗时长而被误判超时          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 超时配置体系

### 1. Bash 工具超时

**工具参数 Schema**（`src/tools/bash.ts:20-50`）

```typescript
import { z } from 'zod';

export const BashToolSchema = z.object({
  command: z.string()
    .describe('Shell command to execute'),

  timeout: z.number()
    .optional()
    .describe('Timeout in milliseconds'),

  workdir: z.string()
    .optional()
    .describe('Working directory for command execution'),
});

export type BashToolParams = z.infer<typeof BashToolSchema>;

// 默认超时：5 分钟
const DEFAULT_TIMEOUT = 5 * 60 * 1000; // 300000ms
```

**Bash 工具执行**（`src/tools/bash.ts:55-110`）

```typescript
export class BashTool implements Tool {
  name = 'bash';
  description = 'Execute shell commands with timeout control';
  parameters = BashToolSchema;

  async execute(params: BashToolParams): Promise<ToolResult> {
    // 解析超时参数（毫秒）
    const timeout = params.timeout ?? DEFAULT_TIMEOUT;

    try {
      const result = await execa(params.command, {
        cwd: params.workdir,
        timeout,  // execa 内置超时支持
        shell: true,
        all: true,  // 合并 stdout + stderr
      });

      return {
        success: true,
        output: result.all || result.stdout,
        exitCode: result.exitCode,
      };

    } catch (error) {
      if (error instanceof ExecaError) {
        // 超时错误判断
        if (error.timedOut) {
          return {
            success: false,
            error: {
              type: 'timeout',
              message: `Command timed out after ${timeout}ms`,
              command: params.command,
              timeout,
            },
          };
        }

        // 其他执行错误
        return {
          success: false,
          error: {
            type: 'execution_error',
            message: error.message,
            exitCode: error.exitCode,
          },
        };
      }

      throw error;
    }
  }
}
```

### 2. MCP 工具超时（动态重置机制）

**MCP 客户端调用**（`src/mcp/client.ts:80-140`）

```typescript
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { CallToolResultSchema } from '@modelcontextprotocol/sdk/types.js';

export class MCPClient {
  private client: Client;
  private defaultTimeout: number = 600000; // 10 分钟默认

  /**
   * 调用 MCP 工具，支持动态超时重置
   */
  async callTool(
    mcpTool: MCPTool,
    args: Record<string, unknown>,
    options: CallOptions = {}
  ): Promise<ToolResult> {
    const timeout = options.timeout ?? this.defaultTimeout;

    try {
      // OpenCode 特色：resetTimeoutOnProgress
      // 当工具报告进度时，自动重置超时计时器
      const result = await this.client.callTool(
        {
          name: mcpTool.name,
          arguments: args,
        },
        CallToolResultSchema,
        {
          timeout,
          resetTimeoutOnProgress: true,  // ★ 有进度时重置超时
        }
      );

      return {
        success: true,
        output: result.content,
      };

    } catch (error) {
      // 超时错误处理
      if (error.code === 'TIMEOUT') {
        return {
          success: false,
          error: {
            type: 'mcp_timeout',
            message: `MCP tool '${mcpTool.name}' timed out after ${timeout}ms`,
            tool: mcpTool.name,
            timeout,
          },
        };
      }

      throw error;
    }
  }
}

interface CallOptions {
  timeout?: number;
  resetTimeoutOnProgress?: boolean;
}
```

**进度通知处理**（`src/mcp/client.ts:145-180`）

```typescript
/**
 * 处理 MCP 服务器的进度通知
 * 当收到进度更新时，重置超时计时器
 */
private setupProgressHandler(): void {
  this.client.onNotification(
    'notifications/progress',
    (notification) => {
      const { progress, total } = notification.params;

      // 触发进度事件
      this.emit('progress', {
        tool: this.currentTool,
        progress,
        total,
        percentage: total ? (progress / total) * 100 : undefined,
      });

      // ★ 重置超时计时器
      if (this.currentTimeoutHandle) {
        clearTimeout(this.currentTimeoutHandle);
        this.currentTimeoutHandle = setTimeout(
          () => this.handleTimeout(),
          this.currentTimeout
        );
      }
    }
  );
}
```

### 3. 工具执行器统一处理

**执行器入口**（`src/tools/executor.ts:40-90`）

```typescript
export class ToolExecutor {
  private tools: Map<string, Tool> = new Map();
  private mcpClient: MCPClient;

  /**
   * 统一执行工具（本地或 MCP）
   */
  async execute(
    toolName: string,
    args: Record<string, unknown>
  ): Promise<ToolResult> {
    const startTime = Date.now();

    try {
      // 检查是否为 MCP 工具
      if (this.isMCPTool(toolName)) {
        const mcpTool = this.getMCPTool(toolName);
        const result = await this.mcpClient.callTool(mcpTool, args);
        return this.wrapResult(result, startTime);
      }

      // 本地工具
      const tool = this.tools.get(toolName);
      if (!tool) {
        throw new ToolNotFoundError(toolName);
      }

      // Zod 参数验证
      const validated = tool.parameters.parse(args);
      const result = await tool.execute(validated);
      return this.wrapResult(result, startTime);

    } catch (error) {
      return this.handleError(error, toolName, startTime);
    }
  }

  private wrapResult(result: ToolResult, startTime: number): ToolResult {
    return {
      ...result,
      metadata: {
        ...result.metadata,
        executionTime: Date.now() - startTime,
      },
    };
  }
}
```

---

## 超时后的行为

### 错误类型定义

**超时错误结构**（`src/tools/errors.ts:15-50`）

```typescript
export interface ToolTimeoutError {
  type: 'timeout' | 'mcp_timeout';
  message: string;

  // Bash 工具特有
  command?: string;

  // MCP 工具特有
  tool?: string;

  // 超时配置
  timeout: number;

  // 执行信息
  executionTime?: number;

  // 建议
  suggestion?: string;
}

// 错误工厂函数
export function createTimeoutError(
  tool: string,
  timeout: number,
  command?: string
): ToolTimeoutError {
  const baseError: ToolTimeoutError = {
    type: tool.startsWith('mcp_') ? 'mcp_timeout' : 'timeout',
    message: `${tool} timed out after ${timeout}ms`,
    timeout,
    executionTime: timeout,  // 实际执行了多久（约等于超时值）
  };

  if (command) {
    baseError.command = command;
    baseError.suggestion = `Consider increasing timeout or breaking '${command}' into smaller steps`;
  }

  return baseError;
}
```

### Agent 循环中的处理

**结果处理**（`src/agent/loop.ts:120-180`）

```typescript
export class AgentLoop {
  async runTurn(userInput: string): Promise<void> {
    // ... 获取 LLM 响应 ...

    for (const toolCall of toolCalls) {
      const result = await this.executor.execute(
        toolCall.name,
        toolCall.arguments
      );

      if (!result.success) {
        const error = result.error;

        // 超时错误特殊处理
        if (error.type === 'timeout' || error.type === 'mcp_timeout') {
          // 向 LLM 报告超时，包含建议
          this.addToContext({
            role: 'tool',
            name: toolCall.name,
            content: JSON.stringify({
              error: error.message,
              suggestion: error.suggestion || `Try increasing timeout (current: ${error.timeout}ms)`,
            }),
          });

          // 如果是 MCP 超时且支持进度，提示用户
          if (error.type === 'mcp_timeout') {
            console.log(`⚠️  MCP tool timed out. It may need more time or is stuck.`);
          }
        } else {
          // 其他错误
          this.addToContext({
            role: 'tool',
            name: toolCall.name,
            content: JSON.stringify({ error: error.message }),
          });
        }
      } else {
        // 成功结果
        this.addToContext({
          role: 'tool',
          name: toolCall.name,
          content: result.output,
        });
      }
    }
  }
}
```

---

## 数据流转

```
LLM 工具调用
    │
    │ { "tool": "bash", "args": { "command": "sleep 300", "timeout": 60000 } }
    ▼
ToolExecutor.execute()
    │
    ├───▶ 判断：本地工具 (bash)
    │
    ▼
BashTool.execute()
    │
    ├───▶ Zod 验证参数 { command: string, timeout?: number }
    │
    ├───▶ timeout = args.timeout ?? DEFAULT_TIMEOUT (300000)
    │       = 60000 (用户指定)
    │
    ▼
execa(command, { timeout: 60000, shell: true })
    │
    ├───▶ 子进程执行 sleep 300
    │
    ├───▶ setTimeout(60000) 等待
    │           │
    │           ├── 60s 内完成 ──▶ 返回 { stdout, stderr, exitCode }
    │           │
    │           └── 60s 超时 ───▶ throw ExecaError { timedOut: true }
    │
    ▼
异常捕获
    │
    ├── 无异常 ──▶ ToolResult { success: true, output, exitCode }
    │
    └── ExecaError ──▶ 判断 error.timedOut
              │
              ├── true ──▶ ToolResult {
              │              success: false,
              │              error: {
              │                type: "timeout",
              │                message: "Command timed out after 60000ms",
              │                timeout: 60000,
              │                command: "sleep 300"
              │              }
              │            }
              │
              └── false ──▶ 其他错误处理
    ▼
AgentLoop
    │
    ├───▶ 成功：添加到 context，继续对话
    │
    └───▶ 超时：添加错误信息 + 建议到 context
              │
              └── "Try increasing timeout (current: 60000ms)"
```

---

## 配置示例

**`opencoder.json` 配置**

```json
{
  "tools": {
    "bash": {
      "defaultTimeout": 300000,
      "maxTimeout": 1800000
    },
    "mcp": {
      "defaultTimeout": 600000,
      "resetTimeoutOnProgress": true
    }
  },
  "mcpServers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"],
      "timeout": 600000,
      "resetTimeoutOnProgress": true
    },
    {
      "name": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "timeout": 300000
    }
  ]
}
```

**工具调用示例（带超时）**

```typescript
// LLM 生成的工具调用
{
  "tool": "bash",
  "arguments": {
    "command": "npm run build:production",
    "timeout": 300000,  // 5 分钟
    "workdir": "/home/user/project"
  }
}

// MCP 工具调用（自动启用 resetTimeoutOnProgress）
{
  "tool": "mcp_filesystem__index",
  "arguments": {
    "path": "/home/user/large-project"
    // timeout 由 MCP 客户端配置决定
    // resetTimeoutOnProgress: true 自动启用
  }
}
```

---

## 设计亮点

1. **毫秒级精度**：timeout 参数使用毫秒（vs 其他项目的秒级），更精细控制
2. **动态超时重置**：`resetTimeoutOnProgress` 避免长任务被误判超时
3. **Zod 验证**：强类型 Schema 确保参数合法性
4. **execa 集成**：利用成熟的 Node.js 库处理超时和子进程
5. **智能建议**：超时错误包含修复建议，帮助 LLM 自我纠正

---

## MCP 动态超时 vs 固定超时

| 场景 | 固定超时 | 动态超时 (resetTimeoutOnProgress) |
|-----|---------|----------------------------------|
| 快速命令 (ls, cat) | ✅ 适合 | ✅ 适合 |
| 编译构建 | ✅ 适合 | ✅ 适合 |
| 文件索引 | ❌ 容易误判 | ✅ 持续重置，不超时 |
| 代码分析 | ❌ 容易误判 | ✅ 适合 |
| 网络请求 | ⚠️ 依赖网络 | ⚠️ 需配合 progress 上报 |

---

> **版本信息**：基于 OpenCode 2026-02-08 版本源码
