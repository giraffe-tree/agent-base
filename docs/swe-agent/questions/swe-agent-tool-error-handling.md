# SWE-agent 工具调用错误处理机制

**结论先行**: SWE-agent 采用 **模板化错误反馈 + forward_with_handling() 集中处理 + Autosubmit 自动提交** 的架构，通过 `max_requeries=3` 限制格式错误重试次数，并实现 `attempt_autosubmission_after_error()` 在异常情况下自动提取 patch，确保在 CI/CD 场景下的高完成率。

---

## 1. 错误类型体系

### 1.1 异常层级定义

位于 `SWE-agent/sweagent/exceptions.py`：

```python
class FormatError(Exception):
    """模型响应无法正确解析为 thought 和 action 时抛出"""

class FunctionCallingFormatError(FormatError):
    """Function calling 解析器使用的格式错误异常"""
    def __init__(
        self,
        message: str,
        error_code: Literal[
            "missing", "multiple", "incorrect_args", "invalid_json",
            "invalid_command", "missing_arg", "unexpected_arg"
        ],
        **extra_info: Any,
    ):
        super().__init__(message + f" [error_code={error_code}]")
        self.message = message
        self.extra_info = {"error_code": error_code, **extra_info}

class ContextWindowExceededError(Exception):
    """LM 的上下文窗口超出时抛出"""

class CostLimitExceededError(Exception):
    """超出成本限制时抛出"""

class InstanceCostLimitExceededError(CostLimitExceededError):
    """单个任务实例的成本限制超出时抛出"""

class TotalCostLimitExceededError(CostLimitExceededError):
    """总成本限制超出时抛出"""

class InstanceCallLimitExceededError(CostLimitExceededError):
    """每个实例的调用限制超出时抛出"""

class ContentPolicyViolationError(Exception):
    """模型响应违反内容策略时抛出"""

class ModelConfigurationError(Exception):
    """模型配置无效/不应再重试时抛出"""
```

### 1.2 内部控制流异常

位于 `SWE-agent/sweagent/agent/agents.py`：

```python
class _BlockedActionError(Exception):
    """Agent 的 action 被阻止时抛出"""

class _RetryWithOutput(Exception):
    """用于内部控制流：带输出重试"""

class _RetryWithoutOutput(Exception):
    """用于内部控制流：不带输出重试"""

class _ExitForfeit(Exception):
    """用于内部控制流：放弃退出"""

class _TotalExecutionTimeExceeded(Exception):
    """用于内部控制流：总执行时间超限"""
```

### 1.3 错误类型层级图

```
┌─────────────────────────────────────────────────────────────────┐
│                    SWE-agent 错误类型体系                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  业务异常（外部可见）                                              │
│  ├─ FormatError                                                 │
│  │   └─ FunctionCallingFormatError                              │
│  ├─ ContextWindowExceededError                                  │
│  ├─ CostLimitExceededError                                      │
│  │   ├─ InstanceCostLimitExceededError                          │
│  │   ├─ TotalCostLimitExceededError                             │
│  │   └─ InstanceCallLimitExceededError                          │
│  └─ ContentPolicyViolationError                                 │
│                                                                 │
│  控制流异常（内部使用）                                            │
│  ├─ _BlockedActionError                                         │
│  ├─ _RetryWithOutput                                            │
│  ├─ _RetryWithoutOutput                                         │
│  ├─ _ExitForfeit                                                │
│  └─ _TotalExecutionTimeExceeded                                 │
│                                                                 │
│  环境异常（来自 swerex）                                           │
│  ├─ BashIncorrectSyntaxError                                    │
│  ├─ CommandTimeoutError                                         │
│  └─ SwerexException                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. forward_with_handling() 集中错误处理

### 2.1 核心处理逻辑

位于 `SWE-agent/sweagent/agent/agents.py:1062`：

```python
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """转发模型并处理错误，如果可以则重新查询模型。

    例如，如果模型输出的 bash 命令有语法错误，
    我们不会执行它，而是重新查询模型获取修正后的命令。

    Args:
        history: 要转发的历史记录

    Returns:
        step_output: 步骤输出
    """

    def handle_error_with_retry(exception: Exception, template: str, n_requeries: int) -> list[dict[str, str]]:
        """如果是格式/阻止列表/bash语法错误，则重新查询模型。"""
        self.logger.warning("Requerying model after %s (%dth requery)", type(exception).__name__, n_requeries)
        step: StepOutput = getattr(exception, "step", StepOutput())
        self.add_step_to_trajectory(step)
        exception_message = getattr(exception, "message", "")
        if not exception_message:
            try:
                exception_message = exception.args[0]
            except (IndexError, AttributeError):
                pass
        return self.get_model_requery_history(
            error_template=template,
            **step.to_template_format_dict(),
            **getattr(exception, "extra_info", {}),
            exception_message=exception_message,
        )

    n_format_fails = 0
    while n_format_fails < self.max_requeries:
        try:
            return self.forward(history)

        # 导致重新查询的错误
        except FormatError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.format_error_template,
                n_requeries=n_format_fails
            )
        except _BlockedActionError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.tools.config.filter.blocklist_error_template,
                n_requeries=n_format_fails,
            )
        except BashIncorrectSyntaxError as e:
            n_format_fails += 1
            history = handle_error_with_retry(
                exception=e,
                template=self.templates.shell_check_error_template,
                n_requeries=n_format_fails,
            )

        # 导致退出的错误
        except ContextWindowExceededError:
            return handle_error_with_autosubmission("exit_context", "Exit due to context window")
        except CostLimitExceededError:
            return handle_error_with_autosubmission("exit_cost", "Exit due to cost limit")
        except CommandTimeoutError:
            return handle_error_with_autosubmission("exit_command_timeout", "Exit due to multiple consecutive command timeouts")
        except SwerexException as e:
            return handle_error_with_autosubmission("exit_environment_error", f"Exit due to environment error: {e}")
        except Exception as e:
            return handle_error_with_autosubmission("exit_error", f"Exit due to unknown error: {e}")
```

### 2.2 错误分类处理矩阵

| 错误类型 | 处理方式 | 是否增加重试计数 | 模板来源 |
|---------|---------|-----------------|---------|
| `FormatError` | 重试 | ✅ 增加 | `format_error_template` |
| `_BlockedActionError` | 重试 | ✅ 增加 | `blocklist_error_template` |
| `ContentPolicyViolationError` | 重试 | ✅ 增加 | 无（简单重采样） |
| `BashIncorrectSyntaxError` | 重试 | ✅ 增加 | `shell_check_error_template` |
| `_RetryWithOutput` | 重试 | ❌ 不增加 | `next_step_template` |
| `_RetryWithoutOutput` | 重试 | ❌ 不增加 | 无（使用上一步模板） |
| `_ExitForfeit` | 退出+Autosubmit | - | - |
| `_TotalExecutionTimeExceeded` | 退出+Autosubmit | - | - |
| `CommandTimeoutError` | 退出+Autosubmit | - | - |
| `ContextWindowExceededError` | 退出+Autosubmit | - | - |
| `CostLimitExceededError` | 退出+Autosubmit | - | - |
| `SwerexException` | 退出+Autosubmit | - | - |
| `TotalCostLimitExceededError` | 直接抛出 | - | - |

### 2.3 处理流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                SWE-agent 错误处理流程                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   forward_with_handling()                                       │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────┐                                              │
│   │ 调用 forward │                                              │
│   └──────┬──────┘                                              │
│          │                                                      │
│    ┌─────┴─────┬─────────────────┐                             │
│    ▼           ▼                 ▼                             │
│  成功      可重试错误         致命错误                          │
│    │           │                 │                             │
│    │     ┌─────┴─────┐     ┌─────┴─────┐                      │
│    │     ▼           ▼     ▼           ▼                      │
│    │  FormatError  Blocked  Context    Cost                    │
│    │     │         Error     Window    Limit                   │
│    │     │           │        │         │                      │
│    │     ▼           ▼        ▼         ▼                      │
│    │  n_requeries   n_requeries  handle_error_with_autosubmit() │
│    │     < 3?         < 3?          │                           │
│    │     │             │            │                           │
│    │   是│           是│            │                           │
│    │     ▼             ▼            ▼                           │
│    │  重新查询        重新查询    提取 patch                      │
│    │  (带模板)       (带模板)   自动提交                          │
│    │     │             │            │                           │
│    │     └──────┬──────┘            │                           │
│    │            ▼                   │                           │
│    │       继续循环                 │                           │
│    │            │                  │                           │
│    │           >=3?                │                           │
│    │            │                  │                           │
│    │            ▼                  │                           │
│    │    Autosubmit (格式重试耗尽)   │                           │
│    │                               │                           │
│    └───────────────────────────────┘                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 模板化错误反馈

### 3.1 错误模板配置

位于 `SWE-agent/sweagent/agent/agents.py` 的 `TemplateConfig`：

```python
class TemplateConfig(BaseModel):
    """用于定义发送给 LM 的所有消息模板的配置"""

    shell_check_error_template: str = (
        "Your bash command contained syntax errors and was NOT executed. "
        "Please fix the syntax errors and try again. This can be the result "
        "of not adhering to the syntax for multi-line commands. Here is the output of `bash -n`:\n"
        "{{bash_stdout}}\n{{bash_stderr}}"
    )
    """bash 命令包含语法错误时的消息模板。
    可用变量: `bash_stdout`, `bash_stderr`
    """

    command_cancelled_timeout_template: str = (
        "The command '{{command}}' was cancelled because it took more than {{timeout}} seconds. "
        "Please try a different command that completes more quickly. "
        "Note: A common source of this error is if the command is interactive or requires user input "
        "(it is impossible to receive user input in the current environment, so the command will never complete)."
    )
    """命令因超时被取消时的消息模板。
    可用变量: `timeout`, `command`
    """

    next_step_truncated_observation_template: str = (
        "Observation: {{observation[:max_observation_length]}}<response clipped>"
        "<NOTE>Observations should not exceeded {{max_observation_length}} characters. "
        "{{elided_chars}} characters were elided. Please try a different command that produces less output "
        "or use head/tail/grep/redirect the output to a file. Do not use interactive pagers.</NOTE>"
    )
    """观察结果被截断时的消息模板。
    可用变量: `observation`, `max_observation_length`, `elided_chars`
    """
```

### 3.2 get_model_requery_history() 实现

```python
def get_model_requery_history(
    self, error_template: str, *, output: str, **kwargs: str | int | float | bool | None
) -> list[dict[str, str]]:
    """在发生以下错误之一后请求模型修正：
    1. 格式错误的输出（无法解析 action）
    2. 被阻止的 action（命令在阻止列表中）
    3. Bash 命令语法错误

    此函数添加基于错误模板的临时历史记录并查询模型。
    如果模型能够自我修正，错误记录不会成为历史的一部分
    （但会保存在 trajectory 中）。

    Args:
        error_template: 错误模板
        output: 模型输出
        **kwargs: 传递给错误模板的关键字参数

    Returns:
        重新查询后的模型输出
    """
    format_dict = {**kwargs, **self._get_format_dict()}
    error_template = Template(error_template).render(**format_dict)

    self.logger.warning(f"{error_template}")

    return self.messages + [
        {"role": "assistant", "content": output, "agent": self.name, "message_type": "assistant"},
        {"role": "user", "content": error_template, "agent": self.name, "message_type": "user"},
    ]
```

---

## 4. Autosubmit 自动提交机制

### 4.1 attempt_autosubmission_after_error()

```python
def attempt_autosubmission_after_error(self, step: StepOutput) -> StepOutput:
    """对于大多数异常，我们仍尝试提取 patch 并提交。
    这意味着我们将 `submit` 命令发送到运行时并解析输出。
    """
    self.logger.warning("Attempting autosubmission after error")
    step = step.model_copy(deep=True)
    step.done = True

    # 检查运行时是否仍然存活
    if not asyncio.run(self._env.deployment.is_alive(timeout=10)):
        self.logger.error("Runtime is no longer alive")
        try:
            last_trajectory_step = self.trajectory[-1]
        except IndexError:
            return step
        # 尝试从上一个 trajectory 步骤的 diff 恢复
        if "diff" not in last_trajectory_step["state"]:
            return step
        diff = last_trajectory_step["state"]["diff"]
        step.submission = diff
        if step.submission:
            step.observation = "Environment died unexpectedly. Exited (autosubmitted)"
            step.exit_status = f"submitted ({step.exit_status})"
        return step

    # 手动执行提交命令收集输出
    submission_command = "git add -A && git diff --cached > /root/model.patch"
    try:
        self._env.execute_command(submission_command, check=True)
    except Exception as e:
        self.logger.error("Failed to execute submission command, got %s", e)

    # 尝试从文件读取提交内容
    step = self.handle_submission(step, observation="", force_submission=True)
    if step.submission:
        self.logger.info("Exiting with autosubmission")
        step.observation = "Exited (autosubmitted)"
    return step
```

### 4.2 handle_submission()

```python
def handle_submission(self, step: StepOutput, *, observation="", force_submission: bool = False) -> StepOutput:
    """检查观察结果中是否有提交并处理。

    Args:
        step: 步骤对象
        observation: 如果指定，将使用此值而非 step.observation
        force_submission: 如果为 True，即使没有找到提交也会强制提交

    Returns:
        更新后的 step 对象（如果找到提交，submission 和 observation 会被更新）
    """
    step = step.model_copy(deep=True)
    is_submission = self.tools.check_for_submission_cmd(observation or step.observation)

    if is_submission or force_submission:
        try:
            submission = self._env.read_file("/root/model.patch", encoding="utf-8")
        except FileNotFoundError:
            self.logger.warning("Submission file not found, no submission was made")
            return step
        except Exception as e:
            self.logger.exception("Failed to read submission file, got %s", e)
            return step

        if submission.strip() != "":
            step.submission = submission
        else:
            step.submission = None
        step.observation = submission
        if not step.exit_status:
            step.exit_status = "submitted"
        elif step.submission:
            step.exit_status = f"submitted ({step.exit_status})"
        step.done = True
        self.logger.info(f"Found submission: {submission}")
    return step
```

---

## 5. 配置参数

### 5.1 DefaultAgentConfig

```python
class DefaultAgentConfig(BaseModel):
    """指定 agent 行为的配置对象"""

    max_requeries: int = 3
    """在错误后重新查询模型的最大次数，例如格式错误、
    被阻止的 action 或 bash 语法错误。
    """

    templates: TemplateConfig = Field(default_factory=TemplateConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)
    model: ModelConfig = Field(description="模型选项")
```

### 5.2 ToolConfig 超时配置

```python
class ToolConfig(BaseModel):
    """工具配置"""

    execution_timeout: int = 30
    """命令执行超时（秒），默认 30 秒"""

    total_execution_timeout: int = 1800
    """总执行超时（秒），默认 1800 秒（30 分钟）"""

    max_consecutive_execution_timeouts: int = 3
    """最大连续执行超时次数，达到后退出 agent"""
```

### 5.3 输出截断配置

```python
class TemplateConfig(BaseModel):
    max_observation_length: int = 100_000
    """观察结果超过此长度时截断（字符数）"""
```

---

## 6. 超时处理

### 6.1 连续超时计数

```python
# 初始化
self._n_consecutive_timeouts = 0
self._total_execution_time = 0.0

# 在 handle_action 中
except CommandTimeoutError:
    self._n_consecutive_timeouts += 1
    if self._n_consecutive_timeouts >= self.tools.config.max_consecutive_execution_timeouts:
        msg = "Exiting agent due to too many consecutive execution timeouts"
        self.logger.critical(msg)
        raise
    try:
        self._env.interrupt_session()
    except Exception as f:
        self.logger.exception("Failed to interrupt session after command timeout: %s", f)
        raise
    step.observation = Template(self.templates.command_cancelled_timeout_template).render(...)
else:
    self._n_consecutive_timeouts = 0  # 重置计数器
```

### 6.2 总执行时间检查

```python
def forward(self, history: list[dict[str, str]]) -> StepOutput:
    if self._total_execution_time > self.tools.config.total_execution_timeout:
        raise _TotalExecutionTimeExceeded()
    # ... 后续逻辑
```

---

## 7. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `SWE-agent/sweagent/exceptions.py` | 异常层级定义：`FormatError`、`CostLimitExceededError` 等 10+ 类型 |
| `SWE-agent/sweagent/agent/agents.py` | `forward_with_handling()` 集中错误处理，`attempt_autosubmission_after_error()` 自动提交 |
| `SWE-agent/sweagent/agent/agents.py:TemplateConfig` | 错误模板配置：`shell_check_error_template`、`command_cancelled_timeout_template` |
| `SWE-agent/sweagent/tools/tools.py` | `ToolConfig` 超时配置，`should_block_action()` 阻止列表检查 |

---

## 8. 设计亮点与启示

### 8.1 Autosubmit 的价值

SWE-agent 的核心设计哲学是**"尽可能完成任务"**：

| 场景 | 行为 | 价值 |
|------|------|------|
| 上下文溢出 | Autosubmit | 不浪费已做的工作 |
| 成本超限 | Autosubmit | 优雅降级而非异常退出 |
| 环境崩溃 | 从 diff 恢复 | 最大限度保留成果 |
| 格式重试耗尽 | Autosubmit | 确保有输出而非空退出 |

### 8.2 模板化错误反馈

相比简单的错误消息，模板化反馈：
1. **结构化**: 包含所有相关上下文（stdout/stderr、命令、超时时间）
2. **可配置**: 用户可自定义模板适应不同场景
3. **LLM 友好**: 清晰指明问题原因和修复方向

### 8.3 错误分类策略

SWE-agent 将错误分为三类：
1. **可重试（计数）**: FormatError、BlockedActionError、BashIncorrectSyntaxError
2. **可重试（不计数）**: _RetryWithOutput、_RetryWithoutOutput
3. **不可重试（Autosubmit）**: 所有其他错误

这种分类平衡了**恢复能力**与**无限循环风险**。

### 8.4 与 Kimi CLI 的对比

| 特性 | SWE-agent | Kimi CLI |
|------|-----------|----------|
| 恢复策略 | Autosubmit | Checkpoint + D-Mail |
| 重试限制 | max_requeries=3 | max_retries_per_step=3 |
| 错误反馈 | Jinja2 模板 | ToolError 封装 |
| 适用场景 | CI/CD 自动化 | 交互式对话 |

---

*文档版本: 2026-02-21*
*基于代码版本: SWE-agent (baseline 2026-02-08)*
