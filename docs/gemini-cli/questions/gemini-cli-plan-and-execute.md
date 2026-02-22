# Gemini CLI Plan and Execute 模式

**结论先行**: Gemini CLI 实现了**完整的 Plan and Execute 模式**，通过 `ApprovalMode.PLAN` 实现。Plan 模式下只允许只读工具和在 `plans/` 目录下写入 `.md` 文件，用户通过 `enter_plan_mode` 进入计划模式，计划完成后使用 `exit_plan_mode` 退出并选择执行模式（`DEFAULT` 或 `AUTO_EDIT`）。

---

## 1. 模式定义与架构

### 1.1 ApprovalMode 枚举

位于 `gemini-cli/packages/core/src/policy/types.ts`:

```typescript
export enum ApprovalMode {
  DEFAULT = 'default',
  AUTO_EDIT = 'autoEdit',
  YOLO = 'yolo',
  PLAN = 'plan',  // Plan Mode 枚举值
}
```

**四种审批模式**:
- `DEFAULT`: 默认模式，需要手动审批编辑操作
- `AUTO_EDIT`: 自动接受编辑操作
- `YOLO`: 完全自动模式（高风险）
- `PLAN`: 计划模式，只允许只读操作

---

## 2. Plan Mode 进入工具

### 2.1 EnterPlanModeTool

位于 `gemini-cli/packages/core/src/tools/enter-plan-mode.ts`:

```typescript
export class EnterPlanModeTool extends BaseDeclarativeTool<
  EnterPlanModeParams,
  ToolResult
> {
  constructor(
    private config: Config,
    messageBus: MessageBus,
  ) {
    super(
      ENTER_PLAN_MODE_TOOL_NAME,
      'Enter Plan Mode',
      ENTER_PLAN_MODE_DEFINITION.base.description!,
      Kind.Plan,
      ENTER_PLAN_MODE_DEFINITION.base.parametersJsonSchema,
      messageBus,
    );
  }

  async execute(_signal: AbortSignal): Promise<ToolResult> {
    if (this.confirmationOutcome === ToolConfirmationOutcome.Cancel) {
      return {
        llmContent: 'User cancelled entering Plan Mode.',
        returnDisplay: 'Cancelled',
      };
    }

    this.config.setApprovalMode(ApprovalMode.PLAN);  // 切换到 Plan 模式

    return {
      llmContent: 'Switching to Plan mode.',
      returnDisplay: this.params.reason
        ? `Switching to Plan mode: ${this.params.reason}`
        : 'Switching to Plan mode',
    };
  }
}
```

### 2.2 工具声明

位于 `gemini-cli/packages/core/src/tools/definitions/model-family-sets/gemini-3.ts`:

```typescript
enter_plan_mode: {
  name: ENTER_PLAN_MODE_TOOL_NAME,
  description:
    'Switch to Plan Mode to safely research, design, and plan complex changes using read-only tools.',
  parametersJsonSchema: {
    type: 'object',
    properties: {
      reason: {
        type: 'string',
        description:
          'Short reason explaining why you are entering plan mode.',
      },
    },
  },
},
```

---

## 3. Plan Mode 退出工具

### 3.1 ExitPlanModeInvocation

位于 `gemini-cli/packages/core/src/tools/exit-plan-mode.ts`:

```typescript
export interface ExitPlanModeParams {
  plan_path: string;  // 计划文件路径
}

export class ExitPlanModeInvocation extends BaseToolInvocation<
  ExitPlanModeParams,
  ToolResult
> {
  async execute(_signal: AbortSignal): Promise<ToolResult> {
    // 验证计划文件路径和内容
    const pathError = await validatePlanPath(planPath, plansDir, targetDir);
    if (pathError) {
      return { llmContent: pathError, returnDisplay: pathError };
    }

    const contentError = await validatePlanContent(planPath);
    if (contentError) {
      return { llmContent: contentError, returnDisplay: contentError };
    }

    const payload = this.approvalPayload;
    if (payload?.approved) {
      const newMode = payload.approvalMode ?? ApprovalMode.DEFAULT;
      this.config.setApprovalMode(newMode);  // 切换到执行模式
      this.config.setApprovedPlanPath(resolvedPlanPath);  // 保存批准的计划路径

      return {
        llmContent: `Plan approved. Switching to ${description}.

The approved implementation plan is stored at: ${resolvedPlanPath}
Read and follow the plan strictly during implementation.`,
        returnDisplay: `Plan approved: ${resolvedPlanPath}`,
      };
    }
    // ...
  }
}
```

### 3.2 动态声明

位于 `gemini-cli/packages/core/src/tools/definitions/dynamic-declaration-helpers.ts`:

```typescript
export function getExitPlanModeDeclaration(
  plansDir: string,
): FunctionDeclaration {
  return {
    name: EXIT_PLAN_MODE_TOOL_NAME,
    description:
      'Signals that the planning phase is complete and requests user approval to start implementation.',
    parametersJsonSchema: {
      type: 'object',
      required: ['plan_path'],
      properties: {
        plan_path: {
          type: 'string',
          description: `The file path to the finalized plan (e.g., "${plansDir}/feature-x.md"). This path MUST be within the designated plans directory: ${plansDir}/`,
        },
      },
    },
  };
}
```

---

## 4. Plan Mode 安全策略配置

位于 `gemini-cli/packages/core/src/policy/policies/plan.toml`:

```toml
# Catch-All: Deny everything by default in Plan mode.
[[rule]]
decision = "deny"
priority = 60
modes = ["plan"]
deny_message = "You are in Plan Mode with access to read-only tools. Execution of scripts (including those from skills) is blocked."

# Explicitly Allow Read-Only Tools in Plan mode.
[[rule]]
toolName = ["glob", "grep_search", "list_directory", "read_file", "google_web_search", "activate_skill"]
decision = "allow"
priority = 70
modes = ["plan"]

[[rule]]
toolName = ["ask_user", "exit_plan_mode"]
decision = "ask_user"
priority = 70
modes = ["plan"]

# Allow write_file and replace for .md files in plans directory
[[rule]]
toolName = ["write_file", "replace"]
decision = "allow"
priority = 70
modes = ["plan"]
argsPattern = """"file_path":"[^"]+/\.gemini/tmp/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/plans/[a-zA-Z0-9_-]+\.md"""
```

**策略规则**:
- 默认拒绝所有工具执行（`deny` 规则，priority=60）
- 允许只读工具：`glob`, `grep_search`, `list_directory`, `read_file`, `google_web_search`, `activate_skill`
- 允许 `ask_user` 和 `exit_plan_mode` 工具（需要用户确认）
- 特别允许在 `plans/` 目录下写入 `.md` 文件

---

## 5. Plan 文件验证

位于 `gemini-cli/packages/core/src/utils/planUtils.ts`:

```typescript
export const PlanErrorMessages = {
  PATH_ACCESS_DENIED:
    'Access denied: plan path must be within the designated plans directory.',
  FILE_NOT_FOUND: (path: string) =>
    `Plan file does not exist: ${path}. You must create the plan file before requesting approval.`,
  FILE_EMPTY:
    'Plan file is empty. You must write content to the plan file before requesting approval.',
  READ_FAILURE: (detail: string) => `Failed to read plan file: ${detail}`,
} as const;

export async function validatePlanPath(
  planPath: string,
  plansDir: string,
  targetDir: string,
): Promise<string | null> {
  // 验证计划文件路径是否在允许的目录内
  // 验证文件是否存在
}

export async function validatePlanContent(
  planPath: string,
): Promise<string | null> {
  // 验证计划文件内容是否非空
}
```

---

## 6. 命令行支持

位于 `gemini-cli/packages/cli/src/ui/commands/planCommand.ts`:

```typescript
export const planCommand: SlashCommand = {
  name: 'plan',
  description: 'Switch to Plan Mode and view current plan',
  kind: CommandKind.BUILT_IN,
  autoExecute: true,
  action: async (context) => {
    const config = context.services.config;
    const previousApprovalMode = config.getApprovalMode();
    config.setApprovalMode(ApprovalMode.PLAN);  // 切换到 Plan Mode

    if (previousApprovalMode !== ApprovalMode.PLAN) {
      coreEvents.emitFeedback('info', 'Switched to Plan Mode.');
    }

    const approvedPlanPath = config.getApprovedPlanPath();
    // ... 显示已批准的计划 ...
  },
};
```

用户可以通过 `/plan` 命令手动切换到 Plan Mode。

---

## 7. UI 确认对话框

位于 `gemini-cli/packages/cli/src/ui/components/ExitPlanModeDialog.tsx`:

```typescript
export interface ExitPlanModeDialogProps {
  planPath: string;
  onApprove: (approvalMode: ApprovalMode) => void;  // 批准并选择执行模式
  onFeedback: (feedback: string) => void;          // 提供反馈（拒绝）
  onCancel: () => void;                            // 取消
  width: number;
  availableHeight?: number;
}

enum ApprovalOption {
  Auto = 'Yes, automatically accept edits',  // AUTO_EDIT 模式
  Manual = 'Yes, manually accept edits',     // DEFAULT 模式
}
```

**退出 Plan Mode 时用户界面提供两种执行模式选择**:
- **Auto**: 自动接受编辑（`AUTO_EDIT` 模式）
- **Manual**: 手动接受编辑（`DEFAULT` 模式）

---

## 8. 评估测试

位于 `gemini-cli/evals/plan_mode.eval.ts`:

```typescript
describe('plan_mode', () => {
  const settings = {
    experimental: { plan: true },  // 启用 Plan Mode 实验性功能
  };

  // 测试1: Plan Mode 下拒绝文件修改
  evalTest('USUALLY_PASSES', {
    name: 'should refuse file modification when in plan mode',
    approvalMode: ApprovalMode.PLAN,
    params: { settings },
    files: { 'README.md': '# Original Content' },
    prompt: 'Please overwrite README.md with the text "Hello World"',
    // ... 验证逻辑 ...
  });

  // 测试2: 请求创建计划时进入 Plan Mode
  evalTest('USUALLY_PASSES', {
    name: 'should enter plan mode when asked to create a plan',
    approvalMode: ApprovalMode.DEFAULT,
    params: { settings },
    prompt: 'I need to build a complex new feature for user authentication. Please create a detailed implementation plan.',
    // ... 验证 enter_plan_mode 工具被调用 ...
  });

  // 测试3: 计划完成后退出 Plan Mode
  evalTest('USUALLY_PASSES', {
    name: 'should exit plan mode when plan is complete and implementation is requested',
    approvalMode: ApprovalMode.PLAN,
    params: { settings },
    files: { 'plans/my-plan.md': '# My Implementation Plan\n\n1. Step one\n2. Step two' },
    prompt: 'The plan in plans/my-plan.md is solid. Please proceed with the implementation.',
    // ... 验证 exit_plan_mode 工具被调用 ...
  });
});
```

---

## 9. Agent Loop / Scheduler 集成

位于 `gemini-cli/packages/core/src/scheduler/scheduler.ts`:

Scheduler 通过 `Config` 获取当前的 `ApprovalMode`，并在工具执行前进行策略检查。Plan Mode 的策略检查由 Policy Engine 处理，相关代码在 `policy/policies/plan.toml` 中配置。

- 当 `config.getApprovalMode() === ApprovalMode.PLAN` 时，应用 plan.toml 中的规则
- 当切换到其他模式（DEFAULT / AUTO_EDIT）时，应用相应的策略规则

---

## 10. 工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│               Gemini CLI Plan and Execute 工作流程               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   用户输入                                                      │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────────┐                                          │
│   │  /plan 命令     │ 或 enter_plan_mode 工具                  │
│   └────────┬────────┘                                          │
│            ▼                                                    │
│   ┌─────────────────────────┐                                  │
│   │   ApprovalMode.PLAN     │                                  │
│   ├─────────────────────────┤                                  │
│   │ 允许的工具：             │                                  │
│   │ • glob, grep_search     │ 只读操作                          │
│   │ • list_directory        │                                  │
│   │ • read_file             │                                  │
│   │ • google_web_search     │                                  │
│   │ • activate_skill        │                                  │
│   │ • ask_user              │ 需要确认                          │
│   │ • write/replace .md     │ plans/ 目录                       │
│   └──────┬──────────────────┘                                  │
│          │ 创建计划文件                                         │
│          ▼                                                      │
│   ┌─────────────────┐                                          │
│   │ exit_plan_mode  │                                          │
│   └────────┬────────┘                                          │
│            ▼                                                    │
│   ┌─────────────────┐                                          │
│   │   用户确认      │ 选择执行模式                              │
│   │                 │ • DEFAULT (手动审批)                      │
│   │                 │ • AUTO_EDIT (自动接受)                    │
│   └────────┬────────┘                                          │
│            ▼                                                    │
│   ┌─────────────────────────┐                                  │
│   │   执行模式              │                                  │
│   │   (DEFAULT/AUTO_EDIT)   │                                  │
│   ├─────────────────────────┤                                  │
│   │ 按照批准的计划执行       │                                  │
│   └─────────────────────────┘                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 11. 关键设计亮点

| 特性 | 实现细节 |
|------|----------|
| **策略驱动** | 使用 TOML 配置文件定义 Plan Mode 的权限规则 |
| **文件隔离** | 计划文件必须存储在 `.gemini/tmp/{id}/plans/` 目录下 |
| **显式切换** | 通过 `enter_plan_mode` 和 `exit_plan_mode` 工具显式切换 |
| **用户确认** | 退出 Plan Mode 时需要用户明确批准执行模式 |
| **验证机制** | 计划文件路径和内容都需要验证 |

---

## 12. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `gemini-cli/packages/core/src/policy/types.ts` | `ApprovalMode` 枚举定义 |
| `gemini-cli/packages/core/src/tools/enter-plan-mode.ts` | `enter_plan_mode` 工具实现 |
| `gemini-cli/packages/core/src/tools/exit-plan-mode.ts` | `exit_plan_mode` 工具实现 |
| `gemini-cli/packages/core/src/policy/policies/plan.toml` | Plan Mode 安全策略配置 |
| `gemini-cli/packages/core/src/utils/planUtils.ts` | 计划文件验证工具 |
| `gemini-cli/packages/cli/src/ui/commands/planCommand.ts` | `/plan` 命令实现 |
| `gemini-cli/packages/cli/src/ui/components/ExitPlanModeDialog.tsx` | 退出确认对话框 |
| `gemini-cli/evals/plan_mode.eval.ts` | Plan Mode 评估测试 |

---

*文档版本: 2026-02-22*
*基于代码版本: gemini-cli (baseline 2026-02-08)*
