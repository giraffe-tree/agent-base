# Codex Plan and Execute 模式

**结论先行**: Codex 实现了**生产级的 Plan and Execute 模式**，通过 `ModeKind` 枚举严格区分 Plan 模式和 Default 模式。Plan 模式下禁止任何文件修改操作，只允许读取和探索，计划通过 `<proposed_plan>` XML 标签块输出，用户确认后切换到 Default 模式执行。这是真正的"先计划后执行"工作流。

---

## 1. 模式定义与架构

### 1.1 ModeKind 枚举

位于 `codex/codex-rs/protocol/src/config_types.rs`:

```rust
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, JsonSchema, TS, Default)]
#[serde(rename_all = "snake_case")]
pub enum ModeKind {
    Plan,
    #[default]
    #[serde(
        alias = "code",
        alias = "pair_programming",
        alias = "execute",
        alias = "custom"
    )]
    Default,
    PairProgramming,
    Execute,
}

pub const TUI_VISIBLE_COLLABORATION_MODES: [ModeKind; 2] = [ModeKind::Default, ModeKind::Plan];
```

**关键设计**:
- `Plan`: 专门用于制定详细计划，禁止执行变更操作
- `Default`: 默认执行模式，允许实际编码和文件修改
- TUI 仅显示 Plan 和 Default 两种模式，其他模式为隐藏模式

---

## 2. Plan 模式三阶段工作流

### 2.1 模式提示词模板

位于 `codex/codex-rs/core/templates/collaboration_mode/plan.md`:

```markdown
## PHASE 1 — Ground in the environment (探索优先)
- 通过探索环境而非询问用户来消除未知
- 执行非变更性探索命令（读取文件、搜索、检查配置）

## PHASE 2 — Intent chat (明确意图)
- 询问目标、成功标准、范围、约束条件
- 使用 request_user_input 工具进行关键决策

## PHASE 3 — Implementation chat (完善方案)
- 确定实现细节：接口、数据流、边界情况、测试标准
- 生成决策完整的计划（decision complete）

## Finalization rule
- 仅当计划决策完整时才输出 <proposed_plan> 块
- 计划必须包含：标题、摘要、API 变更、测试场景、明确假设
```

### 2.2 操作权限隔离

| 操作类型 | Plan 模式 | Default 模式 |
|---------|----------|-------------|
| 读取文件 | 允许 | 允许 |
| 搜索代码 | 允许 | 允许 |
| 静态分析 | 允许 | 允许 |
| 测试构建 | 允许 | 允许 |
| 编辑文件 | **禁止** | 允许 |
| 运行格式化 | **禁止** | 允许 |
| 应用补丁 | **禁止** | 允许 |
| 执行副作用命令 | **禁止** | 允许 |

---

## 3. 计划输出格式

### 3.1 XML 标签块解析

位于 `codex/codex-rs/core/src/proposed_plan_parser.rs`:

```rust
const OPEN_TAG: &str = "<proposed_plan>";
const CLOSE_TAG: &str = "</proposed_plan>";

pub struct ProposedPlanParser {
    // 流式解析 <proposed_plan> 标签块
}

pub struct ProposedPlanSegment {
    pub items: Vec<ProposedPlanItem>,
}
```

### 3.2 计划内容示例

```xml
<proposed_plan>
# 功能实现计划：用户认证模块

## 摘要
实现基于 JWT 的用户认证系统

## API 变更
- POST /api/auth/login - 用户登录
- POST /api/auth/register - 用户注册
- GET /api/auth/me - 获取当前用户

## 测试场景
1. 正常登录流程
2. 错误密码处理
3. Token 过期处理

## 假设
- 使用现有的 User 数据表
- JWT secret 已配置在环境变量
</proposed_plan>
```

---

## 4. Plan 工具（update_plan）

### 4.1 工具定义

位于 `codex/codex-rs/core/src/tools/handlers/plan.rs`:

```rust
pub static PLAN_TOOL: LazyLock<ToolSpec> = LazyLock::new(|| {
    ToolSpec::Function(ResponsesApiTool {
        name: "update_plan".to_string(),
        description: r#"Updates the task plan.
Provide an optional explanation and a list of plan items, each with a step and status.
At most one step can be in_progress at a time."#,
        // ...
    })
});
```

### 4.2 Plan 模式下的工具限制

```rust
if turn_context.collaboration_mode.mode == ModeKind::Plan {
    return Err(FunctionCallError::RespondToModel(
        "update_plan is a TODO/checklist tool and is not allowed in Plan mode".to_string(),
    ));
}
```

**重要区别**: Plan 模式 与 `update_plan` 工具是完全独立的机制。Plan 模式用于制定计划，`update_plan` 用于在执行过程中跟踪任务进度。

---

## 5. 用户界面集成

### 5.1 斜杠命令

位于 `codex/codex-rs/tui/src/slash_command.rs`:

```rust
pub enum SlashCommand {
    Plan,  // 切换到 Plan 模式
    Collab, // 更改协作模式
    // ...
}

impl SlashCommand {
    pub fn description(self) -> &'static str {
        match self {
            SlashCommand::Plan => "switch to Plan mode",
            // ...
        }
    }
}
```

用户可以通过 `/plan` 命令快速切换到 Plan 模式。

### 5.2 模式切换逻辑

位于 `codex/codex-rs/tui/src/collaboration_modes.rs`:

```rust
pub(crate) fn plan_mask(models_manager: &ModelsManager) -> Option<CollaborationModeMask> {
    mask_for_kind(models_manager, ModeKind::Plan)
}

pub(crate) fn next_mask(
    models_manager: &ModelsManager,
    current: Option<&CollaborationModeMask>,
) -> Option<CollaborationModeMask> {
    // 在可用模式间循环切换
}
```

---

## 6. 模式预设配置

位于 `codex/codex-rs/core/src/models_manager/collaboration_mode_presets.rs`:

```rust
fn plan_preset() -> CollaborationModeMask {
    CollaborationModeMask {
        name: ModeKind::Plan.display_name().to_string(),
        mode: Some(ModeKind::Plan),
        model: None,
        reasoning_effort: Some(Some(ReasoningEffort::Medium)),
        developer_instructions: Some(Some(COLLABORATION_MODE_PLAN.to_string())),
    }
}

fn default_preset() -> CollaborationModeMask {
    CollaborationModeMask {
        name: ModeKind::Default.display_name().to_string(),
        mode: Some(ModeKind::Default),
        model: None,
        reasoning_effort: None,
        developer_instructions: None,
    }
}
```

**特点**:
- Plan 模式使用 Medium 级别的 reasoning effort
- Plan 模式注入专门的 developer instructions（plan.md 模板）

---

## 7. Agent Loop 集成

位于 `codex/codex-rs/core/src/codex.rs`:

```rust
use codex_protocol::config_types::ModeKind;
use crate::proposed_plan_parser::ProposedPlanParser;
use crate::proposed_plan_parser::ProposedPlanSegment;

// 在 agent loop 中根据当前模式执行不同的逻辑
```

---

## 8. 测试验证

位于 `codex/codex-rs/app-server/tests/suite/v2/plan_item.rs`:

```rust
#[tokio::test]
async fn plan_mode_uses_proposed_plan_block_for_plan_item() -> Result<()> {
    let plan_block = "<proposed_plan>\n# Final plan\n- first\n- second\n</proposed_plan>\n";

    let collaboration_mode = CollaborationMode {
        mode: ModeKind::Plan,  // 设置 Plan 模式
        // ...
    };
    // 验证 Plan 模式是否正确解析 <proposed_plan> 块
}
```

---

## 9. 关键设计亮点

| 特性 | 实现细节 |
|------|----------|
| **模式隔离** | Plan 模式完全禁止变更操作，Default 模式允许执行 |
| **计划格式** | 使用 `<proposed_plan>` XML 块包裹计划内容 |
| **用户输入** | Plan 模式下强烈建议使用 `request_user_input` 工具进行决策 |
| **工具分离** | Plan 模式与 `update_plan` 工具互斥，避免混淆 |
| **三阶段流程** | 探索 → 明确意图 → 完善方案，确保计划决策完整 |
| **TUI 集成** | 通过 `/plan` 斜杠命令快速切换模式 |

---

## 10. 工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                  Codex Plan and Execute 工作流程                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   用户输入                                                      │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────┐                                              │
│   │ /plan 命令  │ 或 ModeKind::Plan                            │
│   └──────┬──────┘                                              │
│          ▼                                                      │
│   ┌─────────────────────────┐                                  │
│   │      PLAN MODE          │                                  │
│   ├─────────────────────────┤                                  │
│   │ Phase 1: 探索环境        │ 读取文件、搜索、分析              │
│   │ Phase 2: 明确意图        │ 询问目标、约束条件                │
│   │ Phase 3: 完善方案        │ 确定实现细节                      │
│   └──────┬──────────────────┘                                  │
│          │ 输出 <proposed_plan>                                │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │  用户确认   │                                              │
│   └──────┬──────┘                                              │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │ 切换到      │ ModeKind::Default                            │
│   │ Default模式 │                                              │
│   └──────┬──────┘                                              │
│          ▼                                                      │
│   ┌─────────────────────────┐                                  │
│   │     EXECUTE MODE        │                                  │
│   ├─────────────────────────┤                                  │
│   │ 按照计划执行             │ 编辑文件、运行命令、应用补丁       │
│   │ 使用 update_plan 跟踪    │ 更新任务进度                      │
│   └─────────────────────────┘                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 11. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `codex/codex-rs/protocol/src/config_types.rs` | `ModeKind` 枚举定义 |
| `codex/codex-rs/core/templates/collaboration_mode/plan.md` | Plan 模式三阶段工作流提示词 |
| `codex/codex-rs/core/src/proposed_plan_parser.rs` | `<proposed_plan>` 标签块解析 |
| `codex/codex-rs/core/src/tools/handlers/plan.rs` | `update_plan` 工具定义 |
| `codex/codex-rs/tui/src/slash_command.rs` | `/plan` 斜杠命令 |
| `codex/codex-rs/tui/src/collaboration_modes.rs` | 模式切换逻辑 |
| `codex/codex-rs/core/src/models_manager/collaboration_mode_presets.rs` | 模式预设配置 |

---

*文档版本: 2026-02-22*
*基于代码版本: codex-rs (baseline 2026-02-08)*
