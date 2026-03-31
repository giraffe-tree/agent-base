# 安全控制机制

> **文档类型说明**：本文档为跨项目对比分析，对比 5 个 AI Coding Agent 项目的安全控制实现差异。

---

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 20-30 分钟 |
> | 前置文档 | `01-{project}-overview.md`、`04-{project}-agent-loop.md` |
> | 文档结构 | 速览 → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

## TL;DR（结论先行）

一句话定义：安全控制是 Code Agent 的**最后一道防线**，防止 LLM 执行危险操作（删除文件、执行恶意命令、数据外传）。

跨项目的核心取舍：**三层防御体系**——环境隔离（最强）→ 操作确认（折中）→ 状态回滚（补救）（对比各项目的不同层次选择：Codex 选 OS 级沙箱、SWE-agent 选 Docker、Gemini CLI 选 Kind 审批、OpenCode 选规则引擎、Kimi CLI 选 Checkpoint 回滚）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 核心机制 | 三层防御：环境隔离 → 操作确认 → 状态回滚 | 见各项目实现 |
| 环境隔离 | Codex: Seatbelt/Landlock; SWE-agent: Docker | `codex/codex-rs/core/src/seatbelt.rs:36` |
| 操作确认 | Gemini CLI: Kind 分类; OpenCode: 规则引擎 | `gemini-cli/packages/core/src/tools/tools.ts:312` |
| 状态回滚 | Kimi CLI: D-Mail Checkpoint 回滚 | `kimi-cli/src/kimi_cli/soul/denwarenji.py:6` |

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

Code Agent 能执行 shell 命令、读写文件 —— 这意味着一行错误指令就可能：

```
没有安全控制：
  用户: "帮我清理临时文件"
  LLM: "rm -rf /tmp/*" → 实际执行 "rm -rf / tmp/*"（空格错误）
  → 系统被删除 → 灾难

有安全控制（三层防御）：
  第一层（环境隔离）：命令在沙箱中执行，只能访问工作目录
  第二层（操作确认）：删除操作触发用户确认
  第三层（状态回滚）：误删后立即恢复到之前状态
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| 文件系统风险 | `rm -rf /` 删除整个系统；覆盖 `~/.ssh/authorized_keys` 导致无法登录 |
| 命令执行风险 | 网络扫描增大攻击面；`pip install malware` 安装恶意软件；数据外传到恶意服务器 |
| 资源风险 | fork bomb 无限循环；大量写入磁盘填满文件系统 |

**核心矛盾：** Agent 需要足够多的权限才能完成任务（读写代码、运行测试），但权限太大会有风险。安全设计本质是在**能力**和**安全**之间找平衡点。

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 第一层：环境隔离（最强，独立沙箱）                                         │
│ ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐   │
│ │ Docker容器   │  │ Seatbelt/Landlock│  │  Windows 受限令牌           │   │
│ │ SWE-agent   │  │ Codex           │  │  （实验性）                  │   │
│ └─────────────┘  └─────────────────┘  └─────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ 如果无法隔离，则进入第二层
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 第二层：操作确认（折中，人工审批）                                         │
│ ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐   │
│ │ Kind分类    │  │ PermissionNext  │  │  简单确认列表                │   │
│ │ Gemini CLI  │  │ OpenCode        │  │  （大多数项目）               │   │
│ └─────────────┘  └─────────────────┘  └─────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ 如果已执行但出错，进入第三层
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 第三层：状态回滚（补救，出错后恢复）                                       │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ D-Mail / Checkpoint 回滚                                            │ │
│ │ Kimi CLI                                                            │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代表项目 | 代码位置 |
|-----|------|---------|---------|
| `Sandbox` | 在操作系统层面限制进程权限（文件访问、网络、系统调用） | Codex, SWE-agent | `codex/codex-rs/core/src/seatbelt.rs:36` |
| `PermissionManager` | 根据规则评估操作风险，决定允许/拒绝/询问 | Gemini CLI, OpenCode | `opencode/packages/opencode/src/permission/next.ts:236` |
| `Checkpoint` | 保存对话状态，支持回滚到历史节点 | Kimi CLI | `kimi-cli/src/kimi_cli/soul/context.py:80` |

### 2.3 核心组件交互关系

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant A as Agent Loop
    participant S as 安全检查层
    participant E as 执行环境

    U->>A: 1. 提交任务请求
    Note over A: Agent Loop 处理
    A->>S: 2. 生成工具调用请求

    alt 环境隔离层
        S->>S: 3a. 检查沙箱策略
        S->>E: 3b. 在沙箱中执行
        E-->>S: 3c. 返回隔离结果
    else 操作确认层
        S->>S: 4a. 评估操作风险
        alt 需要确认
            S->>U: 4b. 请求用户审批
            U-->>S: 4c. 用户决策
        end
        S->>E: 4d. 执行（或拒绝）
        E-->>S: 4e. 返回结果
    else 状态回滚层
        S->>S: 5a. 打 Checkpoint
        S->>E: 5b. 执行操作
        E-->>S: 5c. 返回结果
        alt 执行出错
            S->>S: 5d. 回滚到 Checkpoint
        end
    end

    S-->>A: 6. 返回安全处理结果
    A-->>U: 7. 展示最终结果
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | 用户向 Agent Loop 提交任务 | 解耦用户输入与安全控制逻辑 |
| 2 | Agent 生成工具调用请求 | 统一入口便于集中安全检查 |
| 3a-3c | 环境隔离层处理 | 最强防护，在 OS 层面限制权限 |
| 4a-4e | 操作确认层处理 | 折中方案，依赖用户判断 |
| 5a-5d | 状态回滚层处理 | 事后补救，提供"反悔"能力 |
| 6-7 | 返回最终结果 | 统一输出格式，上层无感知安全细节 |

---

## 3. 核心组件详细分析

### 3.1 环境隔离层（第一层）

#### 职责定位

在 Agent 代码运行之前就限制其能做什么，是最彻底的安全措施。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Uninitialized: 系统启动
    Uninitialized --> Initializing: 加载沙箱策略
    Initializing --> Ready: 策略加载完成

    Ready --> Executing: 收到执行请求
    Executing --> Success: 执行成功
    Executing --> Blocked: 沙箱拦截
    Executing --> Failed: 执行失败

    Blocked --> Ready: 记录拦截日志
    Failed --> Ready: 返回错误信息
    Success --> Ready: 返回执行结果

    Ready --> [*]: 系统关闭
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Uninitialized | 未初始化 | 系统启动 | 开始加载沙箱策略 |
| Initializing | 初始化中 | 开始加载策略文件 | 策略解析完成 |
| Ready | 就绪等待 | 初始化完成 | 收到执行请求 |
| Executing | 执行中 | 收到执行请求 | 执行完成/被拦截/失败 |
| Blocked | 被沙箱拦截 | 操作违反策略 | 记录日志后返回 Ready |
| Failed | 执行失败 | 沙箱内执行出错 | 返回错误后回到 Ready |
| Success | 执行成功 | 沙箱内执行完成 | 返回结果后回到 Ready |

#### 内部数据流

```text
┌────────────────────────────────────────────┐
│  输入层                                     │
│   原始命令 → 策略匹配 → 沙箱类型选择        │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  处理层                                     │
│   策略加载 → 命令包装 → 沙箱执行           │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  输出层                                     │
│   结果捕获 → 日志记录 → 状态返回            │
└────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `spawn_command_under_seatbelt()` | 命令、工作目录、策略 | 子进程句柄 | Codex macOS 沙箱执行 | `codex/codex-rs/core/src/seatbelt.rs:36` |
| `select_initial()` | 策略、偏好配置 | 沙箱类型 | 动态选择沙箱实现 | `codex/codex-rs/core/src/sandboxing/mod.rs:97` |
| `DockerContainer.run()` | 命令、镜像配置 | 执行结果 | SWE-agent 容器执行 | SWE-agent 容器管理模块 |

#### Codex：OS 级沙箱（macOS Seatbelt / Linux Landlock）

**架构图：**

```mermaid
flowchart LR
    Agent["Agent 代码"] -->|"执行命令"| Sandbox["沙箱层<br/>macOS Seatbelt /<br/>Linux Landlock"]
    Sandbox -->|"允许"| OS["操作系统"]
    Sandbox -->|"拒绝（系统调用被过滤）"| Error["操作失败"]
```

**关键代码：**

```rust
// codex/codex-rs/core/src/seatbelt.rs:26
const MACOS_SEATBELT_BASE_POLICY: &str = include_str!("seatbelt_base_policy.sbpl");
const MACOS_SEATBELT_NETWORK_POLICY: &str = include_str!("seatbelt_network_policy.sbpl");
const MACOS_SEATBELT_PLATFORM_DEFAULTS: &str = include_str!("seatbelt_platform_defaults.sbpl");

// codex/codex-rs/core/src/seatbelt.rs:36
pub async fn spawn_command_under_seatbelt(
    command: Vec<String>,
    command_cwd: PathBuf,
    sandbox_policy: &SandboxPolicy,
    // ...
) -> anyhow::Result<Child> {
    // 通过 sandbox-exec 执行命令
}
```

**沙箱策略**（`codex/codex-rs/core/src/sandboxing/mod.rs:114-129`）：

```rust
SandboxPolicy::DangerFullAccess  // 无限制（开发调试用）
SandboxPolicy::ExternalSandbox { network_access: NetworkAccess::Enabled, .. }
SandboxPolicy::ReadOnly  // 只读模式（最安全）
```

**macOS Seatbelt 限制：**
- 只读路径：除工作目录外的大多数文件系统
- 网络隔离：三级策略 —— `full`（完全访问）/ `host-only`（仅本机）/ `none`（无网络）
- 进程限制：防止 fork bomb

**工程取舍：** 最强的隔离，但 macOS 专用（Seatbelt）或 Linux 专用（Landlock），跨平台需要多套实现。

#### SWE-agent：Docker 容器隔离

**架构图：**

```text
宿主机
├── SWE-agent 进程（控制）
└── Docker 容器（执行）
    ├── 独立文件系统（挂载的项目目录）
    ├── 独立网络（可配置）
    └── 独立进程空间
```

即使 Agent 执行了破坏性命令（如 `rm -rf /`），也只影响容器内部，宿主机安全。

**工程取舍：** 跨平台（Linux/macOS/Windows 都能用 Docker），隔离彻底；但容器启动慢（通常需要 5-30 秒），不适合轻量交互场景。

---

### 3.2 操作确认层（第二层）

#### 职责定位

当无法或不适合使用环境隔离时，通过用户审批来控制危险操作。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Idle: 系统启动
    Idle --> Evaluating: 收到权限请求

    Evaluating --> AutoAllowed: 规则匹配 allow
    Evaluating --> AutoDenied: 规则匹配 deny
    Evaluating --> NeedsConfirmation: 规则匹配 ask / 无匹配

    NeedsConfirmation --> WaitingForUser: 展示确认提示
    WaitingForUser --> Allowed: 用户选择允许
    WaitingForUser --> Denied: 用户选择拒绝
    WaitingForUser --> AlwaysAllowed: 用户选择总是允许

    AlwaysAllowed --> Allowed: 记录规则

    AutoAllowed --> Idle: 执行操作
    Allowed --> Idle: 执行操作
    AutoDenied --> Idle: 返回拒绝
    Denied --> Idle: 返回拒绝
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Idle | 空闲等待 | 初始化完成或处理结束 | 收到权限请求 |
| Evaluating | 评估规则 | 收到权限请求 | 规则匹配完成 |
| AutoAllowed | 自动允许 | 匹配 allow 规则 | 直接执行 |
| AutoDenied | 自动拒绝 | 匹配 deny 规则 | 直接拒绝 |
| NeedsConfirmation | 需要确认 | 匹配 ask 或无规则 | 展示确认 UI |
| WaitingForUser | 等待用户 | 展示确认提示 | 用户做出选择 |
| Allowed | 已允许 | 用户允许 | 执行操作 |
| Denied | 已拒绝 | 用户拒绝 | 返回错误 |
| AlwaysAllowed | 总是允许 | 用户选择记住 | 记录规则并执行 |

#### Gemini CLI：Kind 分类审批

**状态机图：**

```mermaid
flowchart TD
    A["Agent 调用工具"] --> B{查看工具 Kind}
    B -->|"Kind.Read"| C["直接执行<br/>（只读，安全）"]
    B -->|"Kind.Write"| D["请求用户确认"]
    B -->|"Kind.Execute"| E["严格审批流程"]
    D --> F{用户选择}
    F -->|"允许"| G["执行"]
    F -->|"拒绝"| H["拒绝执行"]
    F -->|"总是允许"| I["记住选择<br/>后续跳过确认"]
```

**关键代码：**

```typescript
// gemini-cli/packages/core/src/tools/tools.ts:312
interface Tool<TParams, TResult> {
  // ...
  kind: Kind;  // 工具分类：Read / Write / Execute
  // ...
}
```

**工程取舍：** 实现简单，不需要额外依赖；但保护依赖"工具的 Kind 设置是否正确"，分错 Kind 会影响安全策略。用户确认有"确认疲劳"问题（频繁确认会让用户倾向于无脑点"允许"）。

#### OpenCode：规则引擎 + 模式匹配

**规则评估流程：**

```mermaid
flowchart TD
    Request["权限请求"] --> Match1{精确匹配?}
    Match1 -->|是| Action1["返回指定 Action"]
    Match1 -->|否| Match2{模式匹配?}
    Match2 -->|是| Action2["返回匹配 Action"]
    Match2 -->|否| Default["返回默认 ask"]
```

**关键代码：**

```typescript
// opencode/packages/opencode/src/permission/next.ts:25
export const Action = z.enum(["allow", "deny", "ask"])

// opencode/packages/opencode/src/permission/next.ts:236
export function evaluate(permission: string, pattern: string, ...rulesets: Ruleset[]): Rule {
    const merged = merge(...rulesets)
    const match = merged.findLast(
      (rule) => Wildcard.match(permission, rule.permission) && Wildcard.match(pattern, rule.pattern),
    )
    return match ?? { action: "ask", permission, pattern: "*" }
}
```

**规则示例：**

```typescript
permissions = {
    "bash": {              // 工具类型
        "rm -rf *": "deny", // 精确匹配 → 总是拒绝
        "npm install": "ask", // 精确匹配 → 每次询问
        "*": "allow"        // 通配符 → 默认允许
    }
}
```

**工程取舍：** 最灵活，可以配置任意规则；但规则复杂度高，用户需要理解配置语法。

---

### 3.3 状态回滚层（第三层）

#### 职责定位

当危险操作已经执行，提供一种"反悔"机制恢复到之前状态。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Active: 对话开始
    Active --> CheckpointCreated: 打 Checkpoint

    CheckpointCreated --> Active: 继续执行
    Active --> ToolExecuting: 执行工具

    ToolExecuting --> Success: 执行成功
    ToolExecuting --> Error: 执行出错

    Error --> Reverting: 触发 D-Mail
    Reverting --> Validating: 验证 checkpoint_id

    Validating --> RollingBack: ID 有效
    Validating --> Invalid: ID 无效

    RollingBack --> Active: 回滚完成，恢复对话
    Invalid --> Error: 抛出 ValueError

    Success --> Active: 继续对话
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Active | 正常对话中 | 初始化完成或回滚结束 | 执行工具或打 Checkpoint |
| CheckpointCreated | Checkpoint 已创建 | 调用打 Checkpoint | 继续执行 |
| ToolExecuting | 工具执行中 | 调用工具 | 执行完成 |
| Success | 执行成功 | 工具正常返回 | 继续对话 |
| Error | 执行出错 | 工具执行失败 | 触发回滚或报错 |
| Reverting | 回滚中 | 调用 D-Mail | 验证 checkpoint |
| Validating | 验证中 | 开始回滚 | 验证完成 |
| RollingBack | 执行回滚 | checkpoint 有效 | 恢复对话状态 |
| Invalid | checkpoint 无效 | checkpoint_id 错误 | 抛出异常 |

#### Kimi CLI：D-Mail 回滚机制

**数据流图：**

```text
┌─────────────────────────────────────────────────────────────┐
│  执行前                                                      │
│  ├── 打 Checkpoint ──► 保存对话历史到文件                    │
│  └── 继续执行工具                                            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  执行后（发现问题）                                           │
│  ├── LLM 调用 SendDMail 工具                                  │
│  │   └── 指定 checkpoint_id                                   │
│  └── 系统回滚到指定 Checkpoint                                │
│      └── 截断对话历史（但不回滚文件系统）                      │
└─────────────────────────────────────────────────────────────┘
```

**关键代码：**

```python
# kimi-cli/src/kimi_cli/soul/denwarenji.py:6-9
class DMail(BaseModel):
    message: str = Field(description="The message to send.")
    checkpoint_id: int = Field(description="The checkpoint to send the message back to.", ge=0)
    # TODO: allow restoring filesystem state to the checkpoint

# kimi-cli/src/kimi_cli/soul/context.py:80-99
async def revert_to(self, checkpoint_id: int):
    """Revert the context to the specified checkpoint."""
    if checkpoint_id >= self._next_checkpoint_id:
        raise ValueError(f"Checkpoint {checkpoint_id} does not exist")
    # rotate the context file
```

**⚠️ 重要限制：** 只回滚 LLM 看到的历史，**不回滚文件系统**。

**工程取舍：** 不是传统意义的安全隔离，而是给 LLM 一个"反悔"的能力 —— 当探索方向错误时，可以抛弃无效路径。

---

### 3.4 组件间协作时序

展示三层防御如何协作完成一次完整的安全控制流程。

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as Agent Loop
    participant S1 as 环境隔离层
    participant S2 as 操作确认层
    participant S3 as 状态回滚层
    participant E as 执行环境

    U->>A: 发起操作请求
    activate A

    A->>A: 前置检查：解析工具调用
    Note right of A: 验证输入合法性

    A->>S1: 请求环境隔离检查
    activate S1

    alt 环境隔离可用
        S1->>S1: 加载沙箱策略
        S1->>E: 在沙箱中执行
        activate E
        E-->>S1: 返回隔离结果
        deactivate E
        S1-->>A: 返回执行结果
    else 环境隔离不可用
        S1-->>A: 返回不可用
        deactivate S1

        A->>S2: 请求操作确认
        activate S2

        S2->>S2: 评估规则/Kind
        alt 需要确认
            S2->>U: 请求用户审批
            U-->>S2: 用户决策
        end

        S2->>S3: 打 Checkpoint
        activate S3
        S3-->>S2: Checkpoint ID
        deactivate S3

        S2->>E: 执行操作
        activate E
        E-->>S2: 返回结果
        deactivate E

        S2-->>A: 返回执行结果
        deactivate S2
    end

    A->>A: 结果组装
    A-->>U: 返回最终结果
    deactivate A
```

**协作要点**：

1. **Agent Loop 与环境隔离层**：优先尝试最强防护，如果平台不支持则降级
2. **环境隔离与操作确认**：互斥关系，有沙箱则不触发确认流程
3. **操作确认与状态回滚**：顺序执行，先打 Checkpoint 再执行，确保可回滚
4. **执行环境与各层**：统一接口，各层负责包装执行环境

---

### 3.5 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始输入] --> I2[解析验证]
        I2 --> I3[结构化数据]
    end

    subgraph Process["处理阶段"]
        P1[策略匹配] --> P2[风险评估]
        P2 --> P3[决策执行]
    end

    subgraph Output["输出阶段"]
        O1[结果生成] --> O2[状态更新]
        O2 --> O3[副作用执行]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[发生错误] --> E1{错误类型}
    E1 -->|沙箱拦截| R1[返回拦截信息]
    E1 -->|权限拒绝| R2[抛出 DeniedError]
    E1 -->|执行失败| R3[触发回滚]
    E1 -->|严重错误| R4[终止流程]

    R1 --> R1A[Agent 尝试替代方案]
    R1A -->|成功| R1B[继续主路径]
    R1A -->|失败| R2

    R2 --> R2A[返回错误给 LLM]
    R3 --> R3A[回滚到 Checkpoint]
    R3A --> R3B[恢复对话状态]
    R4 --> R4A[记录错误日志]
    R4A --> R4B[通知用户]

    R1B --> End[结束]
    R2A --> End
    R3B --> End
    R4B --> End

    style R1 fill:#90EE90
    style R2 fill:#FFD700
    style R3 fill:#87CEEB
    style R4 fill:#FF6B6B
```

#### 优化路径（缓存/短路）

```mermaid
flowchart TD
    Start[请求进入] --> Check{已授权?}
    Check -->|命中缓存| Hit[直接允许]
    Check -->|未命中| Miss[执行规则评估]
    Miss --> Save[缓存决策]
    Save --> Result[返回结果]
    Hit --> Result

    Start --> Fast{只读操作?}
    Fast -->|是| FastPath[自动允许]
    Fast -->|否| Check
    FastPath --> Result

    style Hit fill:#90EE90
    style FastPath fill:#87CEEB
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

展示数据如何从输入到输出的完整变换过程。

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as Agent Loop
    participant S as 安全检查层
    participant E as 执行环境

    U->>A: 提交任务请求
    A->>S: 生成工具调用请求
    S->>S: 策略评估与风险检查
    S->>E: 在受限环境中执行
    E-->>S: 返回执行结果
    S->>S: 结果安全检查
    S-->>A: 返回安全结果
    A-->>U: 展示最终结果
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | 用户任务 | 解析意图，生成工具调用 | 工具调用请求 | Agent Loop 入口 |
| 评估 | 工具调用请求 | 匹配安全策略，评估风险 | 决策（允许/拒绝/询问） | `opencode/packages/opencode/src/permission/next.ts:236` |
| 执行 | 决策 + 命令 | 在沙箱/确认后执行 | 原始执行结果 | `codex/codex-rs/core/src/seatbelt.rs:36` |
| 输出 | 执行结果 | 安全检查，格式化 | 最终结果 | 各项目结果处理模块 |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[用户请求] --> I2[工具调用解析]
        I2 --> I3[安全策略匹配]
    end

    subgraph Process["处理阶段"]
        P1[风险评估] --> P2[决策执行]
        P2 --> P3[沙箱/确认/回滚]
    end

    subgraph Output["输出阶段"]
        O1[结果捕获] --> O2[安全验证]
        O2 --> O3[状态更新]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#f9f,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始] --> B{安全检查}
    B -->|通过| C[正常执行]
    B -->|沙箱拦截| D[返回拦截错误]
    B -->|权限拒绝| E[抛出 DeniedError]
    B -->|需要确认| F[请求用户审批]

    F -->|允许| C
    F -->|拒绝| E

    C --> G{执行结果}
    G -->|成功| H[更新状态]
    G -->|失败| I{可回滚?}

    I -->|是| J[触发 Checkpoint 回滚]
    I -->|否| K[返回错误]

    D --> L[Agent 重试]
    E --> M[终止当前操作]
    J --> N[恢复之前状态]
    K --> M
    H --> O[结束]
    L --> O
    M --> O
    N --> O
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

**Codex SandboxPolicy：**

```rust
// codex/codex-rs/core/src/protocol.rs（SandboxPolicy 定义位置）
// 三种模式：DangerFullAccess / ExternalSandbox / ReadOnly
```

**字段说明**：

| 字段/变体 | 类型 | 用途 |
|-----|------|------|
| `DangerFullAccess` | 单元变体 | 无限制模式，开发调试用 |
| `ExternalSandbox` | 结构体变体 | 外部沙箱，含网络配置 |
| `ReadOnly` | 单元变体 | 只读模式，最安全 |
| `network_access` | `NetworkAccess` | 网络访问级别：full/host-only/none |

**OpenCode 权限规则：**

```typescript
// opencode/packages/opencode/src/permission/next.ts:30-39
export const Rule = z.object({
  permission: z.string(),
  pattern: z.string(),
  action: Action,  // "allow" | "deny" | "ask"
})
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `permission` | `string` | 权限类型标识（如 "bash"） |
| `pattern` | `string` | 匹配模式（支持通配符） |
| `action` | `Action` | 决策：allow/deny/ask |

**Kimi CLI D-Mail：**

```python
# kimi-cli/src/kimi_cli/soul/denwarenji.py:6-9
class DMail(BaseModel):
    message: str = Field(description="The message to send.")
    checkpoint_id: int = Field(description="The checkpoint to send the message back to.", ge=0)
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `message` | `str` | D-Mail 消息内容 |
| `checkpoint_id` | `int` | 目标 Checkpoint ID，必须 >= 0 |

### 5.2 主链路代码

**Codex 沙箱执行：**

```rust
// codex/codex-rs/core/src/sandboxing/mod.rs:97-131
pub(crate) fn select_initial(
    &self,
    policy: &SandboxPolicy,
    pref: SandboxablePreference,
    windows_sandbox_level: WindowsSandboxLevel,
    has_managed_network_requirements: bool,
) -> SandboxType {
    match pref {
        SandboxablePreference::Forbid => SandboxType::None,
        SandboxablePreference::Require => {
            crate::safety::get_platform_sandbox(
                windows_sandbox_level != WindowsSandboxLevel::Disabled,
            )
            .unwrap_or(SandboxType::None)
        }
        // ...
    }
}
```

**设计意图**：
1. **三层策略选择**：根据用户偏好（Forbid/Require/Auto）和策略配置动态选择沙箱类型
2. **平台适配**：macOS 用 Seatbelt，Linux 用 Landlock，Windows 用受限令牌
3. **网络需求感知**：有网络需求时强制启用平台沙箱

**OpenCode 规则评估：**

```typescript
// opencode/packages/opencode/src/permission/next.ts:236-246
export function evaluate(permission: string, pattern: string, ...rulesets: Ruleset[]): Rule {
    const merged = merge(...rulesets)
    const match = merged.findLast(
      (rule) => Wildcard.match(permission, rule.permission) && Wildcard.match(pattern, rule.pattern),
    )
    return match ?? { action: "ask", permission, pattern: "*" }
}
```

**设计意图**：
1. **规则合并**：支持多规则集合并，便于模块化配置
2. **最后匹配优先**：`findLast` 确保后定义的规则覆盖前者
3. **默认询问**：无匹配时默认 ask，保守策略

**Kimi CLI 回滚实现：**

```python
# kimi-cli/src/kimi_cli/soul/context.py:80-99
async def revert_to(self, checkpoint_id: int):
    """Revert the context to the specified checkpoint."""
    if checkpoint_id >= self._next_checkpoint_id:
        raise ValueError(f"Checkpoint {checkpoint_id} does not exist")
    # rotate the context file
    # ...
```

**设计意图**：
1. **有效性验证**：检查 checkpoint_id 防止无效回滚
2. **文件旋转**：通过旋转上下文文件实现原子性回滚
3. **仅回滚对话**：⚠️ 明确不回滚文件系统状态

<details>
<summary>查看完整实现（含异常处理、日志等）</summary>

**Codex 完整沙箱执行链：**

```rust
// codex/codex-rs/core/src/seatbelt.rs:36-89
pub async fn spawn_command_under_seatbelt(
    command: Vec<String>,
    command_cwd: PathBuf,
    sandbox_policy: &SandboxPolicy,
    env: Option<HashMap<String, String>>,
) -> anyhow::Result<Child> {
    // 构建 sandbox-exec 命令参数
    let mut args = vec![
        "-f".to_string(),
        policy_file_path.to_string_lossy().to_string(),
    ];

    // 添加网络策略
    if network_enabled {
        args.push("-f".to_string());
        args.push(network_policy_path.to_string_lossy().to_string());
    }

    // 执行命令
    let child = spawn_child_async(...)?;
    Ok(child)
}
```

**OpenCode 完整权限检查：**

```typescript
// opencode/packages/opencode/src/permission/next.ts:131-180
export async function ask(
  permission: string,
  pattern: string,
  options?: AskOptions,
): Promise<boolean> {
  // 评估规则
  const rule = evaluate(permission, pattern, ...rulesets)

  switch (rule.action) {
    case "allow":
      return true
    case "deny":
      throw new DeniedError(permission, pattern)
    case "ask":
      // 请求用户确认
      return await promptUser(permission, pattern, options)
  }
}
```

</details>

### 5.3 关键调用链

```text
Codex 沙箱执行链:
  spawn_command_under_seatbelt()   [codex/codex-rs/core/src/seatbelt.rs:36]
    -> create_seatbelt_command_args()  [seatbelt.rs]
      -> 生成 sandbox-exec 命令参数
        -> spawn_child_async()         [spawn.rs]
          - 执行沙箱包装后的命令

OpenCode 权限检查链:
  PermissionNext.ask()             [opencode/packages/opencode/src/permission/next.ts:131]
    -> evaluate()                    [next.ts:236]
      - Wildcard.match() 匹配规则
      - 返回 allow/deny/ask 决策

Kimi CLI 回滚链:
  DenwaRenji.send_dmail()          [kimi-cli/src/kimi_cli/soul/denwarenji.py:21]
    -> Context.revert_to()           [kimi-cli/src/kimi_cli/soul/context.py:80]
      - 验证 checkpoint_id 有效性
      - 截断对话历史
      - 旋转上下文文件
```

---

## 6. 设计意图与 Trade-off

### 6.1 各项目的选择对比

| 维度 | Codex | SWE-agent | Gemini CLI | OpenCode | Kimi CLI |
|-----|-------|-----------|------------|----------|----------|
| **核心方案** | OS 级沙箱 | Docker 容器 | Kind 审批 | 规则引擎 | Checkpoint 回滚 |
| **隔离强度** | 强（syscall 过滤） | 最强（OS 级） | 弱（仅事前询问） | 可配置 | 无（事后补救） |
| **性能影响** | 低 | 高（容器启动慢） | 无 | 无 | 低 |
| **跨平台** | ❌ 平台限定 | ✅ | ✅ | ✅ | ✅ |
| **实现复杂度** | 高 | 中 | 低 | 中 | 低 |
| **用户干预** | 无 | 无 | 高 | 可配置 | 低 |

### 6.2 为什么这样设计？

**核心问题：** 如何在安全性和可用性之间找到平衡？

**Codex 的解决方案：**
- 代码依据：`codex/codex-rs/core/src/seatbelt.rs:36`
- 设计意图：利用操作系统原生能力实现最强隔离
- 带来的好处：
  - 系统调用级别过滤，无法绕过
  - 性能开销低
- 付出的代价：
  - 平台依赖（macOS Seatbelt / Linux Landlock）
  - 实现复杂度高

**SWE-agent 的解决方案：**
- 代码依据：Docker 容器配置
- 设计意图：利用成熟容器技术实现跨平台隔离
- 带来的好处：
  - 完全隔离，容器边界不可突破
  - 跨平台一致
- 付出的代价：
  - 容器启动延迟（5-30 秒）
  - 资源占用较高

**Gemini CLI 的解决方案：**
- 代码依据：`gemini-cli/packages/core/src/tools/tools.ts:312`
- 设计意图：通过工具分类简化用户决策
- 带来的好处：
  - 实现简单，无需额外依赖
  - 用户体验好（只读操作无打扰）
- 付出的代价：
  - 依赖工具开发者正确设置 Kind
  - 确认疲劳问题

**OpenCode 的解决方案：**
- 代码依据：`opencode/packages/opencode/src/permission/next.ts:236`
- 设计意图：提供灵活可配置的规则引擎
- 带来的好处：
  - 最灵活，可配置任意规则
  - 支持模式匹配
- 付出的代价：
  - 规则复杂度高
  - 用户需要学习配置语法

**Kimi CLI 的解决方案：**
- 代码依据：`kimi-cli/src/kimi_cli/soul/denwarenji.py:6`
- 设计意图：给 LLM "反悔"能力，而非阻止操作
- 带来的好处：
  - 适合探索性任务
  - 实现简单
- 付出的代价：
  - 不回滚文件系统
  - 不是真正的安全隔离

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统方案: 无安全控制"
    branch "Codex"
    checkout "Codex"
    commit id: "OS级沙箱"
    checkout main
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "Docker隔离"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "Kind分类审批"
    checkout main
    branch "OpenCode"
    checkout "OpenCode"
    commit id: "规则引擎"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "Checkpoint回滚"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| **Codex** | OS 级沙箱（Seatbelt/Landlock） | 企业级安全需求，接受平台限定 |
| **SWE-agent** | Docker 容器隔离 | 需要完全隔离，接受启动延迟 |
| **Gemini CLI** | Kind 分类审批 | 日常开发辅助，强调用户体验 |
| **OpenCode** | 规则引擎 | 需要精细控制特定命令 |
| **Kimi CLI** | D-Mail 回滚 | 探索性长任务，不怕文件改动但想控制上下文 |

**选择策略：**
- 在陌生代码库上执行任意命令 → 优先 Docker 或 Seatbelt
- 日常开发辅助、只读操作为主 → Kind 分类审批足够
- 需要精细控制特定命令 → OpenCode 规则引擎
- 探索性长任务，不怕文件改动但想控制上下文 → Kimi CLI D-Mail

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 沙箱策略拒绝 | 操作违反沙箱规则 | `codex/codex-rs/core/src/exec.rs` |
| 权限被拒绝 | 规则匹配 deny 或用户拒绝 | `opencode/packages/opencode/src/permission/next.ts:259-280` |
| Checkpoint 不存在 | checkpoint_id 超出范围 | `kimi-cli/src/kimi_cli/soul/context.py:95-97` |
| D-Mail 重复发送 | 已有一个待处理 D-Mail | `kimi-cli/src/kimi_cli/soul/denwarenji.py:23-24` |
| 沙箱初始化失败 | 平台不支持或策略错误 | `codex/codex-rs/core/src/sandboxing/mod.rs:97-131` |

### 7.2 超时/资源限制

**Codex 沙箱资源限制：**

```rust
// codex/codex-rs/core/src/seatbelt.rs（策略文件配置）
// 网络访问：三级策略（full/host-only/none）
// 文件系统：只读或受限写入
// 进程：防止 fork bomb
```

**SWE-agent 容器限制：**
- 通过 Docker 配置限制 CPU、内存、磁盘
- 网络隔离（可配置）

**资源限制对比：**

| 项目 | CPU 限制 | 内存限制 | 网络限制 | 文件系统限制 |
|-----|---------|---------|---------|-------------|
| Codex | ❌ | ❌ | ✅ 三级策略 | ✅ 只读/受限 |
| SWE-agent | ✅ Docker | ✅ Docker | ✅ Docker | ✅ 容器边界 |
| Gemini CLI | ❌ | ❌ | ❌ | ❌ |
| OpenCode | ❌ | ❌ | ❌ | ❌ |
| Kimi CLI | ❌ | ❌ | ❌ | ❌ |

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 沙箱拒绝 | 返回错误信息，Agent 可尝试其他方法 | `codex/codex-rs/core/src/exec.rs` |
| 权限被拒绝 | 抛出 `DeniedError`，终止当前工具调用 | `opencode/packages/opencode/src/permission/next.ts:259-280` |
| Checkpoint 不存在 | 抛出 `ValueError`，拒绝回滚 | `kimi-cli/src/kimi_cli/soul/context.py:95-97` |
| D-Mail 重复发送 | 抛出 `DenwaRenjiError`，只允许一个待处理 D-Mail | `kimi-cli/src/kimi_cli/soul/denwarenji.py:23-24` |

### 7.4 安全边界情况

| 边界情况 | 风险描述 | 各项目处理 |
|---------|---------|-----------|
| **沙箱逃逸** | Agent 绕过沙箱限制执行危险操作 | Codex: Seatbelt 策略文件严格限制系统调用；SWE-agent: Docker 容器边界 |
| **审批绕过** | 通过混淆命令绕过规则匹配 | OpenCode: 精确匹配 > 模式匹配优先级；Gemini CLI: Kind 由工具开发者定义，不易绕过 |
| **策略冲突** | 多条规则相互矛盾 | OpenCode: `findLast` 取最后匹配的规则；Kimi CLI: 无策略冲突问题 |
| **回滚失效** | Checkpoint 后文件被外部修改 | Kimi CLI: **不回滚文件系统**，仅回滚对话历史 |
| **确认疲劳** | 频繁确认导致用户无脑点击"允许" | Gemini CLI: "总是允许"选项减少重复确认 |

---

## 8. 关键代码索引

| 功能 | 项目 | 文件 | 行号 | 说明 |
|-----|------|------|------|------|
| 沙箱执行 | Codex | `codex/codex-rs/core/src/seatbelt.rs` | 36 | `spawn_command_under_seatbelt()` |
| 沙箱策略 | Codex | `codex/codex-rs/core/src/seatbelt.rs` | 26 | 基础沙箱策略（`.sbpl` 文件引用） |
| 策略选择 | Codex | `codex/codex-rs/core/src/sandboxing/mod.rs` | 97-131 | `SandboxPolicy` 三种模式及选择逻辑 |
| 工具分类 | Gemini CLI | `gemini-cli/packages/core/src/tools/tools.ts` | 312 | `kind` 字段 —— 工具分类 |
| 权限规则 | OpenCode | `opencode/packages/opencode/src/permission/next.ts` | 14 | `PermissionNext` 命名空间 |
| 操作类型 | OpenCode | `opencode/packages/opencode/src/permission/next.ts` | 25 | `Action` 枚举（allow/deny/ask） |
| 规则评估 | OpenCode | `opencode/packages/opencode/src/permission/next.ts` | 236 | `evaluate()` —— 规则评估 |
| D-Mail 定义 | Kimi CLI | `kimi-cli/src/kimi_cli/soul/denwarenji.py` | 6 | `DMail` 类定义 |
| D-Mail 发送 | Kimi CLI | `kimi-cli/src/kimi_cli/soul/denwarenji.py` | 21 | `send_dmail()` 方法 |
| 执行回滚 | Kimi CLI | `kimi-cli/src/kimi_cli/soul/context.py` | 80 | `revert_to()` —— 执行回滚 |

---

## 9. 延伸阅读

- 前置知识：`docs/comm/04-comm-agent-loop.md`（Agent Loop 如何触发工具执行）
- 相关机制：
  - `docs/codex/10-codex-safety-control.md`（Codex 沙箱详细分析）
  - `docs/swe-agent/10-swe-agent-safety-control.md`（SWE-agent Docker 隔离）
  - `docs/gemini-cli/10-gemini-cli-safety-control.md`（Gemini CLI Kind 审批）
  - `docs/opencode/10-opencode-safety-control.md`（OpenCode 规则引擎）
  - `docs/kimi-cli/10-kimi-cli-safety-control.md`（Kimi CLI Checkpoint 回滚）
- 深度分析：`docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`

---

*✅ Verified: 基于 codex/codex-rs/core/src/seatbelt.rs:36、opencode/packages/opencode/src/permission/next.ts:236、kimi-cli/src/kimi_cli/soul/denwarenji.py:6 等源码分析*
*基于版本：2026-02-08 | 最后更新：2026-03-03*
