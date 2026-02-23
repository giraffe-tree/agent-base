# kimi-cli 概述文档

## 1. 项目简介

**kimi-cli** 是 Moonshot AI（月之暗面）推出的官方 CLI Agent，基于 Python 实现，提供智能编程助手功能，支持多种交互模式（shell、print、acp、wire）。

### 项目定位和目标
- Moonshot AI 官方命令行工具
- 支持多种运行模式：交互式 shell、非交互式 print、ACP 协议、Wire 协议
- 集成 Kimi 模型能力（支持 thinking 模式）
- 支持 MCP（Model Context Protocol）服务器扩展
- 提供丰富的内置工具（shell、file、web search 等）

### 技术栈
- **语言**: Python 3.10+
- **核心依赖**:
  - `kosong` - Moonshot AI 内部 SDK（消息处理、工具调用）
  - `typer` - CLI 框架
  - `fastmcp` - MCP 协议支持
  - `pydantic` - 数据验证
  - `aiohttp` - 异步 HTTP

### 官方仓库
- https://github.com/MoonshotAI/kimi-cli
- 文档: https://moonshotai.github.io/kimi-cli/

---

## 2. 架构概览

### 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI Layer                              │
│  (kimi-cli/src/kimi_cli/cli/__init__.py:54)                │
│  ├─ typer.Typer: 命令定义                                   │
│  ├─ 参数解析: --model, --yolo, --work-dir, etc             │
│  └─ 子命令: info, mcp, web, acp                            │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Shell/UI Layer                            │
│  (kimi-cli/src/kimi_cli/ui/shell/)                         │
│  ├─ 交互式界面                                              │
│  ├─ 用户输入处理                                            │
│  └─ Slash 命令解析                                          │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Soul Layer                               │
│  (kimi-cli/src/kimi_cli/soul/kimisoul.py:89)               │
│  ├─ KimiSoul: Agent 核心                                    │
│  ├─ _turn(): 单回合处理                                     │
│  ├─ _agent_loop(): Agent 循环                               │
│  └─ _step(): 单步执行                                       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Agent Layer                               │
│  (kimi-cli/src/kimi_cli/soul/agent.py)                     │
│  ├─ Agent: 代理定义                                         │
│  ├─ Runtime: 运行时上下文                                   │
│  └─ 工具集管理                                              │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Tools Layer                              │
│  (kimi-cli/src/kimi_cli/soul/toolset.py:71)                │
│  ├─ KimiToolset: 工具注册表                                 │
│  ├─ handle(): 工具调用处理                                  │
│  └─ MCP 服务器支持                                          │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Context Layer                             │
│  (kimi-cli/src/kimi_cli/soul/context.py)                   │
│  ├─ Context: 会话上下文                                     │
│  ├─ checkpoint(): 状态检查点                                │
│  └─ 消息历史管理                                            │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Model Layer                              │
│  (kimi-cli/src/kimi_cli/llm.py)                            │
│  ├─ LLM: 模型封装                                           │
│  ├─ ModelCapability: 能力检测                               │
│  └─ 流式响应处理                                            │
└─────────────────────────────────────────────────────────────┘
```

### 各层职责说明

| 层级 | 文件路径 | 核心职责 |
|------|----------|----------|
| CLI | `src/kimi_cli/cli/__init__.py` | 命令解析、配置加载、模式选择 |
| Shell | `src/kimi_cli/ui/shell/` | 交互式界面、用户输入、Slash 命令 |
| Soul | `src/kimi_cli/soul/kimisoul.py` | Agent 核心逻辑、循环控制 |
| Agent | `src/kimi_cli/soul/agent.py` | 代理定义、运行时管理 |
| Tools | `src/kimi_cli/soul/toolset.py` | 工具注册、执行、MCP 集成 |
| Context | `src/kimi_cli/soul/context.py` | 状态管理、检查点、历史记录 |
| Model | `src/kimi_cli/llm.py` | 模型调用、流式响应 |

### 核心组件列表

1. **KimiSoul** (`src/kimi_cli/soul/kimisoul.py`:89) - Agent 核心，管理主循环
2. **KimiToolset** (`src/kimi_cli/soul/toolset.py`:71) - 工具注册与执行
3. **Context** (`src/kimi_cli/soul/context.py`) - 会话上下文和检查点
4. **Agent** (`src/kimi_cli/soul/agent.py`) - 代理定义和运行时
5. **LLM** (`src/kimi_cli/llm.py`) - 模型调用封装
6. **Runtime** (`src/kimi_cli/soul/agent.py`) - 运行时上下文

---

## 3. 入口与 CLI

### 入口文件路径
```
kimi-cli/src/kimi_cli/cli/__init__.py:54
```

### CLI 参数解析方式

使用 `typer` 库进行命令解析：

```python
# src/kimi_cli/cli/__init__.py:34-41
cli = typer.Typer(
    epilog="""Documentation: https://moonshotai.github.io/kimi-cli/""",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Kimi, your next CLI agent.",
)

# 回调函数处理主命令参数
@cli.callback(invoke_without_command=True)
def kimi(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", "-V")] = False,
    model_name: Annotated[str | None, typer.Option("--model", "-m")] = None,
    yolo: Annotated[bool, typer.Option("--yolo", "--yes", "-y")] = False,
    print_mode: Annotated[bool, typer.Option("--print")] = False,
    acp_mode: Annotated[bool, typer.Option("--acp")] = False,
    wire_mode: Annotated[bool, typer.Option("--wire")] = False,
    # ... 更多参数
):
    ...
```

### 启动流程

```
src/kimi_cli/cli/__init__.py:54 kimi()
       │
       ├─ 解析全局参数 (--model, --yolo, --work-dir 等)
       │
       ├─ 确定运行模式:
       │   ├─ acp_mode ──▶ 启动 ACP 服务器
       │   ├─ wire_mode ──▶ 启动 Wire 服务器
       │   ├─ print_mode ──▶ 非交互式执行
       │   └─ 默认 ──▶ 交互式 Shell 模式
       │
       └─ 调用对应模式的入口函数
           │
           Shell 模式:
           └─ src/kimi_cli/ui/shell/__init__.py
               │
               ▼
           ┌─────────────────┐
           │ 初始化 Agent    │
           │ 加载配置        │
           │ 启动 REPL 循环  │
           └─────────────────┘
```

---

## 4. Agent 循环机制

### 主循环代码位置

```
kimi-cli/src/kimi_cli/soul/kimisoul.py:89 (KimiSoul 类)
kimi-cli/src/kimi_cli/soul/kimisoul.py:182 (run 方法)
kimi-cli/src/kimi_cli/soul/kimisoul.py:210 (_turn 方法)
```

### 流程图（文本形式）

```
┌─────────────────┐
│  KimiSoul.run() │  ──▶  kimisoul.py:182
│  (用户输入入口) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 发送 TurnBegin  │
│ 事件            │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│ Slash 命令?     │───▶│ 执行 Slash 命令 │
│ (以 / 开头)     │No   │ (skill/flow)    │
└────────┬────────┘     └─────────────────┘
         │Yes
         ▼
┌─────────────────────────────────────┐
│         _turn()                     │  ──▶  kimisoul.py:210
│                                     │
│  1. 检查 LLM 是否设置               │
│  2. 检查消息能力兼容性              │
│  3. checkpoint() 保存状态           │
│  4. context.append_message()        │
│  5. 调用 _agent_loop()              │
│                                     │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│       _agent_loop()                 │  ──▶  kimisoul.py (内部)
│                                     │
│  while True:                        │
│    ┌─────────────────────────────┐  │
│    │ _step()                     │  │
│    │                             │  │
│    │ 1. context.to_messages()    │  │
│    │ 2. llm.generate()           │  │
│    │ 3. 解析 assistant_message   │  │
│    │ 4. 检查工具调用             │  │
│    │                             │  │
│    │ 有工具调用?                 │  │
│    │ ├─ Yes: 执行工具            │  │
│    │ │   toolset.handle()        │  │
│    │ │   添加结果到上下文        │  │
│    │ │   continue 循环           │  │
│    │ │                           │  │
│    │ └─ No: break 循环           │  │
│    │                             │  │
│    └─────────────────────────────┘  │
│                                     │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ 发送 TurnEnd    │
│ 事件            │
└─────────────────┘
```

### 单次循环的执行步骤

**_turn() 方法** (kimisoul.py:210-220):

```python
async def _turn(self, user_message: Message) -> TurnOutcome:
    # 1. 检查 LLM
    if self._runtime.llm is None:
        raise LLMNotSet()

    # 2. 检查消息能力（多模态等）
    if missing_caps := check_message(user_message, self._runtime.llm.capabilities):
        raise LLMNotSupported(self._runtime.llm, list(missing_caps))

    # 3. 创建检查点
    await self._checkpoint()

    # 4. 添加用户消息到上下文
    await self._context.append_message(user_message)

    # 5. 进入 Agent 循环
    return await self._agent_loop()
```

**_step() 方法**:

```python
async def _step(self) -> StepOutcome:
    # 1. 构建消息列表
    messages = await self._context.to_messages()

    # 2. 调用 LLM
    response = await self._runtime.llm.generate(messages)

    # 3. 解析响应
    assistant_message = parse_response(response)

    # 4. 检查工具调用
    if tool_calls := assistant_message.tool_calls:
        for tool_call in tool_calls:
            # 执行工具
            result = await self._agent.toolset.handle(tool_call)
            # 添加结果到上下文
            await self._context.append_message(tool_result_to_message(result))
        return StepOutcome(stop_reason="tool_calls", ...)

    # 5. 无工具调用，结束回合
    return StepOutcome(stop_reason="no_tool_calls", ...)
```

### 循环终止条件

- **无工具调用** - 模型返回纯文本响应，没有 tool_calls
- **工具被拒绝** - 用户拒绝了工具执行（非 yolo 模式）
- **达到最大步数** - 配置限制
- **发生错误** - 网络错误、模型错误等

---

## 5. 工具系统

### 工具定义方式

```python
# kimi-cli/src/kimi_cli/tools/shell.py
class Shell(CallableTool):
    """Shell 命令执行工具"""
    name = "shell"
    description = "Execute shell commands"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds"},
        },
        "required": ["command"],
    }

    async def __call__(self, command: str, timeout: int = 60) -> ToolOk | ToolError:
        # 执行命令
        result = await execute_shell(command, timeout)
        return ToolOk(result)
```

### 工具注册表位置

```
kimi-cli/src/kimi_cli/soul/toolset.py:71
```

```python
class KimiToolset:
    def __init__(self) -> None:
        self._tool_dict: dict[str, ToolType] = {}
        self._mcp_servers: dict[str, MCPServerInfo] = {}

    def add(self, tool: ToolType) -> None:
        """注册工具"""
        self._tool_dict[tool.name] = tool

    def handle(self, tool_call: ToolCall) -> HandleResult:
        """处理工具调用"""
        tool = self._tool_dict.get(tool_call.function.name)
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                return_value=ToolNotFoundError(tool_call.function.name),
            )

        # 解析参数
        arguments = json.loads(tool_call.function.arguments or "{}")

        # 异步执行
        async def _call():
            ret = await tool.call(arguments)
            return ToolResult(tool_call_id=tool_call.id, return_value=ret)

        return asyncio.create_task(_call())

    @property
    def tools(self) -> list[Tool]:
        """返回所有工具定义（用于发送到模型）"""
        return [tool.base for tool in self._tool_dict.values()]
```

### 工具执行流程

```
模型返回 assistant_message
包含 tool_calls
       │
       ▼
┌─────────────────┐
│ 解析 tool_calls │
│ (ToolCall 列表) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 遍历 tool_calls │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ KimiToolset     │  ──▶  toolset.py:97
│ ::handle()      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 查找工具        │
│ _tool_dict[name]│
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐  ┌─────────────┐
│ 找到   │  │ 未找到      │
└───┬────┘  └─────────────┘
    │            │
    ▼            ▼
┌─────────────────┐  ┌─────────────────┐
│ 解析参数        │  │ 返回错误        │
│ json.loads()    │  │ ToolNotFound    │
└────────┬────────┘  └─────────────────┘
         │
         ▼
┌─────────────────┐
│ 异步执行工具    │
│ tool.call()     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 返回 ToolResult │
│ (异步任务)      │
└─────────────────┘
```

### 审批机制

```python
# kimi-cli/src/kimi_cli/tools/utils.py
class ToolRejectedError(Exception):
    """用户拒绝执行工具"""
    pass

# 在工具执行前的审批检查
async def check_approval(tool_call: ToolCall, runtime: Runtime) -> bool:
    if runtime.approval.is_yolo():
        # YOLO 模式：自动批准
        return True

    # 询问用户
    approval_request = ApprovalRequest(
        tool_name=tool_call.function.name,
        arguments=tool_call.function.arguments,
    )
    response = await runtime.denwa_renji.request_approval(approval_request)
    return response.approved
```

---

## 6. 状态管理

### Session 状态存储位置

```
kimi-cli/src/kimi_cli/soul/context.py
```

```python
class Context:
    """会话上下文管理"""

    def __init__(self, session_dir: Path, ...):
        self.session_dir = session_dir
        self.messages: list[Message] = []
        self.token_count: int = 0
        self.checkpoint_count: int = 0

    async def checkpoint(self, with_user_message: bool = False) -> None:
        """创建检查点"""
        # 保存当前状态到磁盘
        checkpoint_path = self.session_dir / f"checkpoint_{self.checkpoint_count}.json"
        data = {
            "messages": [msg.model_dump() for msg in self.messages],
            "token_count": self.token_count,
            "timestamp": datetime.now().isoformat(),
        }
        checkpoint_path.write_text(json.dumps(data, indent=2))
        self.checkpoint_count += 1

    async def append_message(self, message: Message) -> None:
        """添加消息并更新 Token 计数"""
        self.messages.append(message)
        self.token_count = await self._count_tokens()

    async def to_messages(self) -> list[Message]:
        """返回当前消息列表（用于发送到模型）"""
        return self.messages
```

### Checkpoint 机制

```
checkpoint()
    │
    ├─ 序列化当前消息历史
    ├─ 计算 Token 数量
    ├─ 保存到 session_dir/checkpoint_{n}.json
    └─ 递增 checkpoint_count

恢复:
    ├─ 加载最近的 checkpoint 文件
    ├─ 反序列化消息历史
    └─ 恢复 Context 状态
```

### 历史记录管理

**消息结构**:

```python
# kosong.message.Message (Moonshot SDK)
class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart]
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

class ContentPart(BaseModel):
    type: Literal["text", "image_url", ...]
    text: str | None = None
    image_url: dict | None = None
```

**Context 管理功能**:

- `append_message()` - 添加消息
- `to_messages()` - 获取用于模型的消息列表
- `checkpoint()` - 创建检查点
- `compact()` - 压缩历史（当 Token 超限）

### 状态恢复方式

```
恢复会话 (--continue):
1. 扫描 session_dir 中的 checkpoint 文件
2. 加载最新的 checkpoint
3. 恢复 messages 和 token_count
4. 继续对话

加载特定会话 (--session):
1. 根据 session_id 定位 session_dir
2. 加载对应的 checkpoint
3. 恢复上下文状态
```

---

## 7. 模型调用方式

### 支持的模型提供商

- **Moonshot AI** - Kimi 系列模型（默认）
- **OpenAI** - GPT 系列（通过配置）
- **Anthropic** - Claude 系列（通过配置）
- **其他** - 任何兼容 OpenAI API 的提供商

### 模型调用封装位置

```
kimi-cli/src/kimi_cli/llm.py
```

```python
class LLM:
    """LLM 封装"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.chat_provider = ChatProvider(
            model_name=config.model_name,
            api_key=config.api_key,
            base_url=config.base_url,
        )
        self.capabilities = self._detect_capabilities()

    async def generate(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> GenerateResponse:
        """生成响应"""
        response = await self.chat_provider.chat(
            messages=messages,
            tools=tools,
            stream=True,
            thinking=self.config.thinking,
        )
        return response

    @property
    def max_context_size(self) -> int:
        """最大上下文长度"""
        return MODEL_CONTEXT_LIMITS.get(self.config.model_name, 128000)
```

### 流式响应处理

```python
# kimi-cli/src/kimi_cli/soul/kimisoul.py
async def _step(self) -> StepOutcome:
    messages = await self._context.to_messages()

    # 流式生成
    stream = await self._runtime.llm.generate(messages)

    # 处理流式事件
    async for event in stream:
        match event.type:
            case "content":
                # 累积文本内容
                text += event.content
            case "tool_call":
                # 收集工具调用
                tool_calls.append(event.tool_call)
            case "thinking":
                # 收集思考过程
                thinking += event.thinking
            case "done":
                break
```

### Token 管理

```python
# kimi-cli/src/kimi_cli/soul/context.py
class Context:
    @property
    def _context_usage(self) -> float:
        """上下文使用率 (0.0 - 1.0)"""
        if self._runtime.llm is not None:
            return self.token_count / self._runtime.llm.max_context_size
        return 0.0

    async def _count_tokens(self) -> int:
        """计算当前消息的 Token 数量"""
        # 使用模型的 tokenizer
        return await self._runtime.llm.count_tokens(self.messages)

    async def _maybe_compact(self) -> None:
        """当 Token 超限时压缩历史"""
        if self._context_usage > COMPACT_THRESHOLD:
            await self._compaction.compact(self)
```

---

## 8. 数据流转图

```
┌────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                    │
└────────────────────────────────────────────────────────────────────────┘

用户输入 (Shell/CLI/Wire)
       │
       ▼
┌─────────────────┐
│ kimi()          │  ──▶  src/kimi_cli/cli/__init__.py:54
│ 参数解析        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Shell 模式?     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ KimiSoul.run()  │  ──▶  kimisoul.py:182
│ (处理用户输入)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Slash 命令?     │  ──▶  执行 skill/flow
└────────┬────────┘ No
         │
         ▼
┌─────────────────────────────────────┐
│ _turn(user_message)                 │  ──▶  kimisoul.py:210
│                                     │
│ 1. checkpoint()                     │
│    保存当前状态                     │
│       │                             │
│       ▼                             │
│   ┌─────────────┐                   │
│   │ Context     │                   │
│   │ ::checkpoint│                   │
│   └─────────────┘                   │
│                                     │
│ 2. context.append_message()         │
│    添加用户消息                     │
│                                     │
│ 3. _agent_loop()                    │
│    └─────────────────────────────┐  │
│      while True:                 │  │
│        │                         │  │
│        ▼                         │  │
│      ┌─────────────┐             │  │
│      │ _step()     │             │  │
│      │             │             │  │
│      │ 1. to_      │             │  │
│      │ messages()  │             │  │
│      │    │        │             │  │
│      │    ▼        │             │  │
│      │ ┌─────────┐ │             │  │
│      │ │ Context │ │             │  │
│      │ │ messages│ │             │  │
│      │ └────┬────┘ │             │  │
│      │      │      │             │  │
│      │      ▼      │             │  │
│      │ 2. llm.     │             │  │
│      │ generate()  │             │  │
│      │    │        │             │  │
│      │    ▼        │             │  │
│      │ ┌─────────┐ │             │  │
│      │ │ LLM     │ │             │  │
│      │ │ (kosong)│ │             │  │
│      │ └────┬────┘ │             │  │
│      │      │      │             │  │
│      │      ▼      │             │  │
│      │ 3. 解析     │             │  │
│      │ 响应        │             │  │
│      │      │      │             │  │
│      │      ▼      │             │  │
│      │ 4. 有工具?  │──Yes────────┤  │
│      │      │      │             │  │
│      │     No      │             │  │
│      │      │      │             │  │
│      │      ▼      │             │  │
│      │  break      │             │  │
│      │ (结束回合)  │             │  │
│      └─────────────┘             │  │
│                                  │  │
│    Yes 分支:                     │  │
│    ┌─────────────┐               │  │
│    │ toolset.    │               │  │
│    │ handle()    │               │  │
│    │    │        │               │  │
│    │    ▼        │               │  │
│    │ ┌─────────┐ │               │  │
│    │ │ Kimi    │ │               │  │
│    │ │ Toolset │ │               │  │
│    │ └────┬────┘ │               │  │
│    │      │      │               │  │
│    │      ▼      │               │  │
│    │ 执行工具    │               │  │
│    │ 添加结果    │               │  │
│    │ 到 context  │               │  │
│    │ continue    │               │  │
│    └─────────────┘               │  │
│                                  │  │
└──────────────────────────────────┘  │
         │
         ▼
┌─────────────────┐
│ TurnEnd         │
│ 事件            │
└─────────────────┘
```

### 关键数据结构定义

```python
# Wire 协议类型 (kimi-cli/src/kimi_cli/wire/types.py)
class TurnBegin(BaseModel):
    user_input: str | list[ContentPart]

class TurnEnd(BaseModel):
    pass

class StepBegin(BaseModel):
    step: int

class ToolCall(BaseModel):
    id: str
    function: FunctionCall

class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON string

class ToolResult(BaseModel):
    tool_call_id: str
    return_value: ToolOk | ToolError

# 状态快照
class StatusSnapshot(BaseModel):
    context_usage: float  # 0.0 - 1.0
    yolo_enabled: bool
```

---

## 9. 源码索引

### 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| CLI 入口 | `src/kimi_cli/cli/__init__.py` | 54 | kimi() 主函数 |
| KimiSoul | `src/kimi_cli/soul/kimisoul.py` | 89 | Agent 核心类 |
| run() | `src/kimi_cli/soul/kimisoul.py` | 182 | 用户输入处理 |
| _turn() | `src/kimi_cli/soul/kimisoul.py` | 210 | 单回合处理 |
| KimiToolset | `src/kimi_cli/soul/toolset.py` | 71 | 工具注册表 |
| handle() | `src/kimi_cli/soul/toolset.py` | 97 | 工具调用处理 |
| Context | `src/kimi_cli/soul/context.py` | - | 会话上下文 |
| checkpoint() | `src/kimi_cli/soul/context.py` | - | 检查点机制 |
| Agent | `src/kimi_cli/soul/agent.py` | - | 代理定义 |
| LLM | `src/kimi_cli/llm.py` | - | 模型封装 |

### 工具实现

| 工具 | 文件路径 | 说明 |
|------|----------|------|
| Shell | `src/kimi_cli/tools/shell/__init__.py` | Shell 命令执行 |
| File | `src/kimi_cli/tools/file/` | 文件读写工具集合 |
| Web | `src/kimi_cli/tools/web/` | 网络搜索与抓取 |
| Multiagent | `src/kimi_cli/tools/multiagent/` | 子代理任务与创建 |
| D-Mail/Think/Todo | `src/kimi_cli/tools/dmail/` 等 | 会话控制与辅助工具 |

### 配置类

| 配置 | 文件路径 | 说明 |
|------|----------|------|
| Config | `src/kimi_cli/config.py` | 全局配置 |
| LLM | `src/kimi_cli/llm.py` | 模型封装与能力 |
| Runtime | `src/kimi_cli/soul/agent.py` | 运行时与 Agent 加载 |

### Wire 协议

| 组件 | 文件路径 | 说明 |
|------|----------|------|
| Wire types | `src/kimi_cli/wire/types.py` | 类型定义 |
| Wire file | `src/kimi_cli/wire/file.py` | Wire 日志落盘 |

---

## 总结

kimi-cli 是一个功能丰富的 Python CLI Agent：

1. **多层架构** - CLI → Shell → Soul → Agent → Tools → Context → Model
2. **多种模式** - 支持交互式、非交互式、ACP、Wire 多种运行模式
3. **灵活工具** - 内置工具 + MCP 扩展机制
4. **状态管理** - Checkpoint 机制支持会话恢复
5. **Token 管理** - 自动压缩和上下文管理
