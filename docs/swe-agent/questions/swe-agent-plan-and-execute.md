# SWE-agent Plan and Execute 模式

**结论先行**: SWE-agent **没有实现专门的 "plan and execute" 模式**。它采用**统一的 thought-action 循环**架构，将规划和执行集成在单个步骤中。模型在每个步骤中同时输出推理（thought）和命令（action），而不是先制定完整计划再执行。这种设计更接近 ReAct 范式，而非分阶段的计划-执行模式。

---

## 1. 无显式 Plan/Execute 模式

### 1.1 未发现模式切换机制

经过对 SWE-agent 代码的深入探索：

- **无** `ApprovalMode.PLAN` 或类似的模式枚举
- **无** `enter_plan_mode` / `exit_plan_mode` 工具
- **无** `/plan` 斜杠命令
- **无** plan phase 和 execute phase 的显式区分

### 1.2 与 Codex/Gemini CLI 的对比

| 特性 | Codex/Gemini CLI | SWE-agent |
|------|-----------------|-----------|
| Plan Mode | ✅ ModeKind/ApprovalMode | ❌ 无 |
| Execute Mode | ✅ Default/AUTO_EDIT | ❌ 无 |
| 模式切换工具 | ✅ enter/exit_plan_mode | ❌ 无 |
| 权限隔离 | ✅ Plan 模式下禁止编辑 | ❌ 无 |
| 计划文件 | ✅ 结构化输出 | ❌ 无 |

---

## 2. Thought-Action 循环架构

### 2.1 核心 Agent Loop

位于 `SWE-agent/sweagent/agent/agents.py`:

```python
def step(self) -> StepOutput:
    """Single step of the agent: query model, extract thought/action, execute."""
    # 1. 查询模型
    response = self.forward_with_handling()

    # 2. 提取 thought 和 action
    thought, action = self.parse_response(response)

    # 3. 执行 action
    observation = self.execute_action(action)

    # 4. 记录轨迹
    self.trajectory.add_step(thought, action, observation)

    return StepOutput(thought=thought, action=action, observation=observation)
```

### 2.2 主循环

```python
def run(
    self,
    env: SWEEnv,
    problem_statement: ProblemStatement | ProblemStatementConfig,
    output_dir: Path = Path("."),
) -> AgentRunResult:
    """Run the agent on a problem instance."""
    self.setup(env=env, problem_statement=problem_statement, output_dir=output_dir)

    # Run action/observation loop
    self._chook.on_run_start()
    step_output = StepOutput()
    while not step_output.done:
        step_output = self.step()  # 单步 thought+action+observation
        self.save_trajectory()
    self._chook.on_run_done(trajectory=self.trajectory, info=self.info)
```

**关键特点**:
- 循环**不分阶段**，每个迭代执行完整的 thought-action-observation
- 规划发生在每个 step 内部，而非独立阶段

---

## 3. Thought-Action 解析

### 3.1 ThoughtActionParser

位于 `SWE-agent/sweagent/tools/parsing.py`:

```python
class ThoughtActionParser(AbstractParseFunction, BaseModel):
    """
    Expects the model response to be a discussion followed by a command wrapped in backticks.
    Example:
    Let's look at the files in the current directory.
    ```
    ls -l
    ```
    """

    error_message: str = dedent("""\
    Your output was not formatted correctly. You must always include one discussion and one command as part of your response.
    Please make sure your output precisely matches the following format:
    DISCUSSION
    Discuss here with yourself about what your planning and what you're going to do in this step.
    ```
    command(s) that you're going to run
    ```
    """)
```

**隐式规划机制**:
- 错误消息模板明确要求模型 "Discuss here with yourself about what your planning"
- 这是**唯一的规划机制**，发生在每个 step 内部
- 模型需要自行决定下一步要做什么

---

## 4. 模板驱动的引导

### 4.1 TemplateConfig

位于 `SWE-agent/sweagent/agent/agents.py`:

```python
class TemplateConfig(BaseModel):
    system_template: str = ""
    instance_template: str = ""
    next_step_template: str = "Observation: {{observation}}"
    strategy_template: str | None = None
    demonstration_template: str | None = None
```

### 4.2 默认实例模板

位于 `SWE-agent/config/default.yaml`:

```yaml
instance_template: |-
  Can you help me implement the necessary changes to the repository so that the requirements specified in the <pr_description> are met?
  ...
  Follow these steps to resolve the issue:
  1. As a first step, it might be a good idea to find and read code relevant to the <pr_description>
  2. Create a script to reproduce the error and execute it with `python <filename.py>` using the bash tool, to confirm the error
  3. Edit the sourcecode of the repo to resolve the issue
  4. Rerun your reproduce script and confirm that the error is fixed!
  5. Think about edgecases and make sure your fix handles them as well
```

**设计特点**:
- 提供**程序性指导**而非严格的计划-执行工作流
- 告诉模型按什么步骤处理，但不强制执行

---

## 5. Retry Loop 机制

### 5.1 RetryAgent

位于 `SWE-agent/sweagent/agent/agents.py`:

```python
class RetryAgent(AbstractAgent):
    """Agent that retries solving the issue multiple times and selects the best solution."""
    def __init__(self, config: RetryAgentConfig):
        self.config = config.model_copy(deep=True)
        self._i_attempt = 0
        self._rloop: ScoreRetryLoop | ChooserRetryLoop | None = None
```

**替代方案**:
- SWE-agent 使用**多次尝试**而非计划-执行分离
- 提供 `RetryAgent` 机制进行多次尝试并选择最佳解决方案

---

## 6. 策略模板（有限的规划钩子）

位于 `SWE-agent/sweagent/agent/agents.py`:

```python
if self.templates.strategy_template is not None:
    templates.append(self.templates.strategy_template)
```

**限制**:
- 这是一个**最小化的钩子**用于战略规划
- 只是另一个添加到对话历史的模板
- **不是独立的规划阶段**

---

## 7. 架构对比

### 7.1 传统 Plan-and-Execute vs SWE-agent

```
┌─────────────────────────────────────────────────────────────────┐
│                  传统 Plan and Execute                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌──────────┐      ┌──────────┐      ┌──────────┐             │
│   │   Plan   │ ───▶ │  Confirm │ ───▶ │ Execute  │             │
│   │   Phase  │      │  by User │      │  Phase   │             │
│   └──────────┘      └──────────┘      └──────────┘             │
│                                                                 │
│   特点：                                                         │
│   • 显式的阶段分离                                               │
│   • Plan 阶段禁止执行                                            │
│   • 用户确认后进入执行                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    SWE-agent Thought-Action                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌────────────────────────────────────────────────────────┐   │
│   │                    Agent Loop                           │   │
│   │  ┌──────────┐    ┌──────────┐    ┌──────────┐         │   │
│   │  │  Thought │───▶│  Action  │───▶│Observation│         │   │
│   │  └──────────┘    └──────────┘    └──────────┘         │   │
│   │       ▲───────────────────────────────────────         │   │
│   └────────────────────────────────────────────────────────┘   │
│                                                                 │
│   特点：                                                         │
│   • 无显式阶段分离                                               │
│   • 每个步骤包含规划和执行                                        │
│   • 模型自行决定下一步操作                                        │
│   • 模板提供程序性指导                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 核心设计差异

| 维度 | Plan-and-Execute | SWE-agent Thought-Action |
|------|-----------------|------------------------|
| 阶段 | 明确的 Plan/Execute 阶段 | 无阶段，每步集成 thought-action |
| 规划时机 | 执行前一次性规划 | 每步都进行规划 |
| 用户确认 | 计划完成后显式确认 | 无专门的计划确认环节 |
| 工具权限 | Plan 阶段限制工具使用 | 无特殊限制 |
| 计划格式 | 结构化（XML/Markdown） | 自由文本在 thought 中 |
| 适用场景 | 复杂任务需要预规划 | 探索性任务，需要灵活调整 |

---

## 8. 工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                    SWE-agent 工作流程                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   用户输入问题                                                   │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────────────────────────────────┐                  │
│   │         Template Injection              │                  │
│   │  • system_template                      │                  │
│   │  • instance_template (程序性指导)        │                  │
│   │  • demonstration_template               │                  │
│   └─────────────────────────────────────────┘                  │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────────────────────────────────┐                  │
│   │           Agent Loop                    │                  │
│   │  ┌─────────────────────────────────┐   │                  │
│   │  │ Step 1                          │   │                  │
│   │  │  • Model Query                  │   │                  │
│   │  │  • Extract Thought (隐式规划)    │   │                  │
│   │  │  • Extract Action               │   │                  │
│   │  │  • Execute Action               │   │                  │
│   │  │  • Record Observation           │   │                  │
│   │  └─────────────────────────────────┘   │                  │
│   │              │                         │                  │
│   │              ▼                         │                  │
│   │  ┌─────────────────────────────────┐   │                  │
│   │  │ Step 2 (repeat until done)      │   │                  │
│   │  │  ...                            │   │                  │
│   │  └─────────────────────────────────┘   │                  │
│   └─────────────────────────────────────────┘                  │
│      │                                                          │
│      ▼                                                          │
│   ┌─────────────────────────────────────────┐                  │
│   │         Retry Loop (optional)           │                  │
│   │  • Multiple attempts                    │                  │
│   │  • Select best solution                 │                  │
│   └─────────────────────────────────────────┘                  │
│      │                                                          │
│      ▼                                                          │
│   返回结果                                                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `SWE-agent/sweagent/agent/agents.py` | 主 Agent Loop，`step()`, `run()`, `TemplateConfig` |
| `SWE-agent/sweagent/tools/parsing.py` | `ThoughtActionParser` thought-action 解析 |
| `SWE-agent/config/default.yaml` | 默认配置，包含程序性指导模板 |
| `SWE-agent/sweagent/agent/agents.py` | `RetryAgent` 多次尝试机制 |

---

## 10. 设计哲学与适用场景

### 10.1 SWE-agent 的设计选择

SWE-agent 采用 Thought-Action 循环而非 Plan-and-Execute，基于以下考量：

1. **探索性任务**: 软件工程任务往往需要边探索边调整，预先制定完整计划困难
2. **灵活性**: 每步都重新规划可以更好地适应新发现的信息
3. **简洁性**: 无需复杂的模式切换机制

### 10.2 适用场景对比

| 场景 | Plan-and-Execute | Thought-Action (SWE-agent) |
|------|-----------------|---------------------------|
| 需求明确的大型功能 | ✅ 适合预先规划 | ⚠️ 可能过度探索 |
| 需要多步协调的变更 | ✅ 清晰的执行路径 | ⚠️ 可能遗漏步骤 |
| Bug 修复 | ⚠️ 可能需要多次调整计划 | ✅ 灵活探索问题 |
| 探索性重构 | ⚠️ 难以预先规划 | ✅ 边探索边决策 |
| 代码审查辅助 | ✅ 清晰的审查计划 | ⚠️ 缺乏系统性 |

---

## 11. 总结

SWE-agent **不支持传统的 "plan and execute" 模式**，而是采用：

1. **集成的 Thought-Action 步骤** - 每个迭代结合推理和执行
2. **模板驱动的引导** - 通过 system/instance 模板提供程序性指导
3. **隐式规划** - 模型在每个 step 中自行决定下一步操作
4. **Retry 机制** - 多次尝试选择最佳方案，而非计划-执行分离

这种架构更适合**探索性、需要灵活调整**的软件工程任务，但对于需要严格预规划和多步协调的场景，可能不如显式的 Plan-and-Execute 模式有效。

---

*文档版本: 2026-02-22*
*基于代码版本: SWE-agent (baseline 2026-02-08)*
