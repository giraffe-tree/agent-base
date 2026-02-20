# SWE-agent Skill 执行超时机制

## 结论

SWE-agent 采用**多层超时控制**策略：单命令超时（默认 30 秒）+ 总执行时长限制，超时后触发 `forward_with_handling()` 错误恢复流程，支持可重采样错误（格式错误、语法错误）的自动重试和最终兜底提交。

---

## 关键代码位置

| 层级 | 文件路径 | 关键职责 |
|-----|---------|---------|
| 配置定义 | `sweagent/agent/config.py` | `AgentConfig` 超时配置 |
| 执行环境 | `sweagent/environment/shell.py` | Shell 命令执行与超时 |
| 错误处理 | `sweagent/agent/forward.py` | `forward_with_handling()` 核心逻辑 |
| 错误类型 | `sweagent/agent/errors.py` | 可重采样错误定义 |
| 自动提交 | `sweagent/agent/submit.py` | 错误兜底提交机制 |

---

## 流程图

### 完整超时判断流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    Skill 执行超时流程                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │  Agent Loop │                                               │
│   │  开始迭代    │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ 检查总执行时长限制        │                                   │
│   │ total_execution_timeout │                                   │
│   └──────┬──────────────────┘                                   │
│          │                                                      │
│     超限 │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ 抛出 TotalExecutionTimeout│                                   │
│   └─────────────────────────┘                                   │
│          │                                                      │
│     未超限                                                     │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ forward_with_handling() │                                   │
│   └──────┬──────────────────┘                                   │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ LLM 生成动作             │                                   │
│   │ e.g., {"cmd": "bash",   │                                   │
│   │      "args": ["sleep 60"]}│                                  │
│   └──────┬──────────────────┘                                   │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ ShellEnvironment.execute │                                   │
│   │ timeout = 30 (默认)       │                                   │
│   └──────┬──────────────────┘                                   │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │ subprocess.run(timeout=30)│                                  │
│   └──────┬──────────────────┘                                   │
│          │                                                      │
│    ┌─────┴─────┐                                                │
│    │           │                                                │
│    ▼           ▼                                                │
│ ┌────────┐ ┌─────────────────┐                                  │
│ │ 成功    │ │ TimeoutExpired  │                                  │
│ │完成    │ │ 异常抛出         │                                  │
│ └───┬────┘ └────────┬────────┘                                  │
│     │               │                                           │
│     │               ▼                                           │
│     │      ┌─────────────────┐                                  │
│     │      │ process.kill()  │                                  │
│     │      │ 终止进程         │                                  │
│     │      └────────┬────────┘                                  │
│     │               │                                           │
│     │               ▼                                           │
│     │      ┌─────────────────┐                                  │
│     │      │ ExecutionResult │                                  │
│     │      │ timed_out: true │                                  │
│     │      └────────┬────────┘                                  │
│     │               │                                           │
│     └───────────────┼────────────────┐                          │
│                     │                │                          │
│                     ▼                ▼                          │
│            ┌────────────────┐ ┌─────────────────┐               │
│            │ 构造超时反馈    │ │ 返回正常结果     │               │
│            │ 添加到 history  │ │ 添加到 history  │               │
│            └───────┬────────┘ └─────────────────┘               │
│                    │                                            │
│                    ▼                                            │
│            ┌────────────────┐                                  │
│            │ forward_with_  │                                  │
│            │ handling()     │◀─────────────────────────────┐   │
│            │ 递归调用重试    │                              │   │
│            └────────────────┘                              │   │
│                                                            │   │
│            重试次数 < max_retries ──▶ 是 ──▶ 构造临时      │   │
│            否                                            history │
│            │                                                  │   │
│            ▼                                                  │   │
│    attempt_autosubmission()                                   │   │
│            │                                                  │   │
│            └──────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 超时配置体系

### 1. 配置层

**`AgentConfig` 配置类**（`sweagent/agent/config.py:25-80`）

```python
from pydantic import BaseModel, Field

class AgentConfig(BaseModel):
    """Agent 行为配置"""

    # 单命令执行超时（秒）
    execution_timeout: int = Field(
        default=30,
        description="Timeout for individual command execution in seconds"
    )

    # 总执行时长上限（秒）
    total_execution_timeout: int = Field(
        default=3600,
        description="Total execution time limit for the entire session"
    )

    # 提交命令配置
    submit_command: str = Field(
        default="submit",
        description="Command name for submitting the solution"
    )

    # 错误后自动提交
    attempt_autosubmission_after_error: bool = Field(
        default=True,
        description="Whether to attempt auto-submission when errors occur"
    )

    # 最大重试次数
    max_retries_on_error: int = Field(
        default=3,
        description="Maximum number of retries for recoverable errors"
    )
```

### 2. 执行层

**Shell 环境执行**（`sweagent/environment/shell.py:60-130`）

```python
import subprocess
from dataclasses import dataclass
from typing import Optional

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    timed_out: bool = False

class ShellEnvironment:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.total_start_time: Optional[float] = None

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None
    ) -> ExecutionResult:
        """
        执行命令，带超时控制

        Args:
            command: 要执行的命令
            timeout: 本次执行的超时（秒），默认使用 config.execution_timeout

        Returns:
            ExecutionResult: 包含执行结果和超时状态
        """
        # 检查总执行时长限制
        if self.total_start_time is None:
            self.total_start_time = time.time()

        total_elapsed = time.time() - self.total_start_time
        if total_elapsed > self.config.total_execution_timeout:
            raise TotalExecutionTimeout(
                f"Total execution time exceeded {self.config.total_execution_timeout}s"
            )

        # 使用配置的超时或传入的超时
        exec_timeout = timeout or self.config.execution_timeout

        start_time = time.time()

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=exec_timeout,  # 单命令超时
            )

            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                execution_time=time.time() - start_time,
                timed_out=False,
            )

        except subprocess.TimeoutExpired as e:
            # 超时处理
            execution_time = time.time() - start_time

            # 终止进程组
            if e.process:
                try:
                    e.process.kill()
                    e.process.wait(timeout=1)
                except:
                    pass

            return ExecutionResult(
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                exit_code=-1,
                execution_time=execution_time,
                timed_out=True,  # 标记为超时
            )
```

### 3. 错误处理与恢复

**`forward_with_handling()` 核心**（`sweagent/agent/forward.py:40-120`）

```python
from typing import TypeVar, List
from sweagent.agent.errors import (
    FormatError,
    BashIncorrectSyntaxError,
    NonRecoverableError,
)

T = TypeVar('T')

class AgentForwardHandler:
    def __init__(self, agent: 'SWEAgent'):
        self.agent = agent
        self.retry_count = 0

    def forward_with_handling(
        self,
        history: List[dict],
        max_retries: int = 3
    ) -> dict:
        """
        执行 Agent 前向步骤，带错误处理和重试

        流程：
        1. 调用 LLM 获取动作
        2. 执行动作
        3. 如遇可重采样错误，构造临时 history 重试
        4. 超过重试次数或不可恢复错误，尝试自动提交
        """
        try:
            # 正常前向执行
            return self._forward(history)

        except (FormatError, BashIncorrectSyntaxError) as e:
            # 可重采样错误：格式错误或 Bash 语法错误
            if self.retry_count < max_retries:
                self.retry_count += 1

                # 构造临时 history，包含错误信息
                error_history = history + [
                    {
                        "role": "assistant",
                        "content": f"Error: {e.message}",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response had an error: {e.message}\n"
                            f"Please correct and try again. "
                            f"Retry {self.retry_count}/{max_retries}"
                        ),
                    }
                ]

                # 重试
                return self.forward_with_handling(error_history, max_retries)

            else:
                # 超过重试次数
                raise MaxRetriesExceeded(f"Failed after {max_retries} retries: {e}")

        except subprocess.TimeoutExpired:
            # 执行超时：构造超时反馈
            timeout_history = history + [
                {
                    "role": "system",
                    "content": (
                        f"Command timed out after {self.agent.config.execution_timeout}s. "
                        "Consider breaking the command into smaller steps or increasing timeout."
                    ),
                }
            ]
            return self.forward_with_handling(timeout_history, max_retries)

        except NonRecoverableError as e:
            # 不可恢复错误：尝试自动提交
            if self.agent.config.attempt_autosubmission_after_error:
                return self.attempt_autosubmission(history, e)
            else:
                raise
```

### 4. 自动兜底提交

**错误后自动提交**（`sweagent/agent/submit.py:25-70`）

```python
class AutoSubmissionHandler:
    """当 Agent 遇到不可恢复错误时的兜底提交机制"""

    def __init__(self, environment: ShellEnvironment):
        self.env = environment

    def attempt_autosubmission(
        self,
        history: List[dict],
        error: Exception
    ) -> dict:
        """
        尝试自动提交当前工作进度

        即使遇到错误，也尝试保存已完成的工作
        """
        print(f"⚠️  Encountered error: {error}")
        print("🔄 Attempting auto-submission of current progress...")

        try:
            # 执行提交命令
            submit_result = self.env.execute(
                self.env.config.submit_command,
                timeout=30  # 提交命令单独设置较短超时
            )

            if submit_result.timed_out:
                # 提交也超时
                return {
                    "status": "error",
                    "error": "Submission timed out",
                    "original_error": str(error),
                    "partial_output": submit_result.stdout,
                }

            if submit_result.exit_code == 0:
                return {
                    "status": "submitted",
                    "message": "Auto-submitted after error",
                    "output": submit_result.stdout,
                    "original_error": str(error),
                }
            else:
                return {
                    "status": "submission_failed",
                    "error": submit_result.stderr,
                    "original_error": str(error),
                }

        except Exception as submit_error:
            return {
                "status": "fatal_error",
                "error": str(submit_error),
                "original_error": str(error),
            }
```

---

## 超时后的行为

### 可重采样错误类型

**错误分类**（`sweagent/agent/errors.py:10-50`）

```python
class AgentError(Exception):
    """Agent 错误基类"""
    pass

class RecoverableError(AgentError):
    """可恢复错误：可以重试"""
    recoverable = True

class NonRecoverableError(AgentError):
    """不可恢复错误：直接失败或尝试兜底"""
    recoverable = False

# ===== 可重采样错误（超时后可能触发）=====

class FormatError(RecoverableError):
    """LLM 输出格式错误"""
    def __init__(self, message: str, raw_output: str):
        self.message = message
        self.raw_output = raw_output

class BashIncorrectSyntaxError(RecoverableError):
    """Bash 命令语法错误"""
    def __init__(self, command: str, stderr: str):
        self.command = command
        self.stderr = stderr
        self.message = f"Bash syntax error in: {command}"

# ===== 超时相关错误 =====

class ExecutionTimeoutError(RecoverableError):
    """单命令执行超时"""
    def __init__(self, command: str, timeout: int):
        self.command = command
        self.timeout = timeout
        self.message = f"Command timed out after {timeout}s: {command}"

class TotalExecutionTimeout(NonRecoverableError):
    """总执行时长超限"""
    def __init__(self, timeout: int):
        self.timeout = timeout
        self.message = f"Total execution time exceeded {timeout}s"
```

### 错误恢复流程

```
┌─────────────────────────────────────────────────────────────────┐
│                      错误恢复决策流程                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────────┐                                           │
│   │   执行命令/动作  │                                           │
│   └────────┬────────┘                                           │
│            │                                                    │
│            ▼                                                    │
│   ┌─────────────────┐                                           │
│   │    是否出错？    │                                           │
│   └────┬─────┬──────┘                                           │
│        │     │                                                  │
│       是    否                                                  │
│        │     │                                                  │
│        │     ▼                                                  │
│        │  ┌─────────────────┐                                   │
│        │  │   正常继续       │                                   │
│        │  └─────────────────┘                                   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────┐                                           │
│   │  错误类型判断    │                                           │
│   └────┬─────┬──────┼────────┐                                  │
│        │     │      │        │                                  │
│        ▼     ▼      ▼        ▼                                  │
│   ┌────────┐┌────────┐┌──────────┐┌──────────────┐             │
│   │ Format ││  Bash  ││ Execution││    Total     │             │
│   │ Error  ││Syntax  ││ Timeout  ││   Timeout    │             │
│   │        ││Error   ││          ││              │             │
│   └───┬────┘└───┬────┘└────┬─────┘└──────┬───────┘             │
│       │         │          │             │                     │
│       │         │          │             │                     │
│       └─────────┴──────────┘             │                     │
│                 │                        │                     │
│            可恢复错误                     │ 不可恢复错误         │
│                 │                        │                     │
│                 ▼                        ▼                     │
│        ┌─────────────────┐    ┌─────────────────────┐          │
│        │ retry_count <   │    │ attempt_autosubmission│          │
│        │ max_retries?    │    │     ()?              │          │
│        └────┬─────┬──────┘    └────┬────────┬───────┘          │
│             │     │                │        │                   │
│            是    否               是       否                  │
│             │     │                │        │                   │
│             ▼     ▼                ▼        ▼                   │
│        ┌────────┐┌────────┐   ┌────────┐ ┌────────┐            │
│        │ 构造   ││ 抛出   │   │ 执行   │ │ 抛出   │            │
│        │ 临时   ││ Max    │   │ submit │ │ Fatal  │            │
│        │ history││Retries │   │ 命令   │ │ Error  │            │
│        │ + 重试 ││Exceeded│   │        │ │        │            │
│        └────────┘└────────┘   └────────┘ └────────┘            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 数据流转

```
配置加载 (sweagent.yml)
    │
    │ execution_timeout: 30
    │ total_execution_timeout: 3600
    │ attempt_autosubmission_after_error: true
    ▼
AgentConfig 实例化
    │
    ├───▶ execution_timeout: int = 30
    ├───▶ total_execution_timeout: int = 3600
    └───▶ attempt_autosubmission_after_error: bool = true
    ▼
ShellEnvironment 初始化
    │
    ├───▶ 记录 total_start_time
    ▼
Agent 循环开始
    │
    ├───▶ 检查总时长：time.time() - total_start_time < 3600?
    │       └── 超限 ──▶ TotalExecutionTimeout ──▶ 终止
    │
    └───▶ forward_with_handling(history)
            │
            ├───▶ LLM 生成动作
            │
            ├───▶ ShellEnvironment.execute(command, timeout=30)
            │       │
            │       ├───▶ subprocess.run(timeout=30)
            │       │           │
            │       │           ├── 正常 ──▶ CompletedProcess
            │       │           │
            │       │           └── 超时 ──▶ TimeoutExpired
            │       │                   │
            │       │                   ├───▶ process.kill()
            │       │                   └───▶ raise
            │       │
            │       ├───▶ 捕获 TimeoutExpired
            │       │           │
            │       │           └───▶ ExecutionResult(
            │       │                   timed_out=True,
            │       │                   exit_code=-1
            │       │               )
            │       │
            │       └───▶ 返回结果
            │
            ├───▶ 判断结果
            │       │
            │       ├── timed_out=True ──▶ 构造超时反馈
            │       │                           │
            │       │                           └───▶ history + timeout_msg
            │       │
            │       └── 正常 ──▶ 继续
            │
            ├───▶ 检查是否可重试
            │       │
            │       ├── retry_count < 3 ──▶ 递归调用重试
            │       │
            │       └── retry_count >= 3
            │               │
            │               └───▶ attempt_autosubmission()
            │                       │
            │                       └───▶ 执行 submit 命令
            │                               │
            │                               ├── 成功 ──▶ 返回结果
            │                               └── 失败 ──▶ 错误报告
            │
            └───▶ 返回最终结果
```

---

## 配置示例

**`sweagent.yml`**

```yaml
agent:
  # 超时配置
  execution_timeout: 60  # 单命令 60 秒（适用于慢速编译）
  total_execution_timeout: 7200  # 总共 2 小时

  # 错误处理
  max_retries_on_error: 5  # 格式错误最多重试 5 次
  attempt_autosubmission_after_error: true  # 出错后尝试提交

  # 提交配置
  submit_command: "submit"

environment:
  type: "docker"
  image: "sweagent/swe-env:latest"

# 针对不同任务的覆盖配置
profiles:
  fast_test:
    execution_timeout: 10
    total_execution_timeout: 300

  slow_build:
    execution_timeout: 300
    total_execution_timeout: 18000  # 5 小时
```

---

## 设计亮点

1. **双层超时**：单命令 + 总时长双重保护，防止无限运行
2. **学术研究导向**：`forward_with_handling()` 设计支持实验可重复性
3. **可重采样错误**：区分 recoverable/non-recoverable 错误，智能重试
4. **自动兜底提交**：即使失败也尝试保存进度，适合自动化评测
5. **History Processors**：超时后构造反馈 history，让 LLM 自我纠正

---

## 超时与其他机制的关系

```
┌─────────────────────────────────────────────────────────────────┐
│              SWE-agent 超时与相关机制的关系                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌──────────────┐                                              │
│   │ AgentConfig  │                                              │
│   │              │                                              │
│   │ execution_   │──────▶ ShellEnvironment.execute()           │
│   │ timeout      │              │                               │
│   │              │              │                               │
│   │ total_       │──────▶ 总时长检查 ──▶ 全局终止               │
│   │ execution_   │                                              │
│   │ timeout      │                                              │
│   │              │──────▶ forward_with_handling()              │
│   │ max_retries  │              │                               │
│   │ _on_error    │              ▼                               │
│   │              │      ┌───────────────┐                       │
│   │ attempt_     │      │ 可重采样错误   │                       │
│   │ autosubmis-  │      │ FormatError   │                       │
│   │ sion_after_  │      │ BashSyntaxErr │                       │
│   │ error        │      └───────┬───────┘                       │
│   └──────────────┘              │                               │
│                                 │ retry + temp history          │
│                                 ▼                               │
│                         ┌───────────────┐                       │
│                         │ 超过重试次数   │                       │
│                         └───────┬───────┘                       │
│                                 │                               │
│                                 ▼                               │
│                         ┌───────────────┐                       │
│                         │ attempt_      │                       │
│                         │ autosubmission│                       │
│                         │ ()            │                       │
│                         └───────────────┘                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

> **版本信息**：基于 SWE-agent 2026-02-08 版本源码
