# OpenCode Plan and Execute 模式

**结论先行**: OpenCode 实现了**结构化的 Plan and Execute 模式**，通过 `plan` 和 `build` 两种 Agent 类型实现。Plan Agent 是只读模式，禁止所有编辑操作（除计划文件外）；Build Agent 是执行模式，拥有完整工具访问权限。模式切换通过 `plan_enter` 和 `plan_exit` 工具完成，目前标记为实验性功能。

---

## 1. Agent 定义与架构

### 1.1 Build Agent vs Plan Agent

位于 `opencode/packages/opencode/src/agent/agent.ts`:

```typescript
// Build Agent - 执行模式
build: {
  name: "build",
  description: "The default agent. Executes tools based on configured permissions.",
  permission: PermissionNext.merge(
    defaults,
    PermissionNext.fromConfig({
      question: "allow",
      plan_enter: "allow",  // 可以进入 plan 模式
    }),
    user,
  ),
  mode: "primary",
  native: true,
},

// Plan Agent - 计划模式
plan: {
  name: "plan",
  description: "Plan mode. Disallows all edit tools.",
  permission: PermissionNext.merge(
    defaults,
    PermissionNext.fromConfig({
      question: "allow",
      plan_exit: "allow",  // 可以退出 plan 模式
      external_directory: {
        [path.join(Global.Path.data, "plans", "*")]: "allow",
      },
      edit: {
        "*": "deny",  // 禁止所有编辑
        [path.join(".opencode", "plans", "*.md")]: "allow",  // 除了计划文件
        [path.relative(Instance.worktree, path.join(Global.Path.data, path.join("plans", "*.md")))]: "allow",
      },
    }),
    user,
  ),
  mode: "primary",
  native: true,
},
```

**关键区别**:
- **Build Agent**: 默认执行模式，可以发起进入 Plan 模式
- **Plan Agent**: 计划模式，禁止所有编辑操作，只能编辑计划文件

---

## 2. Plan Mode 工具

### 2.1 PlanEnterTool

位于 `opencode/packages/opencode/src/tool/plan.ts`:

```typescript
export class PlanEnterTool extends BaseTool {
  async execute(): Promise<void> {
    // 提示用户切换到 plan agent 进行研究和规划
    // 创建新消息切换 agent 到 "plan"
    // Plan 文件位置: .opencode/plans/{timestamp}-{session-slug}.md
  }
}
```

**功能**:
- 提示用户切换到 Plan Agent 进行研究和规划
- 创建新消息切换 Agent 到 `plan`
- Plan 文件位置: `.opencode/plans/{timestamp}-{session-slug}.md`

### 2.2 PlanExitTool

```typescript
export class PlanExitTool extends BaseTool {
  async execute(): Promise<void> {
    // 规划完成时调用
    // 请求用户切换到 Build Agent 开始实现
    // 创建新消息切换 agent 到 "build"
  }
}
```

**功能**:
- 规划完成时调用
- 请求用户切换到 Build Agent 开始实现
- 创建新消息切换 Agent 到 `build`

---

## 3. 功能标志控制

位于 `opencode/packages/opencode/src/flag/flag.ts`:

```typescript
export const OPENCODE_EXPERIMENTAL_PLAN_MODE = OPENCODE_EXPERIMENTAL || truthy("OPENCODE_EXPERIMENTAL_PLAN_MODE")
```

**启用方式**:
- 环境变量: `OPENCODE_EXPERIMENTAL_PLAN_MODE=true`
- 或: `OPENCODE_EXPERIMENTAL=true`

**当前状态**: Plan Mode 标记为**实验性功能**。

---

## 4. 工具注册条件

位于 `opencode/packages/opencode/src/tool/registry.ts`:

```typescript
// Plan mode tools only available when experimental flag is on and using CLI
...(Flag.OPENCODE_EXPERIMENTAL_PLAN_MODE && Flag.OPENCODE_CLIENT === "cli"
  ? [PlanExitTool, PlanEnterTool]
  : []),
```

**条件**:
- 需要启用实验性 Plan Mode
- 仅 CLI 客户端可用

---

## 5. 五阶段规划工作流

位于 `opencode/packages/opencode/src/session/prompt.ts`:

### 5.1 Phase 1: Initial Understanding（初始理解）

```
Goal: Gain comprehensive understanding by reading code and asking questions
- Launch up to 3 explore agents IN PARALLEL
- Use question tool to clarify ambiguities
```

**目标**: 通过阅读代码和提问获得全面理解
- 并行启动最多 3 个 explore agents
- 使用 question 工具澄清模糊点

### 5.2 Phase 2: Design（设计）

```
Goal: Design implementation approach
- Launch general agent(s) to design based on exploration results
- Can launch up to 1 agent(s) in parallel
```

**目标**: 设计实现方案
- 基于探索结果启动 general agent 进行设计
- 可并行启动最多 1 个 agent

### 5.3 Phase 3: Review（审查）

```
Goal: Review plan(s) from Phase 2
- Read critical files identified by agents
- Ensure alignment with user intentions
- Use question tool for clarification
```

**目标**: 审查 Phase 2 的计划
- 读取 agents 识别的关键文件
- 确保与用户意图一致
- 使用 question 工具澄清

### 5.4 Phase 4: Final Plan（最终计划）

```
Goal: Write final plan to plan file (only editable file)
- Include recommended approach only
- Must be concise yet detailed enough to execute
- Include paths of critical files and verification steps
```

**目标**: 将最终计划写入计划文件
- 仅包含推荐的方案
- 必须简洁但足够详细以执行
- 包含关键文件路径和验证步骤

### 5.5 Phase 5: Call plan_exit tool

```
- Always call plan_exit when done planning
- Critical: turn should only end with question or plan_exit
```

**要求**:
- 规划完成时必须调用 `plan_exit`
- 回合只能以 question 或 `plan_exit` 结束

---

## 6. Plan 文件位置

位于 `opencode/packages/opencode/src/session/index.ts`:

```typescript
export function plan(input: { slug: string; time: { created: number } }) {
  const base = Instance.project.vcs
    ? path.join(Instance.worktree, ".opencode", "plans")
    : path.join(Global.Path.data, "plans")
  return path.join(base, [input.time.created, input.slug].join("-") + ".md")
}
```

**文件位置**:
- **有 git**: `.opencode/plans/{timestamp}-{slug}.md`
- **无 git**: `{data-dir}/plans/{timestamp}-{slug}.md`

---

## 7. 模式切换提示

### 7.1 Build 切换提示

位于 `opencode/packages/opencode/src/session/prompt/build-switch.txt`:

```text
<system-reminder>
Your operational mode has changed from plan to build.
You are no longer in read-only mode.
You are permitted to make file changes, run shell commands, and utilize your arsenal of tools as needed.
</system-reminder>
```

**作用**: 当从 Plan 模式切换到 Build 模式时，系统注入系统提醒。

---

## 8. 权限对比

### 8.1 Plan Agent 权限

| 操作 | 权限 | 说明 |
|-----|------|------|
| `question` | allow | 可以提问 |
| `plan_exit` | allow | 可以退出 Plan 模式 |
| `edit *` | deny | 禁止所有编辑 |
| `edit .opencode/plans/*.md` | allow | 允许编辑计划文件 |
| `external_directory` | allow | 允许访问 plans 目录 |

### 8.2 Build Agent 权限

| 操作 | 权限 | 说明 |
|-----|------|------|
| `question` | allow | 可以提问 |
| `plan_enter` | allow | 可以进入 Plan 模式 |
| 其他工具 | 根据配置 | 完整的工具访问权限 |

---

## 9. 工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│               OpenCode Plan and Execute 工作流程                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   用户输入                                                      │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────────┐                                          │
│   │ plan_enter 工具 │ 或用户触发                                │
│   └────────┬────────┘                                          │
│            ▼                                                    │
│   ┌─────────────────────────┐                                  │
│   │      PLAN AGENT         │                                  │
│   │      (只读模式)          │                                  │
│   ├─────────────────────────┤                                  │
│   │ Phase 1: Initial        │ 启动 3 个 explore agents         │
│   │         Understanding   │ 并行探索代码                     │
│   ├─────────────────────────┤                                  │
│   │ Phase 2: Design         │ 启动 1 个 general agent          │
│   │         设计方案         │ 设计实现方案                     │
│   ├─────────────────────────┤                                  │
│   │ Phase 3: Review         │ 审查计划                         │
│   │         审查             │ 确保与用户意图一致                │
│   ├─────────────────────────┤                                  │
│   │ Phase 4: Final Plan     │ 写入计划文件                     │
│   │         最终计划         │ .opencode/plans/*.md             │
│   └──────┬──────────────────┘                                  │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────┐                                          │
│   │ plan_exit 工具  │ 必须调用                                  │
│   └────────┬────────┘                                          │
│            ▼                                                    │
│   ┌─────────────────────────┐                                  │
│   │      BUILD AGENT        │                                  │
│   │      (执行模式)          │                                  │
│   ├─────────────────────────┤                                  │
│   │ 按照计划执行             │ 完整工具访问权限                  │
│   │ 编辑文件、运行命令       │                                  │
│   └─────────────────────────┘                                  │
│                                                                 │
│   注意：                                                         │
│   • 需要启用 OPENCODE_EXPERIMENTAL_PLAN_MODE                     │
│   • 仅 CLI 客户端支持                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 10. 关键设计亮点

| 特性 | 实现细节 |
|------|----------|
| **严格的权限分离** | Plan Agent 禁止所有编辑工具，只允许编辑计划文件 |
| **显式用户批准** | 模式切换需要显式用户确认，通过 question 提示 |
| **文件持久化** | 计划以 Markdown 文件形式存储，支持版本控制 |
| **并行探索** | Phase 1 允许并行启动最多 3 个 explore agents |
| **合成消息** | 模式切换通过注入合成用户消息实现，设置 agent 字段为 "plan" 或 "build" |
| **实验性标记** | 功能目前标记为实验性，需要显式启用 |

---

## 11. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `opencode/packages/opencode/src/agent/agent.ts` | Build Agent 和 Plan Agent 定义 |
| `opencode/packages/opencode/src/tool/plan.ts` | `plan_enter` 和 `plan_exit` 工具 |
| `opencode/packages/opencode/src/flag/flag.ts` | `OPENCODE_EXPERIMENTAL_PLAN_MODE` 功能标志 |
| `opencode/packages/opencode/src/tool/registry.ts` | 条件工具注册 |
| `opencode/packages/opencode/src/session/prompt.ts` | 五阶段规划工作流提示词 |
| `opencode/packages/opencode/src/session/index.ts` | Plan 文件路径生成 |
| `opencode/packages/opencode/src/session/prompt/build-switch.txt` | Build 模式切换提示 |

---

## 12. 使用示例

### 12.1 启用 Plan Mode

```bash
# 设置环境变量
export OPENCODE_EXPERIMENTAL_PLAN_MODE=true

# 启动 OpenCode
opencode
```

### 12.2 典型工作流

1. **进入 Plan Mode**: 调用 `plan_enter` 工具或用户指令
2. **五阶段规划**:
   - Phase 1: 探索代码（最多 3 个 agents 并行）
   - Phase 2: 设计方案
   - Phase 3: 审查计划
   - Phase 4: 写入计划文件
3. **退出 Plan Mode**: 调用 `plan_exit` 工具
4. **执行计划**: 在 Build Mode 下按照计划执行

---

*文档版本: 2026-02-22*
*基于代码版本: opencode (baseline 2026-02-08)*
