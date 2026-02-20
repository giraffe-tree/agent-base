# Prompt Organization（gemini-cli）

结论先行：gemini-cli 采用"模块化 snippet 架构 + 环境变量覆盖"的 prompt 组织方式，通过 `PromptProvider` 类动态组合多个代码片段，支持技能系统（Skills）和命令特定提示词，允许通过 `GEMINI_SYSTEM_MD` 环境变量进行完全覆盖。

---

## 1. Prompt 组织流程图

```text
+------------------------+
| 环境变量检查            |
| (GEMINI_SYSTEM_MD?)     |
+-----------+------------+
            |
            +------------+------------+
            |                         |
            v                         v
+------------------------+   +------------------------+
| 使用外部 Prompt 文件    |   | 使用内置 Prompt 系统   |
| (直接加载覆盖)          |   | (动态组合)             |
+-----------+------------+   +-----------+------------+
                                         |
                                         v
                            +------------------------+
                            | PromptProvider 初始化   |
                            | (加载 snippets)         |
                            +-----------+------------+
                                        |
                                        v
                            +------------------------+
                            | 基础 Snippets 加载      |
                            | (core, system, rules)   |
                            +-----------+------------+
                                        |
                                        v
                            +------------------------+
                            | 技能系统检查            |
                            | (.gemini/skills/)       |
                            +-----------+------------+
                                        |
                                        v
                            +------------------------+
                            | 命令特定 Snippets       |
                            | (.gemini/commands/)     |
                            +-----------+------------+
                                        |
                                        v
                            +------------------------+
                            | 动态组合与渲染          |
                            +-----------+------------+
                                        |
                                        v
                            +------------------------+
                            | 最终 Prompt             |
                            +------------------------+
```

---

## 2. 分层架构详解

```text
┌─────────────────────────────────────────────────────┐
│ Layer 4: Command-Specific Snippets                   │
│  - 特定命令的额外上下文                               │
│  - 命令参数和选项说明                                 │
├─────────────────────────────────────────────────────┤
│ Layer 3: Skills Layer                                │
│  - 技能定义 (Gemini Skills)                          │
│  - 领域特定知识和能力                                 │
├─────────────────────────────────────────────────────┤
│ Layer 2: Core Snippets                               │
│  - 系统身份和规则                                     │
│  - 工具使用说明                                       │
│  - 安全约束                                           │
├─────────────────────────────────────────────────────┤
│ Layer 1: Base / Override                             │
│  - GEMINI_SYSTEM_MD 环境变量覆盖                      │
│  - 项目级 .gemini/config                              │
└─────────────────────────────────────────────────────┘
```

---

## 3. Prompt 文件位置

| 文件路径 | 职责 |
|---------|------|
| `gemini-cli/packages/core/src/prompts/snippets.ts` | 核心代码片段定义 |
| `gemini-cli/packages/core/src/prompts/` | Prompt 模块目录 |
| `.gemini/skills/` | 用户自定义技能目录 |
| `.gemini/commands/` | 命令特定 prompt 目录 |
| `.gemini/config` | 项目级 prompt 配置 |
| `GEMINI_SYSTEM_MD` 环境变量 | 完全覆盖系统 prompt |

---

## 4. 加载与管理机制

### 4.1 PromptProvider 类

```typescript
// 核心 Prompt 提供类
class PromptProvider {
  private snippets: Map<string, string>;
  private skills: Skill[];
  private config: PromptConfig;

  constructor(options: PromptProviderOptions) {
    // 1. 检查环境变量覆盖
    if (process.env.GEMINI_SYSTEM_MD) {
      this.loadFromEnv();
      return;
    }

    // 2. 加载内置 snippets
    this.loadCoreSnippets();

    // 3. 加载用户技能
    this.loadSkills();

    // 4. 加载命令特定 snippets
    this.loadCommandSnippets();
  }

  buildPrompt(context: PromptContext): string {
    // 动态组合所有片段
    return this.assembleSnippets(context);
  }
}
```

### 4.2 环境变量覆盖机制

```typescript
function loadSystemPrompt(): string {
  // 最高优先级：环境变量完全覆盖
  if (env.GEMINI_SYSTEM_MD) {
    return fs.readFileSync(env.GEMINI_SYSTEM_MD, 'utf-8');
  }

  // 次优先级：项目配置
  if (fs.existsSync('.gemini/config')) {
    const config = loadConfig('.gemini/config');
    if (config.systemPrompt) {
      return config.systemPrompt;
    }
  }

  // 默认：使用内置 prompt
  return buildDefaultPrompt();
}
```

### 4.3 技能系统加载

```typescript
async function loadSkills(): Promise<Skill[]> {
  const skillsDir = '.gemini/skills';
  if (!fs.existsSync(skillsDir)) {
    return [];
  }

  const skillFiles = await glob(`${skillsDir}/**/*.md`);
  return skillFiles.map(file => ({
    name: path.basename(file, '.md'),
    content: fs.readFileSync(file, 'utf-8'),
    metadata: parseFrontMatter(file)
  }));
}
```

---

## 5. 模板与变量系统

### 5.1 函数式渲染

不同于传统的模板引擎，gemini-cli 采用函数式渲染：

```typescript
// snippets.ts 中的片段定义
const snippets = {
  core: () => `
You are Gemini, a helpful AI assistant.
Current time: ${new Date().toISOString()}
`,

  tools: (availableTools: Tool[]) => `
Available tools:
${availableTools.map(t => `- ${t.name}: ${t.description}`).join('\n')}
`,

  context: (files: string[]) => `
Relevant files:
${files.map(f => `- ${f}`).join('\n')}
`,
};
```

### 5.2 变量类型

| 变量类别 | 来源 | 说明 |
|---------|------|------|
| `cwd` | 运行时 | 当前工作目录 |
| `home` | 运行时 | 用户主目录 |
| `availableTools` | 配置 | 当前启用的工具 |
| `skills` | 用户定义 | 加载的技能列表 |
| `command` | 运行时 | 当前执行的命令 |
| `contextFiles` | 自动发现 | 相关代码文件 |

### 5.3 Context 组装

```typescript
interface PromptContext {
  // 环境信息
  cwd: string;
  home: string;
  env: Record<string, string>;

  // 工具信息
  tools: Tool[];

  // 用户输入
  query: string;
  history: Message[];

  // 项目上下文
  skills?: Skill[];
  relevantFiles?: string[];
  gitInfo?: GitInfo;
}
```

---

## 6. Prompt 工程方法

### 6.1 Snippet 组合模式

```typescript
function assemblePrompt(context: PromptContext): string {
  const parts: string[] = [];

  // 1. 核心身份
  parts.push(snippets.core());

  // 2. 系统规则
  parts.push(snippets.rules());

  // 3. 工具说明
  if (context.tools.length > 0) {
    parts.push(snippets.tools(context.tools));
  }

  // 4. 技能注入
  if (context.skills) {
    for (const skill of context.skills) {
      parts.push(skill.content);
    }
  }

  // 5. 文件上下文
  if (context.relevantFiles) {
    parts.push(snippets.context(context.relevantFiles));
  }

  return parts.join('\n\n---\n\n');
}
```

### 6.2 技能系统设计

技能文件结构（`.gemini/skills/{skill-name}.md`）：

```markdown
---
name: react-expert
description: Expert in React development
commands: [explain, refactor]
---

When working with React code:
1. Prefer functional components over class components
2. Use hooks for state management
3. Follow React best practices
```

### 6.3 命令特定增强

```typescript
function getCommandSnippet(command: string): string | null {
  const commandFile = `.gemini/commands/${command}.md`;
  if (fs.existsSync(commandFile)) {
    return fs.readFileSync(commandFile, 'utf-8');
  }
  return null;
}
```

---

## 7. 实际示例

### 示例 1：explain 命令 + react-expert 技能

**场景设定**：用户在 React 项目目录下执行 `gemini explain src/components/UserProfile.tsx`，启用了 `react-expert` 技能。

**运行时变量值**：
```json
{
  "cwd": "/home/user/react-app",
  "availableTools": [
    {"name": "read", "description": "Read file contents"},
    {"name": "search", "description": "Search across files"},
    {"name": "git", "description": "Git operations"}
  ],
  "relevantFiles": [
    "src/components/UserProfile.tsx",
    "src/types/user.ts",
    "src/api/user.ts"
  ],
  "skills": [
    {
      "name": "react-expert",
      "content": "When working with React code:\n1. Explain component lifecycle and hooks usage\n2. Identify performance optimization opportunities\n3. Suggest modern React patterns (hooks over classes)\n4. Check for accessibility issues"
    }
  ],
  "command": "explain"
}
```

**完整渲染结果（发送给模型的 Prompt）**：

```markdown
You are Gemini, a helpful AI assistant.
Current time: 2024-01-15T10:30:00.000Z
Working directory: /home/user/react-app

Always provide clear, concise explanations.
Use code examples when helpful.

Available tools:
- read: Read file contents
- search: Search across files
- git: Git operations

When working with React code:
1. Explain component lifecycle and hooks usage
2. Identify performance optimization opportunities
3. Suggest modern React patterns (hooks over classes)
4. Check for accessibility issues

Explain the code in detail:
- What does this component/function do?
- What are its props/state?
- How does it fit into the overall architecture?

Relevant files:
- src/components/UserProfile.tsx
- src/types/user.ts
- src/api/user.ts

---

User command: explain src/components/UserProfile.tsx
```

---

### 示例 2：refactor 命令

**场景设定**：用户使用 refactor 命令优化一个 JavaScript 函数。

**运行时变量值**：
```json
{
  "cwd": "/home/user/node-app",
  "availableTools": [
    {"name": "read", "description": "Read file contents"},
    {"name": "write", "description": "Write file contents"},
    {"name": "search", "description": "Search across files"}
  ],
  "relevantFiles": [
    "src/utils/dataProcessor.js"
  ],
  "skills": [],
  "command": "refactor"
}
```

**完整渲染结果（发送给模型的 Prompt）**：

```markdown
You are Gemini, a helpful AI assistant.
Current time: 2024-01-15T14:22:00.000Z
Working directory: /home/user/node-app

Always provide clear, concise explanations.
Use code examples when helpful.

Available tools:
- read: Read file contents
- write: Write file contents
- search: Search across files

Refactor the code to improve:
- Readability and maintainability
- Performance where applicable
- Modern best practices

Provide the refactored code with explanations of changes made.

Relevant files:
- src/utils/dataProcessor.js

---

User command: refactor src/utils/dataProcessor.js
```

---

### 示例 3：无技能模式 vs 技能模式对比

**场景设定**：同一请求，对比无技能和有 python-expert 技能时的 prompt 差异。

**运行时变量值（无技能）**：
```json
{
  "cwd": "/home/user/python-project",
  "availableTools": [
    {"name": "read", "description": "Read file contents"},
    {"name": "bash", "description": "Execute shell commands"}
  ],
  "relevantFiles": ["main.py"],
  "skills": [],
  "command": "explain"
}
```

**完整渲染结果（无技能模式）**：

```markdown
You are Gemini, a helpful AI assistant.
Current time: 2024-01-15T09:00:00.000Z
Working directory: /home/user/python-project

Always provide clear, concise explanations.
Use code examples when helpful.

Available tools:
- read: Read file contents
- bash: Execute shell commands

Explain the code in detail:
- What does this component/function do?
- What are its inputs/outputs?
- How does it work?

Relevant files:
- main.py

---

User command: explain main.py
```

**运行时变量值（有 python-expert 技能）**：
```json
{
  "cwd": "/home/user/python-project",
  "availableTools": [
    {"name": "read", "description": "Read file contents"},
    {"name": "bash", "description": "Execute shell commands"}
  ],
  "relevantFiles": ["main.py"],
  "skills": [
    {
      "name": "python-expert",
      "content": "When working with Python code:\n1. Follow PEP 8 style guidelines\n2. Suggest type hints where appropriate\n3. Identify Pythonic patterns and anti-patterns\n4. Consider performance implications of data structure choices\n5. Recommend modern Python features (3.8+) when beneficial"
    }
  ],
  "command": "explain"
}
```

**完整渲染结果（有技能模式）**：

```markdown
You are Gemini, a helpful AI assistant.
Current time: 2024-01-15T09:00:00.000Z
Working directory: /home/user/python-project

Always provide clear, concise explanations.
Use code examples when helpful.

Available tools:
- read: Read file contents
- bash: Execute shell commands

When working with Python code:
1. Follow PEP 8 style guidelines
2. Suggest type hints where appropriate
3. Identify Pythonic patterns and anti-patterns
4. Consider performance implications of data structure choices
5. Recommend modern Python features (3.8+) when beneficial

Explain the code in detail:
- What does this component/function do?
- What are its inputs/outputs?
- How does it work?

Relevant files:
- main.py

---

User command: explain main.py
```

---

## 8. 证据索引

- `gemini-cli` + `gemini-cli/packages/core/src/prompts/snippets.ts` + 核心代码片段定义
- `gemini-cli` + `gemini-cli/packages/core/src/prompts/prompt_provider.ts` + PromptProvider 类实现
- `gemini-cli` + `gemini-cli/packages/core/src/prompts/` + Prompt 模块目录结构
- `gemini-cli` + `.gemini/skills/` + 用户自定义技能目录（约定）
- `gemini-cli` + `docs/gemini-cli/04-gemini-cli-agent-loop.md` + Agent 循环中的 prompt 使用

---

## 9. 边界与不确定性

- 具体的 snippet 名称和组合顺序需以实码为准
- 技能系统的元数据格式（Front Matter）需验证
- 环境变量覆盖的优先级与其他配置方式的交互需确认

