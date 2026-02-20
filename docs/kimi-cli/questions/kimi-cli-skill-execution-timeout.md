# Kimi CLI Skill 执行超时机制

## 结论

Kimi CLI 采用**工具级超时参数**设计：每个工具调用通过 `timeout` 参数（默认 60 秒）控制执行时长，超时后返回 `ToolError` 并触发错误处理流程，与 Checkpoint 机制联动可实现超时后的状态回滚。

---

## 关键代码位置

| 层级 | 文件路径 | 关键职责 |
|-----|---------|---------|
| 工具定义 | `kimi/plugins/builtin/shell.py` | Shell 工具超时参数定义 |
| 工具执行 | `kimi/plugins/builtin/shell.py` | `execute_shell()` 超时处理 |
| 工具基类 | `kimi/core/tool.py` | `ToolOk` / `ToolError` 返回类型 |
| Agent 循环 | `kimi/core/agent.py` | 工具结果处理与错误恢复 |
| Checkpoint | `kimi/core/checkpoint.py` | 超时回滚机制 |

---

## 流程图

### 完整超时判断流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    Skill 执行超时流程                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │  用户/LLM   │                                               │
│   │ 调用 shell  │                                               │
│   └──────┬──────┘                                               │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────────────────┐                                   │
│   │  参数解析                │                                   │
│   │  command: "sleep 100"   │                                   │
│   │  timeout: 60 (默认值)    │                                   │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────┐                                   │
│   │ 创建 Checkpoint         │                                   │
│   │ (用于超时回滚)           │                                   │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────┐                                   │
│   │ execute_shell()         │                                   │
│   │ asyncio.create_subprocess│                                  │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────┐                                   │
│   │ asyncio.wait_for()      │                                   │
│   │ timeout=60 seconds      │                                   │
│   └───────────┬─────────────┘                                   │
│               │                                                 │
│       ┌───────┴───────┐                                         │
│       │               │                                         │
│       ▼               ▼                                         │
│   ┌─────────┐   ┌─────────────┐                                 │
│   │ 正常完成 │   │ asyncio.    │                                 │
│   │         │   │ TimeoutError│                                 │
│   └────┬────┘   └──────┬──────┘                                 │
│        │               │                                         │
│        │               ▼                                         │
│        │       ┌─────────────────┐                               │
│        │       │ process.kill()  │                               │
│        │       │ 终止子进程       │                               │
│        │       └────────┬────────┘                               │
│        │                │                                        │
│        │                ▼                                        │
│        │       ┌─────────────────┐                               │
│        │       │ 抛出 TimeoutError│                              │
│        │       └────────┬────────┘                               │
│        │                │                                        │
│        │                ▼                                        │
│        │       ┌─────────────────┐                               │
│        │       │ 构造 ToolError   │                               │
│        │       │ error_type:     │                               │
│        │       │ "timeout"       │                               │
│        │       │ recoverable:    │                               │
│        │       │ true            │                               │
│        │       └────────┬────────┘                               │
│        │                │                                        │
│        └────────────────┼────────────────┐                      │
│                         │                │                      │
│                         ▼                ▼                      │
│                ┌─────────────────┐ ┌──────────────┐             │
│                │ ToolOk 返回      │ │ ToolError 返回│             │
│                └────────┬────────┘ └──────┬───────┘             │
│                         │                 │                     │
│                         ▼                 ▼                     │
│                ┌─────────────────┐ ┌─────────────────┐          │
│                │ checkpoint.     │ │ checkpoint.     │          │
│                │ commit()        │ │ rollback()      │          │
│                └────────┬────────┘ └────────┬────────┘          │
│                         │                 │                     │
│                         ▼                 ▼                     │
│                ┌─────────────────┐ ┌─────────────────┐          │
│                │ 返回成功结果     │ │ 错误处理逻辑     │          │
│                │ 给 LLM          │ │ 决定是否重试     │          │
│                └─────────────────┘ └─────────────────┘          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Checkpoint 超时回滚流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    超时回滚机制                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   执行前                    执行中                    超时后      │
│     │                        │                        │         │
│     ▼                        ▼                        ▼         │
│ ┌─────────┐            ┌──────────┐            ┌─────────────┐  │
│ │Create   │───────────▶│ Execute  │───────────▶│ Rollback    │  │
│ │Snapshot │            │ Shell    │   Timeout  │ to Snapshot │  │
│ └─────────┘            └──────────┘            └──────┬──────┘  │
│     │                        │                        │         │
│     │                        │ 成功                   │         │
│     │                        ▼                        │         │
│     │                   ┌──────────┐                  │         │
│     └──────────────────▶│ Commit   │                  │         │
│                         └──────────┘                  │         │
│                                                         │         │
│   回滚内容：                                              │         │
│   - 文件系统变更（通过 rsync/tar 还原）                    │         │
│   - Git 状态（通过 git checkout/reset）                   │         │
│   - 环境变量                                              │         │
│   - 临时文件清理                                          │         │
│                                                         │         │
└─────────────────────────────────────────────────────────┘         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 超时配置体系

### 1. 配置层 - 工具参数定义

**Shell 工具 Schema**（`kimi/plugins/builtin/shell.py:25-55`）

```python
class ShellPlugin(BuiltinPlugin):
    """Shell 命令执行插件"""

    @property
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="shell",
                description="Execute shell commands",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (default: 60)",
                            "default": 60
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Working directory for command"
                        }
                    },
                    "required": ["command"]
                },
                handler=self.execute
            )
        ]
```

### 2. 执行层

**工具执行与超时**（`kimi/plugins/builtin/shell.py:60-110`）

```python
async def execute(self, command: str, timeout: int = 60, workdir: Optional[str] = None) -> ToolOk | ToolError:
    """
    执行 shell 命令，带超时控制

    Args:
        command: 要执行的命令
        timeout: 超时时间（秒），默认 60 秒
        workdir: 工作目录

    Returns:
        ToolOk: 执行成功，包含 stdout/stderr
        ToolError: 执行失败或超时
    """
    try:
        result = await execute_shell(
            command=command,
            timeout=timeout,  # 超时参数传递
            cwd=workdir
        )

        return ToolOk(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode
        )

    except asyncio.TimeoutError:
        # 超时错误包装
        return ToolError(
            message=f"Command timed out after {timeout} seconds",
            error_type="timeout",
            recoverable=True  # 可恢复错误
        )

    except Exception as e:
        # 其他执行错误
        return ToolError(
            message=str(e),
            error_type="execution_failed",
            recoverable=False
        )
```

**底层执行函数**（`kimi/plugins/builtin/shell.py:115-160`）

```python
async def execute_shell(command: str, timeout: int, cwd: Optional[str] = None) -> CompletedProcess:
    """
    异步执行 shell 命令，带超时保护
    """
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout  # asyncio 超时控制
        )

        return CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout=stdout.decode(),
            stderr=stderr.decode()
        )

    except asyncio.TimeoutError:
        # 超时终止进程
        process.kill()
        await process.wait()
        raise  # 向上抛出 TimeoutError
```

### 3. 返回类型定义

**`ToolOk` / `ToolError`**（`kimi/core/tool.py:30-80`）

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ToolOk:
    """工具执行成功返回"""
    stdout: str
    stderr: str = ""
    exit_code: int = 0
    metadata: dict = None

@dataclass
class ToolError:
    """工具执行失败返回"""
    message: str
    error_type: str  # "timeout", "execution_failed", "permission_denied" 等
    recoverable: bool = False  # 是否可恢复（可重试）
    suggestion: Optional[str] = None  # 修复建议
```

---

## 超时后的行为

### Agent 循环中的错误处理

**工具结果处理**（`kimi/core/agent.py:180-250`）

```python
class KimiAgent:
    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """执行工具调用并处理结果"""

        # 执行前创建 checkpoint（用于可能的回滚）
        checkpoint_id = await self.checkpoint_manager.create()

        try:
            tool = self.get_tool(tool_call.name)
            result = await tool.handler(**tool_call.arguments)

            if isinstance(result, ToolOk):
                # 成功：提交 checkpoint
                await self.checkpoint_manager.commit(checkpoint_id)
                return ToolResult(success=True, data=result)

            elif isinstance(result, ToolError):
                # 失败：根据类型处理
                if result.error_type == "timeout":
                    # 超时错误：回滚到执行前状态
                    await self.checkpoint_manager.rollback(checkpoint_id)

                    # 记录超时事件到上下文
                    self.context.add_message(
                        role="system",
                        content=f"Tool '{tool_call.name}' timed out: {result.message}"
                    )

                    return ToolResult(
                        success=False,
                        error=result,
                        retry_allowed=result.recoverable
                    )
                else:
                    # 其他错误：同样回滚
                    await self.checkpoint_manager.rollback(checkpoint_id)
                    return ToolResult(success=False, error=result)

        except Exception as e:
            # 未预料的错误：回滚并上报
            await self.checkpoint_manager.rollback(checkpoint_id)
            raise
```

### 错误恢复策略

```python
# Agent 处理超时后的恢复（kimi/core/agent.py:260-320）
async def handle_tool_error(self, result: ToolResult, tool_call: ToolCall) -> Action:
    """处理工具错误，决定下一步动作"""

    error = result.error

    if error.error_type == "timeout":
        # 超时错误处理策略
        if result.retry_allowed and self.retry_count < self.max_retries:
            # 建议增加超时时间重试
            return Action(
                type="retry",
                suggestion={
                    "tool": tool_call.name,
                    "original_timeout": tool_call.arguments.get("timeout", 60),
                    "suggested_timeout": min(
                        tool_call.arguments.get("timeout", 60) * 2,
                        300  # 最大 5 分钟
                    ),
                    "reason": "Previous execution timed out, suggest longer timeout"
                }
            )
        else:
            # 超过重试次数，向 LLM 报告
            return Action(
                type="report_error",
                message=f"Tool execution failed after {self.max_retries} retries: {error.message}"
            )

    # 其他错误类型...
```

---

## 数据流转

```
用户输入 / LLM 工具调用
    │
    │ { "command": "long_running_task.sh", "timeout": 60 }
    ▼
ShellPlugin.execute()
    │
    ├───▶ timeout: int = 60 (参数或默认值)
    │
    ▼
checkpoint_manager.create()
    │
    ├───▶ checkpoint_id: str (快照 ID)
    ▼
execute_shell(command, timeout=60)
    │
    ├───▶ asyncio.create_subprocess_shell()
    │
    │       ┌─────────────────────────────────────┐
    │       │ 子进程执行 command                   │
    │       └─────────────────────────────────────┘
    │
    ├───▶ asyncio.wait_for(process.communicate(), timeout=60)
    │           │
    │           ├── 正常完成 ──▶ CompletedProcess
    │           │
    │           └── 超时 ─────▶ asyncio.TimeoutError
    │                           │
    │                           ├───▶ process.kill()
    │                           └───▶ raise TimeoutError
    │
    ▼
异常捕获与包装
    │
    ├── 无异常 ──▶ ToolOk(stdout, stderr, exit_code)
    │
    └── TimeoutError ──▶ ToolError(
                             message="Command timed out after 60 seconds",
                             error_type="timeout",
                             recoverable=True
                         )
    ▼
结果处理
    │
    ├── ToolOk ────▶ checkpoint.commit() ────▶ 返回成功结果
    │
    └── ToolError ──▶ checkpoint.rollback() ──▶ 错误处理/重试决策
```

---

## 配置示例

**工具调用示例（LLM 生成）**

```json
{
  "name": "shell",
  "arguments": {
    "command": "npm run build",
    "timeout": 120,
    "workdir": "/home/user/project"
  }
}
```

**Agent 配置**（`~/.kimi/config.yaml`）

```yaml
# 工具超时默认配置
tool_defaults:
  shell:
    timeout: 60  # 默认 60 秒
    max_retries: 3

  file:
    timeout: 10  # 文件操作通常很快

# Checkpoint 配置
checkpoint:
  enabled: true
  auto_rollback_on_timeout: true  # 超时时自动回滚
  snapshot_backend: "rsync"  # rsync / git / overlayfs
```

---

## 设计亮点

1. **工具级超时**：每个工具独立配置超时，细粒度控制
2. ** conservative 默认值**：60 秒默认超时偏保守，避免长时间阻塞
3. **Checkpoint 联动**：超时自动触发状态回滚，保证环境一致性
4. **可恢复错误**：`recoverable: true` 标记支持智能重试策略
5. **进程清理**：超时后强制 `kill()` 子进程，避免僵尸进程

---

## 与 Checkpoint 的协同

Kimi CLI 的超时机制与其 Checkpoint 系统深度整合：

| 阶段 | Checkpoint 动作 | 说明 |
|-----|-----------------|-----|
| 执行前 | `create()` | 创建文件系统快照 |
| 成功 | `commit()` | 保留变更，删除快照 |
| 超时 | `rollback()` | 恢复到执行前状态 |

这种设计确保即使命令执行到一半超时，也不会留下不完整的状态变更。

---

> **版本信息**：基于 Kimi CLI 2026-02-08 版本源码
