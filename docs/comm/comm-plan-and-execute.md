# Plan and Execute 模式跨项目对比

## 引子：当 Agent 需要"三思而后行"

想象一下这个场景：你让 AI Agent 实现一个复杂的新功能。它立刻开始修改代码，删除了你的核心文件，然后又发现思路不对，留下一片狼藉...

这正是 **Plan and Execute 模式** 要解决的问题。就像人类工程师在动手前会先画设计图、写技术方案一样，Agent 也需要先制定计划，再执行实施。

但不同 Agent 对这个问题的回答截然不同：
- **Codex/Gemini CLI/OpenCode**: "我们先进入 Plan Mode，制定详细方案，确认后再执行"
- **Kimi CLI**: "我们预设好流程图，按步骤执行，支持自动迭代"
- **SWE-agent**: "每步都重新思考，不需要预设计划"

理解这些差异，能帮助你在自己的 AI Agent 项目中做出正确选择。

---

## 一句话总结

本文对比 5 个主流 AI Coding Agent 的 Plan and Execute 实现，从模式切换机制、数据流转、Context 管理、权限隔离等维度分析差异，揭示"先计划后执行" vs "边计划边执行" 的设计哲学。

---

## 快速参考

### 5 个 Agent 核心差异一览

| Agent | 架构模式 | Plan 机制 | 权限隔离 | 计划存储 | 适用场景 |
|-------|---------|----------|---------|---------|---------|
| **Codex** | 🔧 配置驱动 | `ModeKind::Plan` | TurnContext 判断 | 消息流 XML | 严格预规划的大型功能 |
| **Gemini CLI** | 🔧 配置驱动 | `ApprovalMode.PLAN` | Policy Engine (TOML) | Markdown 文件 | 需灵活策略配置的任务 |
| **OpenCode** | 🔄 Agent 切换 | Plan/Build Agent | PermissionNext | Markdown 文件 | 需并行探索的复杂任务 |
| **Kimi CLI** | 📋 工作流编排 | Agent Flow | Flow 节点控制 | Flow 图定义 | 需循环/分支的复杂工作流 |
| **SWE-agent** | ❌ 无模式 | Thought-Action | 无隔离 | thought 隐式 | 探索性 Bug 修复 |

### 决策树：选择适合你场景的架构

```
你的任务需要什么？
    │
    ├── 需要严格"先计划后执行"？
    │       │
    │       ├── 是 → 需要文件持久化计划？
    │       │           │
    │       │           ├── 是 → 选择 Gemini CLI 模式 ✅
    │       │           │
    │       │           └── 否 → 选择 Codex 模式 ✅
    │       │
    │       └── 否 → 需要 Agent 类型分离？
    │                   │
    │                   ├── 是 → 选择 OpenCode 模式 ✅
    │                   │
    │                   └── 否 → 流程是否预设？
    │                               │
    │                               ├── 是 → 选择 Kimi CLI 模式 ✅
    │                               │
    │                               └── 否 → 选择 SWE-agent 模式 ✅
    │
    └── 不需要 → 简单循环足够 → SWE-agent 模式 ✅
```

### 三种架构模式速查

| 模式 | 代表 Agent | 核心特点 | 最佳实践 |
|-----|-----------|---------|---------|
| **配置驱动** | Codex, Gemini CLI | 同一线程，配置切换权限 | 使用枚举区分模式，策略文件控制工具权限 |
| **Agent 切换** | OpenCode | 不同类型 Agent，合成消息切换 | Plan Agent 只读，Build Agent 全权限 |
| **工作流编排** | Kimi CLI | 预定义 Flow 图，节点遍历执行 | 定义 begin/task/decision/end 节点类型 |
| **无模式** | SWE-agent | Thought-Action 统一循环 | 模板引导，每步重新评估 |

---

## 1. 概念定义

### 1.1 什么是 Plan and Execute 模式

**Plan and Execute（计划与执行）** 是一种将任务处理分为两个阶段的架构模式：

- **Plan Phase（计划阶段）**: 只读探索，制定详细实施方案
- **Execute Phase（执行阶段）**: 按照计划执行修改操作

### 1.2 核心设计问题

| 问题 | 设计选择 |
|-----|---------|
| 如何切换阶段？ | 配置切换 / Agent 切换 / 工具触发 |
| 计划如何存储？ | XML 标签 / Markdown 文件 / 内存中 |
| 权限如何隔离？ | 策略配置 / 权限系统 / 无隔离 |
| Context 如何流转？ | 共享上下文 / 阶段重置 / 持续累积 |

---

## 2. 三种架构模式详解

### 2.1 配置驱动模式 —— Codex & Gemini CLI

**核心思想**：在同一线程/会话中，通过配置切换来改变 Agent 行为模式。

#### Codex 实现

```
用户输入
    │
    ▼
┌─────────────────────────────┐
│  CollaborationModeMask      │
│  ├── mode: ModeKind::Plan   │
│  ├── model: Option<Model>   │
│  └── developer_instructions │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│       Agent Loop            │
│  ┌───────────────────────┐  │
│  │ TurnContext           │  │
│  │ └── collaboration_mode│  │ ← 模式判断依据
│  └───────────────────────┘  │
            │
            ▼
┌─────────────────────────────┐
│      Tool Router            │
│  if mode == Plan:           │
│    → 只允许只读工具          │
│  else:                      │
│    → 允许所有工具            │
└─────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 说明 |
|-----|---------|------|
| ModeKind | `codex-rs/protocol/src/config_types.rs` | 模式枚举定义 |
| Plan 模板 | `codex-rs/core/templates/collaboration_mode/plan.md` | 三阶段工作流提示词 |
| 计划解析 | `codex-rs/core/src/proposed_plan_parser.rs` | XML 标签块解析 |
| 工具限制 | `codex-rs/core/src/tools/handlers/plan.rs` | Plan 模式下禁用 update_plan |

#### Gemini CLI 实现

```
用户输入 / enter_plan_mode
    │
    ▼
┌─────────────────────────────┐
│   Config.approval_mode      │
│   = ApprovalMode.PLAN       │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│       Scheduler             │
│  ┌───────────────────────┐  │
│  │ Policy Engine         │  │
│  │ ├── plan.toml (rules) │  │
│  │ └── decision: allow/  │  │
│  │       deny/ask_user   │  │
│  └───────────────────────┘  │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│      Tool Execution         │
│  Plan 模式允许:             │
│  • 只读工具 (glob, grep...) │
│  • plans/*.md 写入          │
│  • ask_user, exit_plan_mode │
└─────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 说明 |
|-----|---------|------|
| ApprovalMode | `packages/core/src/policy/types.ts` | 模式枚举 |
| 进入工具 | `packages/core/src/tools/enter-plan-mode.ts` | enter_plan_mode 实现 |
| 退出工具 | `packages/core/src/tools/exit-plan-mode.ts` | exit_plan_mode 实现 |
| 策略配置 | `packages/core/src/policy/policies/plan.toml` | Plan 模式权限规则 |
| 计划验证 | `packages/core/src/utils/planUtils.ts` | 路径和内容验证 |

#### 配置驱动模式对比

| 特性 | Codex | Gemini CLI |
|-----|-------|-----------|
| **配置方式** | `ModeKind` 枚举 | `ApprovalMode` + TOML |
| **切换机制** | `/plan` 命令 | `enter_plan_mode` / `exit_plan_mode` 工具 |
| **权限控制** | TurnContext 判断 | Policy Engine 决策 |
| **计划存储** | XML 标签 (内存) | Markdown 文件 (持久化) |
| **Context 流转** | 配置切换，Context 延续 | Config + 文件 |

---

### 2.2 Agent 切换模式 —— OpenCode

**核心思想**：通过切换不同类型的 Agent 来实现模式分离，Plan Agent 和 Build Agent 有完全不同的权限配置。

```
用户输入 / plan_enter
    │
    ▼
┌─────────────────────────────┐
│  合成消息 {agent: "plan"}   │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│      Session                │
│  ┌───────────────────────┐  │
│  │ Current Agent: plan   │  │
│  │ Permission:           │  │
│  │   edit.* = deny       │  │
│  │   edit.plans/*.md =   │  │
│  │            allow      │  │
│  └───────────────────────┘  │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│    Plan Agent Execution     │
│  五阶段工作流:              │
│  1. Initial Understanding   │
│  2. Design                  │
│  3. Review                  │
│  4. Final Plan              │
│  5. plan_exit               │
└─────────────────────────────┘
```

**Context 流转与 Agent 切换**

```
Build Agent Context              切换点                Plan Agent Context
┌─────────────────┐                                    ┌─────────────────┐
│ agent: "build"  │                                    │ agent: "plan"   │
│ 全权限          │ ──plan_enter─────────────────────▶ │ 只读权限        │
│                 │    合成消息切换                     │                 │
│ 执行历史         │                                    │ 探索结果         │
└─────────────────┘                                    └─────────────────┘
                                                           │
                                                           │ 写入
                                                           ▼
                                                    ┌───────────────┐
                                                    │ .opencode/    │
                                                    │ plans/*.md    │
                                                    └───────────────┘
                                                           │
                           切换点                          │
┌─────────────────┐         │                              │
│ agent: "build"  │ ◀────plan_exit─────────────────────────┘
│ 全权限恢复       │    合成消息切换
│ 读取计划         │
│ 开始执行         │
└─────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 说明 |
|-----|---------|------|
| Agent 定义 | `packages/opencode/src/agent/agent.ts` | build/plan Agent 定义 |
| Plan 工具 | `packages/opencode/src/tool/plan.ts` | plan_enter/exit 工具 |
| 功能标志 | `packages/opencode/src/flag/flag.ts` | OPENCODE_EXPERIMENTAL_PLAN_MODE |
| 五阶段提示 | `packages/opencode/src/session/prompt.ts` | 工作流提示词 |

---

### 2.3 工作流编排模式 —— Kimi CLI

**核心思想**：使用预定义的 Flow 图（节点和边）来编排工作流，而非显式的 Plan/Execute 阶段分离。

```
用户输入 /flow:name
    │
    ▼
┌─────────────────────────────┐
│     FlowRunner              │
│  ┌───────────────────────┐  │
│  │ flow: Flow            │  │
│  │ ├── nodes: begin/task/│  │
│  │ │        decision/end │  │
│  │ └── edges: 转移边     │  │
│  └───────────────────────┘  │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│   遍历执行 (max_moves)      │
│                             │
│   BEGIN → task → DECISION ──┼── CONTINUE
│              │              │    (循环)
│              ▼              │
│            STOP → END       │
└─────────────────────────────┘
```

**Ralph 模式（自动迭代）**

```
用户输入
    │
    ▼
┌─────────────────────────────┐
│  Ralph Loop                 │
│  max_ralph_iterations       │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ R1: 执行用户 prompt         │
│     获取 observation        │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐
│ R2: 决策节点                │
│  ┌───────────────────────┐  │
│  │ CONTINUE? / STOP?     │  │
│  └───────────────────────┘  │
└───────────┬─────────────────┘
            │
      ┌─────┴─────┐
      ▼           ▼
 CONTINUE       STOP
    │            │
    └─────┬──────┘
          ▼
    ┌───────────┐
    │ END       │
    └───────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 说明 |
|-----|---------|------|
| FlowRunner | `src/kimi_cli/soul/kimisoul.py` | 流程执行器 |
| Flow 定义 | `src/kimi_cli/skill/flow/__init__.py` | FlowNode, FlowNodeKind |
| Ralph Loop | `src/kimi_cli/soul/kimisoul.py` | 自动迭代模式 |
| 技能系统 | `src/kimi_cli/skill/__init__.py` | Skill, SkillType |

---

### 2.4 无模式模式 —— SWE-agent

**核心思想**：没有显式的 Plan/Execute 分离，规划和执行在每个步骤中通过 Thought-Action 循环完成。

```
Problem Statement
    │
    ▼
┌─────────────────────────────┐
│   Template Injection        │
│   • system_template         │
│   • instance_template       │
│   • next_step_template      │
└───────────┬─────────────────┘
            │
            ▼
┌─────────────────────────────┐     ┌─────────────────┐
│       Agent Loop            │     │  Retry Loop     │
│  ┌───────────────────────┐  │     │ (可选)          │
│  │ Step:                 │  │     │ 多次尝试        │
│  │ 1. Model Query        │  │     │ 选择最佳        │
│  │ 2. Parse Thought      │  │     └─────────────────┘
│  │ 3. Parse Action       │  │
│  │ 4. Execute            │  │
│  │ 5. Observation        │  │
│  └───────────────────────┘  │
│            │                │
│            └────────────────┤
│                             │
│   ┌─────────────────────┐   │
│   │ Trajectory (轨迹)   │   │
│   │ 记录每步 thought/   │   │
│   │ action/observation  │   │
│   └─────────────────────┘   │
└─────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 说明 |
|-----|---------|------|
| Agent Loop | `sweagent/agent/agents.py` | step(), run() |
| Thought-Action | `sweagent/tools/parsing.py` | ThoughtActionParser |
| 模板配置 | `sweagent/agent/agents.py` | TemplateConfig |
| 默认配置 | `config/default.yaml` | instance_template |

---

## 3. 深度对比

### 3.1 Context 流转对比

| Agent | Plan Phase Context | Execute Phase Context | 隔离方式 |
|-------|-------------------|----------------------|---------|
| **Codex** | 同一线程，共享 | 同一线程，共享 | 配置切换，Context 延续 |
| **Gemini CLI** | 共享内存 + 文件 | 共享内存，读取文件 | Config 切换，文件持久化 |
| **OpenCode** | Plan Agent | Build Agent | Agent 切换，合成消息 |
| **Kimi CLI** | Flow 节点间传递 | - | 节点遍历，局部变量 |
| **SWE-agent** | thought（隐式） | thought + action | 无隔离，累积式 |

**Context 持久化可视化**

```
Codex:
┌──────────────┐     ┌──────────────┐
│ 内存 Context  │ ──▶ │  内存 Context  │
└──────────────┘     └──────────────┘
  (Plan)               (Execute)
  共享所有信息

Gemini CLI:
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 内存 Context  │ ──▶ │ {plan}.md    │ ──▶ │ 内存 Context  │
└──────────────┘     └──────────────┘     └──────────────┘
  (Plan)               文件持久化          (Execute)

OpenCode:
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Plan Agent   │ ──▶ │ .opencode/   │ ──▶ │ Build Agent  │
│   Context    │     │ plans/*.md   │     │   Context    │
└──────────────┘     └──────────────┘     └──────────────┘

Kimi CLI:
┌──────────────┐     ┌──────────────┐
│ Node Context │ ──▶ │ Node Context │
└──────────────┘     └──────────────┘
  节点间传递，无全局持久化

SWE-agent:
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Step N       │ ──▶ │ Trajectory   │ ──▶ │ Step N+1     │
│ Context      │     │ (累积历史)    │     │ Context      │
└──────────────┘     └──────────────┘     └──────────────┘
```

### 3.2 关键代码位置汇总

#### Plan and Execute 核心实现

| Agent | 模式定义 | 进入工具 | 退出工具 | 权限控制 |
|-------|---------|---------|---------|---------|
| **Codex** | `config_types.rs:ModeKind` | `/plan` 命令 | `/plan` 切换 | TurnContext 判断 |
| **Gemini CLI** | `types.ts:ApprovalMode` | `enter-plan-mode.ts` | `exit-plan-mode.ts` | Policy Engine |
| **OpenCode** | `agent.ts:AgentConfig` | `plan.ts:PlanEnterTool` | `plan.ts:PlanExitTool` | PermissionNext |

#### 计划存储格式

| Agent | 存储位置 | 格式 | 解析方式 |
|-------|---------|------|---------|
| **Codex** | 消息流中 | `<proposed_plan>` XML | 流式解析器 |
| **Gemini CLI** | `.gemini/tmp/*/plans/*.md` | Markdown | 文件读取 |
| **OpenCode** | `.opencode/plans/*.md` | Markdown | 文件读取 |
| **Kimi CLI** | SKILL.md + Flow 图 | Mermaid/D2 | FlowRunner |
| **SWE-agent** | thought 中 | 自由文本 | 无结构化 |

#### 完整文件路径

| Agent | 核心文件 | 说明 |
|-------|---------|------|
| **Codex** | `codex/codex-rs/protocol/src/config_types.rs` | ModeKind 枚举 |
| | `codex/codex-rs/core/templates/collaboration_mode/plan.md` | 三阶段提示词 |
| | `codex/codex-rs/core/src/proposed_plan_parser.rs` | XML 解析 |
| **Gemini CLI** | `gemini-cli/packages/core/src/policy/types.ts` | ApprovalMode |
| | `gemini-cli/packages/core/src/tools/enter-plan-mode.ts` | 进入工具 |
| | `gemini-cli/packages/core/src/tools/exit-plan-mode.ts` | 退出工具 |
| | `gemini-cli/packages/core/src/policy/policies/plan.toml` | 策略配置 |
| **OpenCode** | `opencode/packages/opencode/src/agent/agent.ts` | Agent 定义 |
| | `opencode/packages/opencode/src/tool/plan.ts` | Plan 工具 |
| | `opencode/packages/opencode/src/flag/flag.ts` | 功能标志 |
| **Kimi CLI** | `kimi-cli/src/kimi_cli/soul/kimisoul.py` | FlowRunner |
| | `kimi-cli/src/kimi_cli/skill/flow/__init__.py` | Flow 定义 |
| **SWE-agent** | `SWE-agent/sweagent/agent/agents.py` | Agent Loop |
| | `SWE-agent/sweagent/tools/parsing.py` | Thought-Action 解析 |

---

## 4. 总结对比表

### 4.1 核心特性对比

| 特性 | Codex | Gemini CLI | OpenCode | Kimi CLI | SWE-agent |
|-----|-------|-----------|----------|----------|-----------|
| **Plan 模式** | ✅ ModeKind::Plan | ✅ ApprovalMode.PLAN | ✅ Plan Agent | ❌ Flow 编排 | ❌ 无 |
| **Execute 模式** | ✅ ModeKind::Default | ✅ DEFAULT/AUTO_EDIT | ✅ Build Agent | ❌ Flow 执行 | ❌ 统一循环 |
| **显式切换** | ✅ `/plan` 命令 | ✅ enter/exit 工具 | ✅ plan_enter/exit | ❌ `/flow` 触发 | ❌ N/A |
| **权限隔离** | ✅ TurnContext 判断 | ✅ TOML 策略 | ✅ PermissionNext | ⚠️ Flow 节点控制 | ❌ 无隔离 |
| **计划持久化** | ❌ 消息流中 | ✅ Markdown 文件 | ✅ Markdown 文件 | ✅ Flow 图定义 | ❌ thought 中 |
| **Context 流转** | 配置切换延续 | Config + 文件 | Agent 切换 | 节点遍历 | 累积式 |

### 4.2 适用场景

| Agent | 最佳场景 | 避免场景 |
|-------|---------|---------|
| **Codex** | 需要严格预规划的大型功能 | 探索性任务 |
| **Gemini CLI** | 需要用户确认策略灵活配置 | 简单任务 |
| **OpenCode** | 需要并行探索的复杂任务 | 小型快速修改 |
| **Kimi CLI** | 需要循环/分支的复杂工作流 | 简单线性任务 |
| **SWE-agent** | Bug 修复等探索性任务 | 需要严格步骤的任务 |

### 4.3 设计哲学

| 设计 | Agent | 说明 |
|-----|-------|------|
| **先计划后执行** | Codex, Gemini CLI, OpenCode | 安全第一，降低风险 |
| **工作流编排** | Kimi CLI | 预设流程，自动执行 |
| **边计划边执行** | SWE-agent | 灵活探索，适应变化 |

---

## 5. 实现建议

### 5.1 如果你想在自己的 Agent 中实现 Plan and Execute

**方案 A: 配置驱动（推荐用于生产环境）**

```typescript
// 类似 Codex/Gemini CLI 的方式
enum Mode {
  PLAN = 'plan',      // 只读模式
  EXECUTE = 'execute' // 完整权限
}

class Agent {
  private mode: Mode = Mode.EXECUTE;

  async executeTool(tool: Tool) {
    if (this.mode === Mode.PLAN && tool.isMutating()) {
      throw new Error('Cannot execute mutating tool in Plan mode');
    }
    return tool.execute();
  }
}
```

**方案 B: Agent 切换（适合复杂 Agent 系统）**

```typescript
// 类似 OpenCode 的方式
class PlanAgent extends Agent {
  permissions = { edit: 'deny', read: 'allow' };
}

class ExecuteAgent extends Agent {
  permissions = { edit: 'allow', read: 'allow' };
}
```

**方案 C: 工作流编排（适合预设流程）**

```typescript
// 类似 Kimi CLI 的方式
interface FlowNode {
  id: string;
  type: 'task' | 'decision' | 'end';
  execute: () => Promise<NextNodeId>;
}
```

### 5.2 关键决策点

1. **是否需要严格隔离？** → 选择配置驱动或 Agent 切换
2. **流程是否预设？** → 选择工作流编排
3. **是否需要用户确认？** → 选择显式 enter/exit 工具
4. **Context 如何传递？** → 文件持久化 vs 内存共享

---

*文档版本: 2026-02-22*
