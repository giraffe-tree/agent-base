# Gemini CLI Tool Call 并发机制（questions）

## 结论

Gemini CLI **未实现“多工具并行执行”**，核心调度器是“**单 active call + queue 串行执行**”。

- 项目名 + 文件路径 + 关键职责：
  - `gemini-cli` + `gemini-cli/packages/core/src/scheduler/scheduler.ts`：核心状态机，逐个出队执行 tool call。
  - `gemini-cli` + `gemini-cli/packages/core/src/core/coreToolScheduler.ts`：同样体现单活跃调用 + 队列语义（兼容层）。
  - `gemini-cli` + `gemini-cli/packages/core/src/scheduler/types.ts`：`ToolCallRequestInfo` / `ToolCall` / `CompletedToolCall` 数据结构。

## 如何实现（串行）

1. `schedule(requests)` 接收一批工具请求。  
2. 若当前有执行中请求，则入 `requestQueue`。  
3. `_startBatch()` 把请求转换为 `ToolCall` 并入内部队列。  
4. `_processQueue()` 循环处理，每次只取一个 active call。  
5. `Validating -> Scheduled -> Executing -> (Success|Error|Cancelled)`。  
6. 当前 active call 结束后，再处理下一个。

关键源码信号：

- `if (this.isProcessing || this.state.isActive) { enqueue }`
- `_processQueue()` + `_processNextItem()` 单步推进
- `firstActiveCall` 仅一个

## 流程图

```text
+--------------------+
| schedule(requests) |
+---------+----------+
          |
          v
+----------------------------------+
| isProcessing or isActive ?       |
+------------+---------------------+
             |是
             v
   +--------------------------+
   | requestQueue 入队等待     |
   +--------------------------+
             |
             |否
             v
   +--------------------------+
   | _startBatch              |
   +------------+-------------+
                |
                v
   +--------------------------+
   | state.enqueue(newCalls)  |
   +------------+-------------+
                |
                v
   +------------------------------------------+
   | _processQueue while(queue or active)     |
   +-------------------+----------------------+
                       |
                       v
            +----------------------+
            | _processNextItem     |
            +----------+-----------+
                       |
                       v
            +----------------------+
            | 存在 active call ?   |
            +------+---------------+
                   |否
                   v
      +-------------------------------+
      | dequeue 1 个 call 设为 active |
      +---------------+---------------+
                      |
                      v
               +-------------+
               | 处理 active |
               +------+------+ 
                      |
                      v
 +------------+  +--------------+  +-----------+  +-----------+  +-----------------------+
 | Validating |->| Policy/Approval |->| Scheduled |->| Executing |->| Success/Error/Cancelled |
 +------------+  +--------------+  +-----------+  +-----------+  +-----------+-----------+
                                                                           |
                                                                           v
                                                              +----------------------+
                                                              | finalize 当前 call   |
                                                              +----------+-----------+
                                                                         |
                                                                         v
                                                            (回到 _processQueue 循环)
```

## 数据格式

### 1) 请求

```ts
export interface ToolCallRequestInfo {
  callId: string;
  name: string;
  args: Record<string, unknown>;
  isClientInitiated: boolean;
  prompt_id: string;
  checkpoint?: string;
}
```

### 2) 状态机状态

```ts
export enum CoreToolCallStatus {
  Validating,
  Scheduled,
  Executing,
  AwaitingApproval,
  Success,
  Error,
  Cancelled,
}
```

### 3) 完成结果

```ts
export type CompletedToolCall =
  | SuccessfulToolCall
  | CancelledToolCall
  | ErroredToolCall;
```

## 备注

- 并发主要用于其它模块（如 MCP 连接生命周期、文件处理等 `Promise.all`），**但工具执行调度本身是串行**。
