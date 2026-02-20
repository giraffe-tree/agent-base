# Prompt Organization（opencode）

结论先行：opencode 采用"Provider-specific 多版本 + TypeScript 模块导入"的 prompt 组织方式，通过将 `.txt` 文件作为字符串模块导入，为不同模型提供商（Anthropic/Gemini/Beast）和专用 Agent 提供定制化的提示词。

---

## 1. Prompt 组织流程图

```text
+------------------------+
| Provider 检测           |
| (Anthropic/Gemini/      |
|  Beast/Default)         |
+-----------+------------+
            |
            v
+------------------------+
| Prompt 文件选择         |
| (*.txt 模块)            |
+-----------+------------+
            |
            v
+------------------------+
| TypeScript 导入         |
| (import * as prompt     |
|   from './prompt.txt')  |
+-----------+------------+
            |
            v
+------------------------+
| Agent 类型选择          |
| (session/agent/         |
|  specialized)           |
+-----------+------------+
            |
            v
+------------------------+
| 变量替换                |
| (字符串替换)            |
+-----------+------------+
            |
            v
+------------------------+
| 最终 Prompt             |
| (发送至模型)            |
+------------------------+
```

---

## 2. 分层架构详解

```text
┌─────────────────────────────────────────────────────┐
│ Layer 3: Agent-Specific Prompts                      │
│  - 专用 Agent 提示词                                  │
│  - 特定任务场景定制                                   │
├─────────────────────────────────────────────────────┤
│ Layer 2: Session Prompts                             │
│  - 会话级系统提示词                                   │
│  - 对话上下文管理                                     │
├─────────────────────────────────────────────────────┤
│ Layer 1: Provider-Specific Base                      │
│  - Anthropic (Claude) 专用优化                       │
│  - Gemini 专用优化                                    │
│  - Beast 专用优化                                     │
│  - 默认/通用版本                                      │
└─────────────────────────────────────────────────────┘
```

---

## 3. Prompt 文件位置

| 文件路径 | 职责 |
|---------|------|
| `opencode/src/session/prompt/*.txt` | 会话级 prompt 模板 |
| `opencode/src/agent/prompt/*.txt` | Agent 级 prompt 模板 |
| `opencode/src/prompts/anthropic/` | Anthropic 模型专用 prompt |
| `opencode/src/prompts/gemini/` | Gemini 模型专用 prompt |
| `opencode/src/prompts/beast/` | Beast 模型专用 prompt |
| `opencode/src/prompts/default/` | 默认 prompt 版本 |

---

## 4. 加载与管理机制

### 4.1 TypeScript 模块导入

```typescript
// 将 txt 文件作为字符串模块导入
// 需要配置 TypeScript/Bundler 支持
import systemPrompt from './prompts/system.txt';
import anthropicPrompt from './prompts/anthropic/system.txt';
import geminiPrompt from './prompts/gemini/system.txt';

// 使用示例
const getSystemPrompt = (provider: string): string => {
  switch (provider) {
    case 'anthropic':
      return anthropicPrompt;
    case 'gemini':
      return geminiPrompt;
    default:
      return systemPrompt;
  }
};
```

### 4.2 Provider 选择逻辑

```typescript
enum ModelProvider {
  ANTHROPIC = 'anthropic',
  GEMINI = 'gemini',
  BEAST = 'beast',
  DEFAULT = 'default'
}

class PromptManager {
  private provider: ModelProvider;

  constructor(provider: ModelProvider) {
    this.provider = provider;
  }

  getPrompt(type: PromptType): string {
    // 优先返回 provider-specific 版本
    const providerPrompt = this.loadProviderPrompt(type);
    if (providerPrompt) {
      return providerPrompt;
    }
    // 回退到默认版本
    return this.loadDefaultPrompt(type);
  }

  private loadProviderPrompt(type: PromptType): string | null {
    // 动态加载对应 provider 的 prompt
    const prompts = this.loadPromptsForProvider(this.provider);
    return prompts[type] || null;
  }
}
```

### 4.3 Prompt 目录结构

```
src/
├── prompts/
│   ├── default/
│   │   ├── system.txt
│   │   ├── tool_usage.txt
│   │   └── safety.txt
│   ├── anthropic/
│   │   ├── system.txt      # Claude 优化版本
│   │   └── tool_usage.txt
│   ├── gemini/
│   │   ├── system.txt      # Gemini 优化版本
│   │   └── tool_usage.txt
│   └── beast/
│       ├── system.txt      # Beast 优化版本
│       └── tool_usage.txt
├── session/
│   └── prompt/
│       ├── init.txt
│       └── context.txt
└── agent/
    └── prompt/
        ├── default.txt
        └── specialized/
            ├── reviewer.txt
            └── debugger.txt
```

---

## 5. 模板与变量系统

### 5.1 简单字符串替换

不同于复杂的模板引擎，opencode 采用简单的字符串替换：

```typescript
// prompt.txt 中的占位符
// 欢迎使用 {{APP_NAME}}
// 当前版本: {{VERSION}}
// 模型: {{MODEL_NAME}}

function renderPrompt(
  template: string,
  variables: Record<string, string>
): string {
  return template.replace(
    /\{\{(\w+)\}\}/g,
    (match, key) => variables[key] || match
  );
}

// 使用示例
const finalPrompt = renderPrompt(systemPrompt, {
  APP_NAME: 'OpenCode',
  VERSION: '1.0.0',
  MODEL_NAME: 'claude-3-opus'
});
```

### 5.2 变量类型

| 变量名 | 说明 | 示例值 |
|-------|------|--------|
| `{{APP_NAME}}` | 应用名称 | "OpenCode" |
| `{{VERSION}}` | 版本号 | "1.0.0" |
| `{{MODEL_NAME}}` | 模型名称 | "claude-3-opus" |
| `{{CWD}}` | 当前目录 | "/project" |
| `{{DATE}}` | 当前日期 | "2024-01-15" |

### 5.3 Provider 特定适配

```typescript
// Anthropic 版本优化
// - 使用 XML 标签进行结构化
// - 强调 thinking 过程

// Gemini 版本优化
// - 调整格式以适应 Gemini 的解析习惯
// - 简化工具描述

// Beast 版本优化
// - 针对本地模型的优化
// - 更详细的指令
```

---

## 6. Prompt 工程方法

### 6.1 Provider 特定优化策略

```typescript
const providerOptimizations: Record<ModelProvider, PromptOptimization> = {
  [ModelProvider.ANTHROPIC]: {
    // Claude 偏好 XML 结构
    structureTag: '<system>',
    toolFormat: 'xml',
    emphasizeThinking: true
  },
  [ModelProvider.GEMINI]: {
    // Gemini 偏好 Markdown
    structureTag: '##',
    toolFormat: 'json',
    emphasizeThinking: false
  },
  [ModelProvider.BEAST]: {
    // 本地模型需要更详细说明
    structureTag: '###',
    toolFormat: 'detailed',
    emphasizeThinking: true
  }
};
```

### 6.2 专用 Agent 模式

```typescript
enum SpecializedAgent {
  CODE_REVIEWER = 'reviewer',
  DEBUGGER = 'debugger',
  TEST_WRITER = 'test_writer',
  DOC_WRITER = 'doc_writer'
}

function getSpecializedPrompt(
  agent: SpecializedAgent,
  provider: ModelProvider
): string {
  const basePrompt = loadPrompt(`agent/prompt/specialized/${agent}.txt`);
  const providerSuffix = provider !== ModelProvider.DEFAULT
    ? `_${provider}`
    : '';

  // 尝试加载 provider-specific 版本
  const specializedPrompt = loadPrompt(
    `agent/prompt/specialized/${agent}${providerSuffix}.txt`
  );

  return specializedPrompt || basePrompt;
}
```

### 6.3 Prompt 组合模式

```typescript
interface PromptParts {
  system: string;
  providerContext: string;
  agentSpecific: string;
  safety: string;
}

function assembleFullPrompt(parts: PromptParts): string {
  return [
    parts.system,
    parts.providerContext,
    parts.agentSpecific,
    parts.safety
  ].join('\n\n---\n\n');
}
```

---

## 7. 证据索引

- `opencode` + `opencode/src/session/prompt/*.txt` + 会话级 prompt 模板
- `opencode` + `opencode/src/agent/prompt/*.txt` + Agent 级 prompt 模板
- `opencode` + `opencode/src/prompts/anthropic/` + Anthropic 模型专用优化 prompt
- `opencode` + `opencode/src/prompts/gemini/` + Gemini 模型专用优化 prompt
- `opencode` + `opencode/src/prompts/beast/` + Beast 模型专用优化 prompt
- `opencode` + `docs/opencode/04-opencode-agent-loop.md` + Agent 循环中的 prompt 使用

---

## 8. 边界与不确定性

- `.txt` 文件的 TypeScript 模块导入需要特定的构建配置（如 raw-loader）
- Provider 检测的具体逻辑和优先级需以实码为准
- 专用 Agent 的完整列表和命名需验证
- 变量替换的具体语法（双大括号 vs 其他）可能有所调整

