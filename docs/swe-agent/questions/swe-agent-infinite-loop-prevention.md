# SWE-agent 如何避免 Tool 无限循环调用

**结论先行**: SWE-agent 通过 **`max_requeries` 重试上限** + **Autosubmit 自动提交** + **连续超时计数器**防止 tool 无限循环。核心设计是"优雅完成"，即使发生异常也尝试提取 patch 提交结果，而非强制中断。

---

## 1. 核心防护机制：max_requeries

位于 `SWE-agent/sweagent/agent/agents.py:451`：

```python
class DefaultAgent(AbstractAgent):
    def __init__(
        self,
        *,
        max_requeries: int = 3,  # ← 重试上限
        ...
    ):
        self.max_requeries = max_requeries
        self._n_consecutive_timeouts = 0
```

### 1.1 forward_with_handling() 重试控制

```python
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """Forward the model and handle errors, requerying the model if we can."""

    def handle_error_with_retry(
        exception: Exception, template: str, n_requeries: int
    ) -> list[dict[str, str]]:
        """Requeries the model if the error is a format/blocklist/bash syntax error."""
        self.logger.warning(
            "Requerying model after %s (%dth requery)",
            type(exception).__name__, n_requeries
        )
        # 添加错误到 trajectory（历史记录）
        step: StepOutput = getattr(exception, "step", StepOutput())
        self.add_step_to_trajectory(step)

        # 构建重新查询的历史
        return self.get_model_requery_history(
            error_template=template,
            **step.to_template_format_dict(),
            exception_message=str(exception),
        )

    n_format_fails = 0
    while n_format_fails < self.max_requeries:
        try:
            return self.forward(history)

        # 可重试错误（增加计数）
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

        # 可重试错误（不增加计数）
        except _RetryWithOutput as e:
            history = handle_error_with_retry(
                exception=e,
                template=self.templates.next_step_template,
                n_requeries=n_format_fails,
            )
        except _RetryWithoutOutput:
            pass  # 简单重试，不增加计数

        # 致命错误 → Autosubmit
        except ContextWindowExceededError:
            return handle_error_with_autosubmission("exit_context", "...")
        except CostLimitExceededError:
            return handle_error_with_autosubmission("exit_cost", "...")
        except CommandTimeoutError:
            return handle_error_with_autosubmission("exit_command_timeout", "...")

    # 重试耗尽 → Autosubmit
    self.logger.exception("Exit due to repeated format errors")
    return handle_error_with_autosubmission(
        "exit_format",
        "Exit due to repeated format/blocklist/bash syntax errors",
    )
```

**分类处理策略**:

| 错误类型 | 增加计数 | 说明 |
|---------|---------|------|
| `FormatError` | ✅ | 格式错误，需要 LLM 修正 |
| `_BlockedActionError` | ✅ | 被阻止的操作 |
| `BashIncorrectSyntaxError` | ✅ | Bash 语法错误 |
| `_RetryWithOutput` | ❌ | 带输出的简单重试 |
| `_RetryWithoutOutput` | ❌ | 不带输出的简单重试 |

---

## 2. Autosubmit 自动提交机制

位于 `SWE-agent/sweagent/agent/agents.py:823`：

### 2.1 核心实现

```python
def attempt_autosubmission_after_error(self, step: StepOutput) -> StepOutput:
    """对于大多数异常，我们仍尝试提取 patch 并提交。"""
    self.logger.warning("Attempting autosubmission after error")
    step = step.model_copy(deep=True)
    step.done = True  # 标记为完成

    # 检查运行时是否存活
    if not asyncio.run(self._env.deployment.is_alive(timeout=10)):
        self.logger.error("Runtime is no longer alive")
        # 尝试从最后一个 trajectory 步骤的 diff 恢复
        try:
            last_trajectory_step = self.trajectory[-1]
            diff = last_trajectory_step["state"].get("diff")
            if diff:
                step.submission = diff
                step.observation = "Environment died unexpectedly. Exited (autosubmitted)"
                step.exit_status = f"submitted ({step.exit_status})"
        except IndexError:
            pass
        return step

    # 手动执行提交命令
    submission_command = "git add -A && git diff --cached > /root/model.patch"
    try:
        self._env.execute_command(submission_command, check=True)
    except Exception as e:
        self.logger.error("Failed to execute submission command: %s", e)

    # 读取并提交 patch
    step = self.handle_submission(step, observation="", force_submission=True)
    if step.submission:
        step.observation = "Exited (autosubmitted)"
    return step
```

**设计哲学**: 即使遇到错误，也尽可能完成任务并提交结果，而不是直接失败。

---

## 3. 连续超时计数器

位于 `SWE-agent/sweagent/agent/agents.py:968`：

```python
# 初始化
self._n_consecutive_timeouts = 0

# 在 handle_action 中
except CommandTimeoutError:
    self._n_consecutive_timeouts += 1
    if self._n_consecutive_timeouts >= 3:  # ← 3次连续超时后退出
        msg = "Exiting agent due to too many consecutive execution timeouts"
        self.logger.critical(msg)
        raise  # 终止 agent
    try:
        self._env.interrupt_session()
    except Exception as f:
        self.logger.exception("Failed to interrupt session: %s", f)
        raise
    # 使用超时模板通知 LLM
    step.observation = Template(
        self.templates.command_cancelled_timeout_template
    ).render(...)
else:
    self._n_consecutive_timeouts = 0  # 成功则重置计数器
```

**防护逻辑**:
- 连续超时达到 3 次强制退出
- 成功执行一次即重置计数器
- 允许偶尔的超时（如网络抖动）

---

## 4. 总执行时间限制

位于 `SWE-agent/sweagent/agent/agents.py:1018`：

```python
def forward(self, history: list[dict[str, str]]) -> StepOutput:
    # 检查总执行时间
    if self._total_execution_time > self.tools.config.total_execution_timeout:
        raise _TotalExecutionTimeExceeded()
    # ... 后续逻辑
```

配置：
```python
class ToolConfig(BaseModel):
    total_execution_timeout: int = 1800  # 30 分钟
```

---

## 5. 防循环流程图

```
┌─────────────────────────────────────────────────────────────────┐
│               SWE-agent Tool 调用防循环流程                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   LLM 输出 action                                                │
│        │                                                        │
│        ▼                                                        │
│   ┌───────────────────┐                                        │
│   │ parse_actions()   │                                        │
│   └─────────┬─────────┘                                        │
│             │                                                   │
│     ┌───────┴───────┬──────────────────┐                      │
│     ▼               ▼                  ▼                      │
│   解析成功       解析失败            被阻止                      │
│     │               │                  │                       │
│     │          ┌────┘                  │                       │
│     │          ▼                       ▼                       │
│     │     n_format_fails             blocklist                │
│     │          │                        │                       │
│     │          ▼                        ▼                       │
│     │     ┌─────────┐              同 FormatError             │
│     │     │ < 3?    │                                           │
│     │     └────┬────┘                                           │
│     │       是│                                                 │
│     │          ▼                                                 │
│     │     模板化错误反馈                                          │
│     │     (Jinja2 template)                                      │
│     │          │                                                 │
│     │          ▼                                                 │
│     └───────重新查询 LLM ─────────────────┘                     │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                   工具执行阶段                           │   │
│   │                                                         │   │
│   │   execute_command()                                     │   │
│   │        │                                                │   │
│   │        ▼                                                │   │
│   │   ┌─────────────┐                                      │   │
│   │   │ 超时?       │────是────▶ _n_consecutive_timeouts++  │   │
│   │   └─────────────┘                │                      │   │
│   │        │否                        │ >= 3?               │   │
│   │        ▼                          ▼                      │   │
│   │   正常结束              Autosubmit + 退出                │   │
│   │        │                          │                      │   │
│   │        ▼                          ▼                      │   │
│   │   _n_consecutive_timeouts = 0   提取 patch               │   │
│   │   (重置计数器)                  自动提交                  │   │
│   │                                                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                   重试耗尽后                             │   │
│   │                                                         │   │
│   │   n_format_fails >= 3                                   │   │
│   │        │                                                │   │
│   │        ▼                                                │   │
│   │   ┌───────────────────┐                                │   │
│   │   │ handle_error_with │                                │   │
│   │   │ _autosubmission() │                                │   │
│   │   └─────────┬─────────┘                                │   │
│   │             │                                           │   │
│   │             ▼                                           │   │
│   │   1. 执行 git diff 生成 patch                           │   │
│   │   2. 读取 /root/model.patch                             │   │
│   │   3. 将 patch 作为 submission                           │   │
│   │   4. 标记 step.done = True                              │   │
│   │             │                                           │   │
│   │             ▼                                           │   │
│   │   返回 AgentRunResult (info, trajectory)                │   │
│   │                                                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 与其他 Agent 的对比

| 防护机制 | SWE-agent | Gemini CLI | Kimi CLI | Codex | OpenCode |
|---------|-----------|------------|----------|-------|----------|
| **重试上限** | ✅ max_requeries=3 | ✅ 3次 | ✅ 3次 | ✅ 5/4次 | ❌ 无 |
| **自动提交** | ✅ Autosubmit | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **连续超时计数** | ✅ 3次退出 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **总执行时间** | ✅ 30分钟 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **模板化错误** | ✅ Jinja2 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **状态回滚** | ❌ 无 | ❌ 无 | ✅ Checkpoint | ❌ 无 | ❌ 无 |

---

## 7. 总结

SWE-agent 的防循环设计哲学是**"优雅完成"**：

1. **重试限制**: `max_requeries=3` 限制格式错误重试
2. **Autosubmit**: 即使异常也尝试提交 patch，确保 CI/CD 成功率
3. **超时防护**: 连续超时计数器（3次）和总执行时间（30分钟）兜底
4. **模板化反馈**: Jinja2 模板给 LLM 清晰的修正指导

SWE-agent 的独特之处在于**不追求"完美完成"，而是"尽可能完成"**。即使发生错误，也会尝试提取已做的工作（patch）并提交，这在自动化评测场景（如 SWE-bench）中尤为重要。

---

*文档版本: 2026-02-21*
*基于代码版本: SWE-agent (baseline 2026-02-08)*
