# Kimi CLI 工具调用错误处理机制

**结论先行**: Kimi CLI 采用 **四层 ToolError 继承体系** 与 **Checkpoint + D-Mail 时间旅行** 机制，通过 `ToolReturnValue` 统一封装成功与失败结果，实现了工具调用错误的优雅降级与状态回滚。核心特点是错误处理与上下文压缩、审批流程、MCP 超时深度集成。

---

## 1. 错误类型体系

### 1.1 ToolError 四层继承体系

位于 `kimi-cli/packages/kosong/src/kosong/tooling/error.py`：

```python
class ToolError(ToolReturnValue):
    """工具调用失败的基类"""
    def __init__(self, *, message: str, brief: str, output: str | ContentPart | list[ContentPart] = ""):
        super().__init__(
            is_error=True,
            output=([output] if isinstance(output, ContentPart) else output),
            message=message,
            display=[BriefDisplayBlock(text=brief)] if brief else [],
        )


class ToolNotFoundError(ToolError):
    """工具未找到"""
    def __init__(self, tool_name: str):
        super().__init__(
            message=f"Tool `{tool_name}` not found",
            brief=f"Tool `{tool_name}` not found",
        )


class ToolParseError(ToolError):
    """工具参数 JSON 解析失败"""
    def __init__(self, message: str):
        super().__init__(
            message=f"Error parsing JSON arguments: {message}",
            brief="Invalid arguments",
        )


class ToolValidateError(ToolError):
    """工具参数 Schema 校验失败"""
    def __init__(self, message: str):
        super().__init__(
            message=f"Error validating JSON arguments: {message}",
            brief="Invalid arguments",
        )


class ToolRuntimeError(ToolError):
    """工具运行时错误"""
    def __init__(self, message: str):
        super().__init__(
            message=f"Error running tool: {message}",
            brief="Tool runtime error",
        )
```

### 1.2 错误类型层级图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Kimi CLI ToolError 体系                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ToolReturnValue (基类)                                         │
│        ├─ is_error: bool                                         │
│        ├─ output: str | list[ContentPart]                       │
│        ├─ message: str                                          │
│        └─ display: list[DisplayBlock]                           │
│              │                                                   │
│       ┌──────┴──────┐                                           │
│       ▼             ▼                                           │
│   ToolOk       ToolError (基类)                                 │
│                    │                                             │
│        ┌───────────┼───────────┐                                │
│        ▼           ▼           ▼           ▼                    │
│  ToolNotFound  ToolParse   ToolValidate   ToolRuntime          │
│       │           │            │            │                   │
│   工具未找到    JSON解析    Schema校验    运行时异常            │
│               参数错误      参数错误       执行错误              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 统一返回封装

```python
class ToolReturnValue(BaseModel):
    """工具调用返回的统一格式"""

    is_error: bool
    """是否发生错误"""

    # 给模型看的
    output: str | list[ContentPart]
    """工具输出内容"""
    message: str
    """给模型的解释消息"""

    # 给用户看的
    display: list[DisplayBlock]
    """用户界面显示内容"""

    # 调试/测试
    extras: dict[str, JsonType] | None = None
```

---

## 2. 参数验证错误处理

### 2.1 CallableTool 验证流程

```python
class CallableTool(Tool, ABC):
    async def call(self, arguments: JsonType) -> ToolReturnValue:
        # 1. JSON Schema 验证
        try:
            jsonschema.validate(arguments, self.parameters)
        except jsonschema.ValidationError as e:
            return ToolValidateError(str(e))

        # 2. 参数分发
        if isinstance(arguments, list):
            ret = await self.__call__(*arguments)
        elif isinstance(arguments, dict):
            ret = await self.__call__(**arguments)
        else:
            ret = await self.__call__(arguments)

        # 3. 返回类型检查
        if not isinstance(ret, ToolReturnValue):
            return ToolError(
                message=f"Invalid return type: {type(ret)}",
                brief="Invalid return type",
            )
        return ret
```

### 2.2 CallableTool2 Pydantic 验证

```python
class CallableTool2[Params: BaseModel](ABC):
    async def call(self, arguments: JsonType) -> ToolReturnValue:
        # 使用 Pydantic 进行强类型验证
        try:
            params = self.params.model_validate(arguments)
        except pydantic.ValidationError as e:
            return ToolValidateError(str(e))

        ret = await self.__call__(params)
        # ... 返回类型检查
```

---

## 3. 重试机制

### 3.1 循环控制配置

位于 `kimi-cli/src/kimi_cli/config.py`：

```python
class LoopControl(BaseModel):
    """Agent 循环控制配置"""

    max_steps_per_turn: int = Field(default=100, ge=1)
    """单次 turn 的最大步数"""

    max_retries_per_step: int = Field(default=3, ge=1)
    """单步最大重试次数"""

    max_ralph_iterations: int = Field(default=0, ge=-1)
    """Ralph 模式的额外迭代次数，-1 表示无限制"""

    reserved_context_size: int = Field(default=50_000, ge=1000)
    """为 LLM 响应保留的 token 数，用于触发自动压缩"""
```

### 3.2 重试策略

使用 Python `tenacity` 库实现指数退避：

```python
from tenacity import retry, wait_exponential_jitter, stop_after_attempt

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((NetworkError, APIError)),
)
async def call_with_retry(self, ...):
    """仅对网络/API错误重试，业务错误不重试"""
    return await self.llm.call(...)
```

**关键决策**:
- 仅重试网络层/API层错误
- `ToolParseError`、`ToolValidateError` 等业务错误不重试（让 LLM 自纠正）

---

## 4. Checkpoint + D-Mail 恢复机制

### 4.1 Checkpoint 创建与回滚

```python
# 伪代码：基于 checkpoint-implementation 文档
class Context:
    def create_checkpoint(self) -> int:
        """创建 checkpoint，返回 checkpoint_id"""
        cid = next_checkpoint_id()
        checkpoints[cid] = len(history)  # 锚定到当前消息边界
        return cid

    def revert_to(self, checkpoint_id: int):
        """回滚到指定 checkpoint"""
        pos = checkpoints[checkpoint_id]
        history = history[:pos]
        prune_checkpoints_after(pos)
```

### 4.2 D-Mail 时间旅行

```python
# 伪代码：BackToTheFuture 异常处理
class BackToTheFuture(Exception):
    """触发回滚到指定 checkpoint 并注入新消息"""
    def __init__(self, checkpoint_id: int, messages: list[Message]):
        self.checkpoint_id = checkpoint_id
        self.messages = messages

# 在 _agent_loop 中捕获
async def _agent_loop(self):
    while not done:
        try:
            result = await self._step()
        except BackToTheFuture as bttf:
            # 1. 回滚到指定 checkpoint
            self.context.revert_to(bttf.checkpoint_id)
            # 2. 新建 checkpoint
            new_cid = self.context.create_checkpoint()
            # 3. 注入 D-Mail 消息
            self.context.append_messages(bttf.messages)
            continue
```

### 4.3 Checkpoint 触发场景

| 场景 | 行为 | 目的 |
|------|------|------|
| Turn 开始 | 创建 checkpoint 0 | 建立回退锚点 |
| 上下文压缩 | 重建 checkpoint | 新基线锚点 |
| D-Mail 回滚 | revert + 新建 checkpoint | 恢复状态并继续 |

---

## 5. MCP 超时处理

### 5.1 MCP 客户端配置

```python
class MCPClientConfig(BaseModel):
    """MCP 客户端配置"""

    tool_call_timeout_ms: int = 60000
    """工具调用超时时间（毫秒），默认 60 秒"""
```

### 5.2 超时处理流程

```python
async def call_mcp_tool(self, tool_name: str, arguments: dict) -> ToolReturnValue:
    try:
        # 使用 asyncio.wait_for 实现超时
        result = await asyncio.wait_for(
            self.mcp_client.call_tool(tool_name, arguments),
            timeout=self.config.mcp.client.tool_call_timeout_ms / 1000
        )
        return ToolOk(output=result)
    except asyncio.TimeoutError:
        return ToolRuntimeError(
            f"MCP tool '{tool_name}' timed out after "
            f"{self.config.mcp.client.tool_call_timeout_ms}ms"
        )
    except Exception as e:
        return ToolRuntimeError(str(e))
```

---

## 6. Token 溢出处理

### 6.1 自动压缩触发

```python
async def _check_and_compact(self):
    """检查并触发上下文压缩"""
    estimated_tokens = self.estimate_token_count()
    max_tokens = self.config.models[self.model].max_context_size
    reserved = self.config.loop_control.reserved_context_size

    if estimated_tokens + reserved >= max_tokens:
        # 触发压缩
        await self.compact_context()
```

### 6.2 压缩流程

1. 发送 `CompactionBegin` 事件
2. 生成压缩消息（摘要/保留消息）
3. 清空旧上下文
4. **新建 checkpoint**（关键）
5. 追加压缩后的消息
6. 发送 `CompactionEnd` 事件

---

## 7. 审批流程集成

### 7.1 Approval 类

```python
class Approval:
    """审批管理器"""

    def __init__(self, yolo: bool = False):
        self.yolo = yolo  # 是否自动审批模式
        self.approved_commands: set[str] = set()

    async def request(self, command: str, dangerous: bool = False) -> bool:
        """请求审批"""
        if self.yolo:
            return True
        if command in self.approved_commands:
            return True
        if not dangerous:
            return True

        # 交互式审批
        return await self.prompt_user(command)
```

### 7.2 危险命令检测

```python
DANGEROUS_PATTERNS = [
    r"rm\s+-rf",
    r">\s*/",
    r"curl\s+.*\s*\|",
    # ...
]

def is_dangerous(command: str) -> bool:
    """检测是否为危险命令"""
    return any(re.search(pattern, command) for pattern in DANGEROUS_PATTERNS)
```

---

## 8. 关键源码文件索引

| 文件路径 | 核心职责 |
|---------|---------|
| `kimi-cli/packages/kosong/src/kosong/tooling/error.py` | `ToolError`四层继承体系定义 |
| `kimi-cli/packages/kosong/src/kosong/tooling/__init__.py` | `ToolReturnValue`、`CallableTool`验证逻辑 |
| `kimi-cli/src/kimi_cli/config.py` | `LoopControl`配置，`MCPClientConfig`超时配置 |
| `kimi-cli/src/kimi_cli/soul/agent.py` | Agent 运行时，`Approval`集成 |
| `docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md` | Checkpoint + D-Mail 详细实现 |

---

## 9. 设计亮点与启示

### 9.1 四层错误继承体系

| 层级 | 错误类型 | 触发时机 | 恢复策略 |
|------|---------|---------|---------|
| 1 | ToolNotFoundError | 工具路由阶段 | 返回错误让 LLM 调整 |
| 2 | ToolParseError | JSON 解析阶段 | 返回错误让 LLM 重试 |
| 3 | ToolValidateError | Schema 校验阶段 | 返回错误让 LLM 修正 |
| 4 | ToolRuntimeError | 工具执行阶段 | 依赖具体错误类型 |

这种分层使得错误定位精确，恢复策略清晰。

### 9.2 统一返回封装

`ToolReturnValue` 统一封装成功与失败：
- `is_error` 标志区分状态
- `output`/`message` 给模型
- `display` 给用户
- 避免异常抛出影响控制流

### 9.3 Checkpoint 与错误恢复

Kimi CLI 的创新在于将错误恢复与 Checkpoint 机制结合：
- 工具调用失败不会直接退出
- 可通过 D-Mail 回滚到安全状态
- 压缩后重建 checkpoint 保证一致性

---

*文档版本: 2026-02-21*
*基于代码版本: kimi-cli (baseline 2026-02-08)*
