# SWE-agent Revert User Edit Conflict

## TL;DR（结论先行）

**当前 SWE-agent 的 checkpoint 仅用于"每步持久化、断点续传"，未实现用户可触发的文件级 revert/rollback 能力。** 因此"revert 时发现用户已编辑与源文件冲突"的文件冲突场景，在现有架构中不适用；无对应冲突检测或协商逻辑。

---

## 1. 为什么需要这个机制？

### 1.1 问题场景

在支持 revert 的系统中（如 Kimi CLI），可能出现以下场景：
- 用户手动编辑文件后触发 revert
- revert 操作与用户的编辑产生冲突
- 需要冲突检测和协商机制

SWE-agent 的现状：
- Checkpoint 仅用于内部持久化（trajectory 保存）
- 无用户触发的文件级 revert
- 因此不存在上述冲突场景

### 1.2 核心挑战

| 挑战 | 假设支持 revert 时的处理 | SWE-agent 现状 |
|-----|------------------------|---------------|
| 冲突检测 | 需要对比文件版本 | 不适用 |
| 协商策略 | 用户选择/自动合并 | 不适用 |
| 数据一致性 | 确保 checkpoint 与文件一致 | 仅内部使用 |
| 用户体验 | 清晰的冲突提示 | 无此功能 |

---

## 2. 整体架构

### 2.1 SWE-agent Checkpoint 定位

```text
┌─────────────────────────────────────────────────────────────┐
│ SWE-agent Session Management                                 │
│ sweagent/agent/agents.py                                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Checkpoint（内部使用）                                       │
│ - 每 step 持久化 trajectory                                 │
│ - 支持断点续传                                              │
│ - 支持从 trajectory 恢复执行                                │
│ - ❌ 不支持用户触发的 revert                                 │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `RetryAgent` | 多轮尝试管理，保存每轮 trajectory | `sweagent/agent/agents.py:257` |
| `DefaultAgent` | 单轮执行，保存 step 级别的 trajectory | `sweagent/agent/agents.py:443` |
| `save_trajectory()` | 将 trajectory 持久化到磁盘 | `sweagent/agent/agents.py:779` |
| `traj_path` | trajectory 文件路径 | `sweagent/agent/agents.py:589` |

### 2.3 对比：假设的 Revert 系统

```text
┌─────────────────────────────────────────────────────────────┐
│ Hypothetical Revert System                                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   User Edit ──┐                                             │
│               ▼                                             │
│   ┌─────────────────┐      ┌─────────────────┐             │
│   │ Conflict        │─────▶│ Resolution      │             │
│   │ Detection       │      │ Strategy        │             │
│   └─────────────────┘      └─────────────────┘             │
│           ▲                                                │
│           │                                                │
│   Revert ─┘                                                │
│                                                            │
│   需要：文件版本对比、冲突标记、用户协商                    │
│                                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件详细分析

### 3.1 Trajectory 持久化机制

#### 职责定位

Trajectory 是 SWE-agent 的核心状态载体，包含完整的执行历史，用于断点续传和结果分析。

#### 关键数据结构

```python
# sweagent/types.py:44-52
class TrajectoryStep(TypedDict):
    action: str
    observation: str
    response: str
    state: dict[str, str]
    thought: str
    execution_time: float
    query: list[dict[str, Any]]
    extra_info: dict[str, Any]
```

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Running: 开始执行
    Running --> StepComplete: 单步完成
    StepComplete --> Running: 继续执行
    StepComplete --> TrajectorySaved: 保存 trajectory
    TrajectorySaved --> Running: 下一 step
    Running --> Completed: 任务完成
    Running --> Error: 发生错误
    Error --> TrajectorySaved: 保存错误状态
    TrajectorySaved --> [*]: 结束
```

---

## 4. 端到端数据流转

### 4.1 正常流程（Trajectory 保存）

```mermaid
sequenceDiagram
    participant A as Agent Loop
    participant B as DefaultAgent.step()
    participant C as save_trajectory()
    participant D as Disk

    A->>B: 执行单步
    B->>B: forward_with_handling()
    B->>B: add_step_to_trajectory()
    B-->>A: 返回 StepOutput
    A->>C: 调用保存
    C->>C: get_trajectory_data()
    C->>D: 写入 .traj 文件
```

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[StepOutput] --> I2[提取 trajectory 数据]
    end

    subgraph Process["处理阶段"]
        P1[构造 trajectory 对象] --> P2[添加 metadata]
        P2 --> P3[序列化为 JSON]
    end

    subgraph Output["输出阶段"]
        O1[写入 .traj 文件]
    end

    I2 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```python
# sweagent/types.py:44-78
Trajectory = list[TrajectoryStep]

class TrajectoryStep(TypedDict):
    action: str
    observation: str
    response: str
    state: dict[str, str]
    thought: str
    execution_time: float
    query: list[dict[str, Any]]
    extra_info: dict[str, Any]

class HistoryItem(_HistoryItem, total=False):
    agent: str
    is_demo: bool
    thought: str
    action: str | None
    tool_calls: list[dict[str, str]] | None
    thinking_blocks: list[dict[str, Any]] | None
```

**字段说明**：

| 字段 | 类型 | 用途 |
|-----|------|------|
| `trajectory` | `list[TrajectoryStep]` | 完整的执行历史 |
| `history` | `History` | LLM 对话历史 |
| `info` | `AgentInfo` | 执行元数据（提交、编辑文件等） |

### 5.2 主链路代码

```python
# sweagent/agent/agents.py:779-787
def save_trajectory(self) -> None:
    """Save the trajectory to disk.
    This includes the history, the environment state, and the model stats.
    """
    data = self.get_trajectory_data()
    assert self.traj_path is not None
    self.traj_path.write_text(json.dumps(data, indent=2))
```

**代码要点**：

1. **每步保存**：在 `run()` 循环中每完成一个 step 就调用保存
2. **完整状态**：包含 history、environment state、model stats
3. **JSON 格式**：便于人工阅读和工具解析

### 5.3 关键调用链

```text
DefaultAgent.run()                    [sweagent/agent/agents.py:1265]
  -> step()                           [sweagent/agent/agents.py:1235]
    -> forward_with_handling()        [sweagent/agent/agents.py:1062]
    -> add_step_to_trajectory()       [sweagent/agent/agents.py:1260]
  -> save_trajectory()                [sweagent/agent/agents.py:1286]
    -> get_trajectory_data()          [sweagent/agent/agents.py:785]
    -> 写入 .traj 文件
```

---

## 6. 设计意图与 Trade-off

### 6.1 SWE-agent 的选择

| 维度 | SWE-agent 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| 持久化粒度 | 每 step 保存 | 仅最终保存 | 支持断点续传，但 IO 开销增加 |
| 文件格式 | JSON | 二进制/数据库 | 可读性强，但体积较大 |
| revert 能力 | 不支持 | Kimi CLI 式回滚 | 架构简单，但无用户恢复能力 |
| 冲突处理 | 无 | 版本对比+协商 | 无需复杂逻辑，但功能受限 |

### 6.2 为什么这样设计？

**核心问题**：SWE-agent 面向的是自动化代码修复任务（如 SWE-bench），而非交互式开发场景。

**SWE-agent 的解决方案**：
- 代码依据：`sweagent/agent/agents.py:779`
- 设计意图：trajectory 用于事后分析和断点续传，而非运行时用户交互
- 带来的好处：
  - 架构简单，无需复杂的版本管理
  - trajectory 可用于离线分析和调试
  - 支持从任意 step 恢复执行
- 付出的代价：
  - 用户无法手动回滚到某个状态
  - 无文件级冲突检测能力

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "基础持久化"
    branch "SWE-agent"
    checkout "SWE-agent"
    commit id: "Trajectory 保存"
    checkout main
    branch "Kimi CLI"
    checkout "Kimi CLI"
    commit id: "Checkpoint 回滚"
    checkout main
    branch "Gemini CLI"
    checkout "Gemini CLI"
    commit id: "会话恢复"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| SWE-agent | 仅保存 trajectory，无 revert | 自动化批处理任务 |
| Kimi CLI | Checkpoint 支持完整状态回滚 | 交互式对话开发 |
| Gemini CLI | 分层记忆 + 会话恢复 | 复杂多轮任务 |
| Codex | 无内置 checkpoint，依赖外部 | 简单单次任务 |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| 任务完成 | step_output.done = True | `sweagent/agent/agents.py:1284` |
| 达到最大步数 | 超过 max_steps | Agent 配置 |
| 成本超限 | 超过 cost_limit | `sweagent/agent/models.py` |
| 上下文超限 | ContextWindowExceededError | `sweagent/agent/agents.py:1176` |

### 7.2 Trajectory 文件管理

| 情况 | 处理策略 | 代码位置 |
|---------|---------|---------|
| 文件已存在 | 覆盖写入 | `sweagent/agent/agents.py:787` |
| 路径未设置 | 断言失败 | `sweagent/agent/agents.py:786` |
| 磁盘满 | 抛出异常 | 系统级处理 |

### 7.3 断点续传

SWE-agent 支持从 trajectory 文件恢复执行：

```bash
sweagent run-replay --traj_path <path_to_traj_file>
```

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `sweagent/agent/agents.py` | 1265 | DefaultAgent.run() |
| Trajectory 保存 | `sweagent/agent/agents.py` | 779 | save_trajectory() |
| 数据结构 | `sweagent/types.py` | 44 | TrajectoryStep 定义 |
| 重放功能 | `sweagent/run/run_replay.py` | - | 从 trajectory 恢复 |
| 批量执行 | `sweagent/run/run_batch.py` | 276 | ThreadPoolExecutor 并行 |

---

## 9. 延伸阅读

- 相关文档：`docs/swe-agent/02-swe-agent-session-management.md`（Session 管理详细分析）
- 对比分析：`docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`（Kimi CLI 的 Checkpoint 回滚机制）
- 源码参考：`sweagent/agent/agents.py`（Agent 实现核心）

---

*✅ Verified: 基于 sweagent/agent/agents.py:779、sweagent/types.py:44 等源码分析*
*基于版本：SWE-agent (baseline 2026-02-08) | 最后更新：2026-02-25*
