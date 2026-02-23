# Prompt 组织（Qwen Code）

本文分析 Qwen Code 的 Prompt 组织机制，包括 System Prompt 构造、工具描述生成和动态提醒。

---

## 1. 先看全局（流程图）

### 1.1 Prompt 层级结构

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      Prompt 层级                                     │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  System Prompt (核心)                                          │  │
│  │  (packages/core/src/core/prompts.ts)                           │  │
│  │                                                                │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ getCoreSystemPrompt()                                    │  │  │
│  │  │ - 角色定义 (AI 编程助手)                                  │  │  │
│  │  │ - 行为准则 (工具使用规范)                                  │  │  │
│  │  │ - 输出格式要求                                            │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                                │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ getCustomSystemPrompt()                                  │  │  │
│  │  │ - 用户自定义 system prompt                               │  │  │
│  │  │ - 从 .qwen.md 或设置加载                                  │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  工具声明 (Function Declarations)                               │  │
│  │  - ToolRegistry.getFunctionDeclarations()                      │  │
│  │  - 动态生成工具 schema                                         │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  动态提醒 (System Reminders)                                    │  │
│  │                                                                │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ getSubagentSystemReminder()                              │  │  │
│  │  │ - 列出可用子代理                                          │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                                │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ getPlanModeSystemReminder()                              │  │  │
│  │  │ - Plan 模式行为提醒                                       │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件

### 2.1 System Prompt 生成

✅ **Verified**: `qwen-code/packages/core/src/core/prompts.ts`

```typescript
export function getCoreSystemPrompt(): string {
  return `You are an AI coding assistant. You help users with programming tasks.

Guidelines:
- Use the provided tools to interact with the file system and execute commands
- Always prefer reading files before modifying them
- When editing files, use the edit tool with clear descriptions
- Explain your reasoning before taking actions
- If you're unsure about something, ask for clarification`;
}

export function getCustomSystemPrompt(config: Config): string | undefined {
  // 从配置加载自定义 prompt
  const customPrompt = config.getCustomSystemPrompt();
  if (customPrompt) {
    return customPrompt;
  }

  // 从 .qwen.md 文件加载
  const geminiMdContent = config.getGeminiMdFileContent();
  if (geminiMdContent) {
    return geminiMdContent;
  }

  return undefined;
}
```

### 2.2 子代理提醒

✅ **Verified**: `qwen-code/packages/core/src/core/prompts.ts`

```typescript
export function getSubagentSystemReminder(subagentNames: string[]): string {
  return `You have access to the following subagents: ${subagentNames.join(', ')}

Use subagents for specialized tasks:
- Delegate appropriate work to subagents
- Provide clear context and instructions
- Review their results before incorporating`;
}
```

### 2.3 Plan 模式提醒

✅ **Verified**: `qwen-code/packages/core/src/core/prompts.ts`

```typescript
export function getPlanModeSystemReminder(sdkMode: boolean): string {
  return `You are in PLAN mode.

In this mode:
1. First create a plan using the todoWrite tool
2. Wait for user approval of the plan
3. Execute the plan step by step
4. Mark todos as complete as you progress

${sdkMode ? 'SDK mode is active. Tools will be executed automatically after approval.' : ''}`;
}
```

---

## 3. 工具描述生成

### 3.1 Schema 生成

```typescript
// ToolRegistry 自动生成 FunctionDeclaration
getFunctionDeclarations(): FunctionDeclaration[] {
  const declarations: FunctionDeclaration[] = [];
  this.tools.forEach((tool) => {
    declarations.push({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameterSchema,
    });
  });
  return declarations;
}
```

### 3.2 工具描述示例

```typescript
// read-file 工具 schema
{
  name: 'read_file',
  description: 'Read the contents of a file at the specified path. ' +
    'Optionally specify a line offset and limit to read a specific range.',
  parameters: {
    type: 'object',
    properties: {
      path: {
        type: 'string',
        description: 'The path to the file to read'
      },
      offset: {
        type: 'number',
        description: 'The line number to start reading from (0-indexed)'
      },
      limit: {
        type: 'number',
        description: 'The maximum number of lines to read'
      }
    },
    required: ['path']
  }
}
```

---

## 4. .qwen.md 文件

### 4.1 加载优先级

```
1. 项目级 .qwen.md（当前目录）
2. 用户级 ~/.qwen/.qwen.md
3. 配置中的 customSystemPrompt
```

### 4.2 文件格式示例

```markdown
# Qwen Code Project Configuration

You are working on a TypeScript project. Please:

- Use TypeScript strict mode
- Prefer async/await over callbacks
- Follow the existing code style
- Run tests before completing tasks

## Common Commands

- Build: `npm run build`
- Test: `npm test`
- Lint: `npm run lint`
```

---

## 5. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| System Prompt 未生效 | 检查 getCustomSystemPrompt | `prompts.ts` |
| 工具描述不清晰 | 检查 tool.description | 各工具文件 |
| .qwen.md 未加载 | 检查文件路径和权限 | `config.ts` |
| 提醒重复添加 | 检查 isContinuation 条件 | `client.ts:499` |

---

## 6. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| Core System Prompt | ✅ | ✅ 继承 |
| Custom Prompt | ✅ | ✅ 继承 |
| 子代理提醒 | ✅ | ✅ 继承 |
| Plan 模式提醒 | ✅ | ✅ 继承 |
| .qwen.md | .gemini.md | ✅ 继承 |

---

## 7. 总结

Qwen Code 的 Prompt 组织特点：

1. **分层结构** - Core/Custom/Reminders 三层
2. **文件化配置** - .qwen.md 项目级配置
3. **动态提醒** - 根据模式/子代理动态添加
4. **自动 Schema** - 从工具定义自动生成
5. **继承扩展** - 完整继承 Gemini CLI 的 prompt 系统
