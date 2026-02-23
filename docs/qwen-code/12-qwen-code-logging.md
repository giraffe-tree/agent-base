# 日志记录机制（Qwen Code）

本文分析 Qwen Code 的日志记录机制，包括调试日志、遥测和错误报告。

---

## 1. 先看全局（流程图）

### 1.1 日志系统架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      日志系统架构                                    │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Debug Logger (调试日志)                                        │  │
│  │  (packages/core/src/utils/debugLogger.ts)                      │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ createDebugLogger('CATEGORY')                            │  │  │
│  │  │   ├── DEBUG env var 控制                                 │  │  │
│  │  │   └── 写入 ~/.qwen/tmp/<project>/debug.log              │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  UI Telemetry (UI 遥测)                                        │  │
│  │  (packages/core/src/telemetry/uiTelemetry.ts)                  │  │
│  │  - token 使用量统计                                            │  │
│  │  - 会话统计                                                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Chat Recording (聊天记录)                                     │  │
│  │  (packages/core/src/services/chatRecordingService.ts)          │  │
│  │  - JSONL 格式                                                  │  │
│  │  - 会话持久化                                                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Error Reporting (错误报告)                                    │  │
│  │  (packages/core/src/utils/errorReporting.ts)                   │  │
│  │  - 自动错误上报                                                │  │
│  │  - 上下文收集                                                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件

### 2.1 Debug Logger

✅ **Verified**: `qwen-code/packages/core/src/utils/debugLogger.ts`

```typescript
export function createDebugLogger(category: string) {
  return {
    debug: (message: string, ...args: unknown[]) => {
      if (process.env['DEBUG']) {
        const logLine = `[${new Date().toISOString()}] [${category}] ${message} ${args.map(String).join(' ')}`;
        writeToDebugLog(logLine);
      }
    },
    warn: (message: string, ...args: unknown[]) => {
      if (process.env['DEBUG']) {
        const logLine = `[${new Date().toISOString()}] [${category}] [WARN] ${message}`;
        writeToDebugLog(logLine);
      }
    },
    error: (message: string, ...args: unknown[]) => {
      // 错误总是记录
      const logLine = `[${new Date().toISOString()}] [${category}] [ERROR] ${message}`;
      writeToDebugLog(logLine);
    },
  };
}

function writeToDebugLog(line: string): void {
  const debugLogPath = Storage.getDebugLogPath();
  fs.appendFileSync(debugLogPath, line + '\n');
}
```

### 2.2 UI Telemetry

✅ **Verified**: `qwen-code/packages/core/src/telemetry/uiTelemetry.ts`

```typescript
export class UITelemetryService {
  private tokenCounts: TokenCount[] = [];
  private lastPromptTokenCount = 0;

  recordTokenCount(count: TokenCount): void {
    this.tokenCounts.push(count);
    this.lastPromptTokenCount = count.promptTokens;
  }

  getLastPromptTokenCount(): number {
    return this.lastPromptTokenCount;
  }

  getTotalTokenUsage(): number {
    return this.tokenCounts.reduce((sum, c) => sum + c.totalTokens, 0);
  }
}

export const uiTelemetryService = new UITelemetryService();
```

### 2.3 Chat Recording

✅ **Verified**: `qwen-code/packages/core/src/services/chatRecordingService.ts`

```typescript
export class ChatRecordingService {
  private stream: fs.WriteStream | null = null;

  recordUserMessage(content: Content, prompt_id: string): string {
    const record: ChatRecord = {
      type: 'user',
      uuid: generateUuid(),
      timestamp: new Date().toISOString(),
      message: content,
      prompt_id,
    };
    this.writeRecord(record);
    return record.uuid;
  }

  private writeRecord(record: ChatRecord): void {
    if (!this.stream) {
      const filePath = path.join(
        this.storage.getChatsDir(),
        `${this.sessionId}.jsonl`
      );
      this.stream = fs.createWriteStream(filePath, { flags: 'a' });
    }
    this.stream.write(JSON.stringify(record) + '\n');
  }
}
```

---

## 3. 日志级别

| 级别 | 触发条件 | 输出位置 |
|------|----------|----------|
| DEBUG | `DEBUG=1` 环境变量 | `~/.qwen/tmp/<project>/debug.log` |
| INFO | 关键流程节点 | stderr（非交互模式）|
| WARN | 潜在问题 | debug.log + stderr |
| ERROR | 异常 | debug.log + 错误上报 |

---

## 4. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 调试日志未生成 | 检查 DEBUG 环境变量 | `debugLogger.ts` |
| Token 统计不准 | 检查 usageMetadata | `geminiChat.ts` |
| 会话记录丢失 | 检查 ChatRecordingService | `chatRecordingService.ts` |
| 错误未上报 | 检查 errorReporting | `errorReporting.ts` |

---

## 5. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| Debug Logger | ✅ | ✅ 继承 |
| UI Telemetry | ✅ | ✅ 继承 |
| Chat Recording | JSONL | ✅ 继承 |
| Error Reporting | ✅ | ✅ 继承 |

---

## 6. 总结

Qwen Code 的日志系统特点：

1. **分级记录** - DEBUG/INFO/WARN/ERROR 四级
2. **文件持久化** - JSONL 格式便于分析
3. **遥测统计** - Token 使用量实时监控
4. **错误上报** - 自动收集上下文
5. **环境控制** - DEBUG 变量控制详细程度
