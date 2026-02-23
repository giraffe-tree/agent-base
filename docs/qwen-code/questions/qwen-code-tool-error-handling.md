# 工具调用错误处理（Qwen Code）

本文深入分析 Qwen Code 的工具调用错误处理机制。

---

## 1. 错误类型定义

✅ **Verified**: `qwen-code/packages/core/src/tools/tool-error.ts`

```typescript
export enum ToolErrorType {
  // 工具未找到
  TOOL_NOT_FOUND = 'tool_not_found',

  // 参数错误
  INVALID_PARAMS = 'invalid_params',

  // 执行错误
  EXECUTION_ERROR = 'execution_error',

  // 用户取消
  USER_DECLINED = 'user_declined',

  // 超时
  TIMEOUT = 'timeout',

  // MCP 相关
  MCP_CONNECTION_ERROR = 'mcp_connection_error',
  MCP_TOOL_NOT_FOUND = 'mcp_tool_not_found',

  // 发现工具执行错误
  DISCOVERED_TOOL_EXECUTION_ERROR = 'discovered_tool_execution_error',
}

export interface ToolError {
  message: string;
  type: ToolErrorType;
  details?: Record<string, unknown>;
}
```

---

## 2. 错误处理流程

### 2.1 调度器错误处理

✅ **Verified**: `qwen-code/packages/core/src/core/coreToolScheduler.ts`

```typescript
async executePendingCalls(
  pendingCalls: ToolCallRequestInfo[],
  signal: AbortSignal,
): Promise<ToolCallResponseInfo[]> {
  const responses: ToolCallResponseInfo[] = [];

  for (const call of pendingCalls) {
    const tool = this.toolRegistry.getTool(call.name);

    // 1. 工具未找到
    if (!tool) {
      responses.push({
        callId: call.callId,
        responseParts: [{
          functionResponse: {
            name: call.name,
            response: { error: `Tool not found: ${call.name}` },
          },
        }],
        error: new Error(`Tool not found: ${call.name}`),
        errorType: ToolErrorType.TOOL_NOT_FOUND,
      });
      continue;
    }

    // 2. 参数校验
    let validatedParams: Record<string, unknown>;
    try {
      validatedParams = this.validateParams(tool, call.args);
    } catch (error) {
      responses.push({
        callId: call.callId,
        responseParts: [{
          functionResponse: {
            name: call.name,
            response: { error: `Invalid params: ${error}` },
          },
        }],
        error: error instanceof Error ? error : new Error(String(error)),
        errorType: ToolErrorType.INVALID_PARAMS,
      });
      continue;
    }

    // 3. 执行工具
    try {
      const result = await tool.execute(validatedParams, signal);
      responses.push({
        callId: call.callId,
        responseParts: [{
          functionResponse: {
            name: call.name,
            response: { output: result.llmContent },
          },
        }],
        resultDisplay: result.returnDisplay,
        error: result.error ? new Error(result.error.message) : undefined,
        errorType: result.error?.type,
      });
    } catch (error) {
      // 4. 执行异常
      responses.push({
        callId: call.callId,
        responseParts: [{
          functionResponse: {
            name: call.name,
            response: { error: String(error) },
          },
        }],
        error: error instanceof Error ? error : new Error(String(error)),
        errorType: ToolErrorType.EXECUTION_ERROR,
      });
    }
  }

  return responses;
}
```

---

## 3. 错误恢复策略

| 错误类型 | 处理策略 | 是否重试 |
|----------|----------|----------|
| TOOL_NOT_FOUND | 返回错误给模型 | 否 |
| INVALID_PARAMS | 返回错误给模型 | 否 |
| EXECUTION_ERROR | 返回错误详情 | 视工具而定 |
| TIMEOUT | 返回超时错误 | 可配置 |
| MCP_CONNECTION_ERROR | 断开重连 | 是 |

---

## 4. UI 层错误展示

```typescript
// 错误消息在 UI 中展示
function ToolCallResult({ response }: { response: ToolCallResponseInfo }) {
  if (response.error) {
    return (
      <Box color="red">
        <Text>❌ Error: {response.error.message}</Text>
        {response.errorType && (
          <Text dimColor>Type: {response.errorType}</Text>
        )}
      </Box>
    );
  }

  return <Text>{response.resultDisplay}</Text>;
}
```

---

## 5. 总结

Qwen Code 的工具错误处理特点：

1. **类型化错误** - ToolErrorType 枚举明确定义
2. **分层处理** - 校验层、执行层分别处理
3. **错误回传** - 所有错误通过 functionResponse 回传模型
4. **用户可见** - UI 层展示错误详情
