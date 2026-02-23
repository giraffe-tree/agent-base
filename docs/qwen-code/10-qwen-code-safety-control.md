# Safety Control（Qwen Code）

本文分析 Qwen Code 的安全控制机制，包括工具确认、沙盒隔离和敏感操作保护。

---

## 1. 先看全局（流程图）

### 1.1 安全控制层级

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      安全控制层级                                    │
│                                                                      │
│  Level 1: 沙盒隔离 (可选)                                             │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ start_sandbox() 启动 Docker 容器                               │  │
│  │ - 文件系统隔离                                                  │  │
│  │ - 网络隔离                                                      │  │
│  │ - 资源限制                                                      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  Level 2: 工具确认 (Approval Mode)                                    │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ ApprovalMode.SUGGEST  - 提示但允许自动批准                      │  │
│  │ ApprovalMode.AUTO     - 完全自动（--yolo）                      │  │
│  │ ApprovalMode.PLAN     - Plan 模式（分阶段确认）                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  Level 3: 工具级确认                                                  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ tool.shouldConfirmExecute()                                    │  │
│  │ - 读操作：通常无需确认                                          │  │
│  │ - 写操作：需要用户确认                                          │  │
│  │ - 执行操作：强制确认                                            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  Level 4: 文件夹信任                                                   │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ isTrustedFolder()                                              │  │
│  │ - 受信任：完整功能                                              │  │
│  │ - 不受信任：限制 MCP、某些工具                                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件

### 2.1 ApprovalMode

✅ **Verified**: `qwen-code/packages/core/src/config/config.ts`

```typescript
export enum ApprovalMode {
  SUGGEST = 'suggest',  // 建议确认但可自动批准
  AUTO = 'auto',        // 完全自动（危险操作仍确认）
  PLAN = 'plan',        // Plan 模式，分阶段确认
}
```

### 2.2 工具确认检查

✅ **Verified**: `qwen-code/packages/core/src/tools/read-file.ts`

```typescript
class ReadFileTool extends BaseDeclarativeTool {
  async shouldConfirmExecute(
    params: ReadFileParams,
    abortSignal: AbortSignal,
  ): Promise<ToolCallConfirmationDetails | false> {
    // 读文件通常不需要确认
    return false;
  }
}
```

✅ **Verified**: `qwen-code/packages/core/src/tools/shell.ts`

```typescript
class ShellTool extends BaseDeclarativeTool {
  async shouldConfirmExecute(
    params: ShellParams,
    abortSignal: AbortSignal,
  ): Promise<ToolCallConfirmationDetails | false> {
    // Shell 执行需要确认
    return {
      toolName: this.displayName,
      description: `Execute: ${params.command}`,
      requiresConfirmation: true,
    };
  }
}
```

### 2.3 文件夹信任检查

✅ **Verified**: `qwen-code/packages/core/src/tools/mcp-client-manager.ts:56`

```typescript
async discoverAllMcpTools(cliConfig: Config): Promise<void> {
  if (!cliConfig.isTrustedFolder()) {
    // 不受信任文件夹禁用 MCP
    return;
  }
  // ... 发现 MCP 工具
}
```

---

## 3. 确认对话框

### 3.1 UI 确认流程

```typescript
// UI 层处理工具确认
async function scheduleToolCalls(
  toolCalls: ToolCallRequestInfo[],
): Promise<ToolCallResponseInfo[]> {
  for (const call of toolCalls) {
    const tool = toolRegistry.getTool(call.name);
    const confirmation = await tool.shouldConfirmExecute(call.args, signal);

    if (confirmation && confirmation.requiresConfirmation) {
      // 显示确认对话框
      const approved = await showConfirmationDialog({
        toolName: confirmation.toolName,
        description: confirmation.description,
        options: ['Approve', 'Approve All', 'Cancel', 'Edit'],
      });

      if (!approved) {
        // 用户拒绝，返回错误
        return { error: 'User declined' };
      }
    }

    // 执行工具
    return await tool.execute(call.args, signal);
  }
}
```

---

## 4. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 工具不执行 | 检查 ApprovalMode | `config.ts` |
| 无确认弹窗 | 检查 shouldConfirmExecute | 各工具文件 |
| MCP 不工作 | 检查 isTrustedFolder | `mcp-client-manager.ts:56` |
| 沙盒启动失败 | 检查 sandboxConfig | `gemini.tsx:248` |

---

## 5. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| ApprovalMode | SUGGEST/AUTO/PLAN | ✅ 继承 |
| 工具确认 | 支持 | ✅ 继承 |
| 沙盒隔离 | ✅ 支持 | ✅ 继承 |
| 文件夹信任 | ✅ 支持 | ✅ 继承 |

---

## 6. 总结

Qwen Code 的安全控制特点：

1. **多层防护** - 沙盒、确认模式、工具级检查、文件夹信任
2. **灵活配置** - ApprovalMode 适应不同场景
3. **默认保守** - 写操作和 shell 默认需要确认
4. **信任模型** - 区分受信任/不受信任文件夹
