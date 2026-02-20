# Gemini CLI Skill 执行超时机制

## 结论

Gemini CLI 采用**Scheduler 状态机驱动**的超时管理：默认 10 分钟超时通过 `MCPServerConfig` 配置，工具执行流经 `Validating → AwaitiingApproval/Scheduled → Executing → Success/Error/Cancelled` 状态机，用户可通过 `cancelAll()` 主动取消当前及排队中的工具调用。

---

## 关键代码位置

| 层级 | 文件路径 | 关键职责 |
|-----|---------|---------|
| 配置定义 | `src/mcp/config.ts` | `MCPServerConfig` 接口定义 |
| 配置定义 | `src/mcp/server.ts` | 超时参数初始化 |
| Scheduler | `src/mcp/scheduler.ts` | 工具执行状态机管理 |
| 状态管理 | `src/mcp/types.ts` | `ToolExecutionState` 枚举 |
| 取消逻辑 | `src/mcp/client.ts` | `cancelAll()` 实现 |
| 调用执行 | `src/mcp/call.ts` | 实际工具调用与超时处理 |

---

## 流程图

### Scheduler 状态流转

```
┌──────────────────────────────────────────────────────────────┐
│                    Scheduler 状态流转                         │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────────┐                                            │
│   │  用户请求    │                                            │
│   └──────┬──────┘                                            │
│          │ schedule()                                         │
│          ▼                                                   │
│   ┌─────────────┐     无需批准      ┌─────────────┐           │
│   │  Validating │──────────────────▶│  Scheduled  │           │
│   └──────┬──────┘                   └──────┬──────┘           │
│          │ 需要批准                         │                 │
│          ▼                                  │ processQueue()  │
│   ┌─────────────┐                           ▼                 │
│   │AwaitingApproval│──────────────────▶┌───────────┐          │
│   └─────────────┘   approve()          │ Executing │          │
│                                        └─────┬─────┘          │
│                                              │                │
│                    ┌─────────────────────────┼─────────┐      │
│                    │                         │         │      │
│                    ▼                         ▼         ▼      │
│              ┌─────────┐              ┌─────────┐  ┌─────────┐│
│              │ Success │              │  Error  │  │Cancelled││
│              └─────────┘              └─────────┘  └─────────┘│
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 完整超时判断流程

```
┌─────────────────────────────────────────────────────────────────┐
│                     Skill 执行超时流程                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │  用户调用工具 │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ 读取 MCPServerConfig     │                                   │
│   │ timeout: 600000 (10min) │                                   │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────┐                                   │
│   │   scheduler.schedule()  │                                   │
│   │   state: Validating     │                                   │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────┐                                   │
│   │   权限验证通过？          │                                   │
│   └──────┬────────┬─────────┘                                   │
│          │        │                                             │
│      否   │        │ 是                                          │
│          ▼        ▼                                             │
│   ┌──────────┐  ┌─────────────┐                                 │
│   │Awaiting  │  │  Scheduled  │                                 │
│   │Approval  │  └──────┬──────┘                                 │
│   └────┬─────┘         │                                        │
│        │               │                                        │
│        │ approve()     │ processQueue()                         │
│        └───────────────┘                                        │
│                        │                                        │
│                        ▼                                        │
│   ┌─────────────────────────────────────────┐                   │
│   │            Executing 状态               │                   │
│   │                                         │                   │
│   │   Promise.race([                        │                   │
│   │     callTool(),                         │                   │
│   │     timeoutPromise(600000)              │                   │
│   │   ])                                    │                   │
│   └──────────────────┬──────────────────────┘                   │
│                      │                                          │
│           ┌──────────┼──────────┐                               │
│           │          │          │                               │
│           ▼          ▼          ▼                               │
│      ┌────────┐ ┌────────┐ ┌──────────┐                        │
│      │ Success│ │ Error  │ │ Timeout  │                        │
│      └───┬────┘ └───┬────┘ └────┬─────┘                        │
│          │          │           │                               │
│          ▼          ▼           ▼                               │
│      emitResult  emitError  emitError                           │
│                              (Timeout)                          │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                     用户主动取消                          │  │
│   │                                                         │  │
│   │   cancelAll() ──▶ abortController.abort()              │  │
│   │              ──▶ scheduler.cancelAll()                 │  │
│   │              ──▶ emit 'toolCancelled'                  │  │
│   │                                                         │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 超时配置体系

### 1. 配置层

**`MCPServerConfig` 接口**（`src/mcp/config.ts:15-35`）

```typescript
export interface MCPServerConfig {
  /**
   * MCP 服务器唯一标识
   */
  readonly id: string;

  /**
   * 服务器启动命令
   */
  readonly command: string;

  /**
   * 启动参数
   */
  readonly args?: readonly string[];

  /**
   * 工具执行超时（毫秒）
   * 默认：10 分钟（600000ms）
   */
  readonly timeout?: number;

  /**
   * 是否信任此服务器（跳过权限确认）
   */
  readonly trust?: boolean;

  /**
   * 环境变量
   */
  readonly env?: Record<string, string>;
}
```

**默认超时配置**（`src/mcp/server.ts:40-60`）

```typescript
const DEFAULT_TOOL_TIMEOUT = 600000; // 10 分钟

export class MCPServer {
  private timeout: number;

  constructor(config: MCPServerConfig) {
    this.timeout = config.timeout ?? DEFAULT_TOOL_TIMEOUT;
    // ...
  }

  getToolTimeout(): number {
    return this.timeout;
  }
}
```

### 2. 执行层 - Scheduler 状态机

**`ToolExecutionState` 枚举**（`src/mcp/types.ts:20-50`）

```typescript
export enum ToolExecutionState {
  /** 等待权限验证 */
  Validating = 'validating',

  /** 等待用户批准 */
  AwaitingApproval = 'awaiting_approval',

  /** 已调度等待执行 */
  Scheduled = 'scheduled',

  /** 正在执行 */
  Executing = 'executing',

  /** 执行成功 */
  Success = 'success',

  /** 执行失败 */
  Error = 'error',

  /** 已取消 */
  Cancelled = 'cancelled',
}

export interface ToolExecution {
  id: string;
  toolName: string;
  args: unknown;
  state: ToolExecutionState;
  startTime?: number;
  endTime?: number;
  error?: Error;
}
```

**Scheduler 核心逻辑**（`src/mcp/scheduler.ts:80-150`）

```typescript
export class ToolScheduler {
  private executions: Map<string, ToolExecution> = new Map();
  private queue: string[] = [];

  /**
   * 调度工具执行
   */
  async schedule(toolName: string, args: unknown): Promise<string> {
    const id = generateId();

    const execution: ToolExecution = {
      id,
      toolName,
      args,
      state: ToolExecutionState.Validating,
    };

    this.executions.set(id, execution);

    // 检查权限
    if (!await this.validatePermission(toolName)) {
      execution.state = ToolExecutionState.AwaitingApproval;
      return id;
    }

    // 进入调度队列
    execution.state = ToolExecutionState.Scheduled;
    this.queue.push(id);

    // 触发执行
    this.processQueue();

    return id;
  }

  /**
   * 实际执行工具（带超时）
   */
  private async execute(execution: ToolExecution): Promise<void> {
    execution.state = ToolExecutionState.Executing;
    execution.startTime = Date.now();

    const timeout = this.server.getToolTimeout();

    try {
      const result = await Promise.race([
        this.callTool(execution.toolName, execution.args),
        this.createTimeoutPromise(timeout, execution.id),
      ]);

      execution.state = ToolExecutionState.Success;
      execution.endTime = Date.now();
      this.emitResult(execution.id, result);

    } catch (error) {
      if (error instanceof TimeoutError) {
        execution.state = ToolExecutionState.Error;
        execution.error = new Error(`Tool execution timed out after ${timeout}ms`);
      } else {
        execution.state = ToolExecutionState.Error;
        execution.error = error as Error;
      }
      execution.endTime = Date.now();
      this.emitError(execution.id, execution.error);
    }
  }

  private createTimeoutPromise(timeout: number, id: string): Promise<never> {
    return new Promise((_, reject) => {
      setTimeout(() => {
        reject(new TimeoutError(id));
      }, timeout);
    });
  }
}
```

### 3. 取消机制

**`cancelAll()` 实现**（`src/mcp/client.ts:200-250`）

```typescript
export class MCPClient {
  private scheduler: ToolScheduler;
  private abortController: AbortController;

  /**
   * 取消所有当前和排队中的工具调用
   */
  async cancelAll(): Promise<void> {
    // 取消正在进行的 fetch/执行
    this.abortController.abort();

    // 重置 AbortController 供下次使用
    this.abortController = new AbortController();

    // 通知 scheduler 取消所有
    const cancelledIds = this.scheduler.cancelAll();

    // 发送取消事件
    for (const id of cancelledIds) {
      this.emit('toolCancelled', { id });
    }

    console.log(`Cancelled ${cancelledIds.length} tool execution(s)`);
  }
}
```

**Scheduler 取消实现**（`src/mcp/scheduler.ts:180-220`）

```typescript
export class ToolScheduler {
  /**
   * 取消所有排队中和执行中的任务
   */
  cancelAll(): string[] {
    const cancelledIds: string[] = [];

    // 取消排队中的任务
    for (const id of this.queue) {
      const execution = this.executions.get(id);
      if (execution && execution.state === ToolExecutionState.Scheduled) {
        execution.state = ToolExecutionState.Cancelled;
        cancelledIds.push(id);
      }
    }

    // 清空队列
    this.queue = [];

    // 标记正在执行的任务为取消（实际执行会继续直到完成或超时）
    for (const [id, execution] of this.executions) {
      if (execution.state === ToolExecutionState.Executing) {
        // 设置取消标志，执行完成后会检查
        execution.state = ToolExecutionState.Cancelled;
        cancelledIds.push(id);
      }
    }

    return cancelledIds;
  }
}
```

---

## 超时后的行为

### 超时错误处理

```typescript
// 超时后生成的错误对象
interface ToolTimeoutError {
  id: string;
  toolName: string;
  timeout: number;
  message: string;
  state: ToolExecutionState.Error;
}

// 典型超时错误信息
{
  id: "exec_abc123",
  toolName: "bash",
  timeout: 600000,
  message: "Tool execution timed out after 600000ms",
  state: "error"
}
```

---

## 数据流转

```
配置文件 (.gemini/config.json)
    │
    │ { "mcpServers": [{ "id": "bash", "timeout": 300000 }] }
    ▼
MCPServerConfig 接口
    │
    ├───▶ timeout: number (默认 600000)
    ▼
MCPServer 实例
    │
    ├───▶ getToolTimeout() ──▶ 300000
    ▼
ToolScheduler
    │
    │ schedule() ──▶ ToolExecution { state: Validating }
    │
    │ validatePermission() ──▶ state: Scheduled
    │
    │ processQueue() ──▶ state: Executing
    │
    │ Promise.race()
    │   ├── callTool() ──▶ ToolResult
    │   └── timeoutPromise(300000)
    │           │
    │           ├── 超时 ──▶ TimeoutError
    │           │              │
    │           │              ▼
    │           │         state: Error
    │           │         emitError()
    │           │
    │           └── 取消 ──▶ AbortError ──▶ state: Cancelled
    │
    ▼
前端 UI 更新
    │
    ├───▶ 成功：显示结果
    ├───▶ 超时：显示 "Execution timed out after 300000ms"
    └───▶ 取消：显示 "Tool execution cancelled"
```

---

## 配置示例

**`.gemini/config.json`**

```json
{
  "mcpServers": [
    {
      "id": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"],
      "timeout": 600000,
      "trust": false
    },
    {
      "id": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "timeout": 300000,
      "trust": true
    },
    {
      "id": "postgres",
      "command": "docker",
      "args": ["run", "--rm", "-i", "mcp/postgres"],
      "timeout": 120000,
      "env": {
        "DATABASE_URL": "postgresql://localhost/mydb"
      }
    }
  ]
}
```

---

## 设计亮点

1. **状态机驱动**：清晰的 `ToolExecutionState` 枚举管理工具全生命周期
2. **队列调度**：支持多工具排队执行，可查看执行状态
3. **用户可控**：`cancelAll()` 提供主动取消能力，AbortController 底层支持
4. **权限集成**：超时配置与权限验证流程整合，超时前需先通过权限检查

---

> **版本信息**：基于 Gemini CLI 2026-02-08 版本源码
