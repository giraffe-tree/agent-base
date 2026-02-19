# Tool System（SWE-agent）

本文基于 `./SWE-agent/sweagent/tools` 源码，解释 SWE-agent 的工具系统架构——从 Bundle 配置、Command 抽象到多解析器支持的完整链路。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────────┐
│  配置层：ToolConfig 定义工具集                                       │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolConfig (Pydantic BaseModel)                                ││
│  │  ├── bundles: list[Bundle]        工具包列表                     ││
│  │  ├── enable_bash_tool: bool       启用 bash 工具                ││
│  │  ├── filter: ToolFilterConfig     命令过滤器                     ││
│  │  │   ├── blocklist                前缀匹配阻止                   ││
│  │  │   ├── blocklist_standalone     完全匹配阻止                   ││
│  │  │   └── block_unless_regex       条件阻止                       ││
│  │  ├── parse_function               输出解析器                     ││
│  │  │   ├── FunctionCallingParser    函数调用格式                   ││
│  │  │   ├── ThoughtActionParser      思考-行动格式                  ││
│  │  │   ├── JsonParser               JSON 格式                     ││
│  │  │   └── ...                      更多解析器                     ││
│  │  └── env_variables                 环境变量配置                  ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Bundle 层：工具包组织                                               │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Bundle                                                         ││
│  │  ├── path: Path                   工具包目录                     ││
│  │  ├── config.yaml                  工具定义配置                   ││
│  │  │   └── tools: {name: {docstring, signature, arguments}}       ││
│  │  ├── commands: list[Command]      解析后的命令                   ││
│  │  └── state_command: str           状态获取命令                   ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  执行层：ToolHandler 管理工具生命周期                                 │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolHandler                                                    ││
│  │  ├── install(env)                 安装工具到环境                 ││
│  │  ├── reset(env)                   重置工具状态                   ││
│  │  ├── get_state(env)               获取环境状态                   ││
│  │  ├── should_block_action(action)  检查命令阻止                   ││
│  │  ├── parse_actions(output)        解析模型输出                   ││
│  │  └── guard_multiline_input(action) 处理多行命令                  ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念与设计哲学

### 2.1 一句话定义

SWE-agent 的工具系统是「**Bundle 配置驱动 + Command 抽象 + 多解析器适配**」的架构：工具按 Bundle 组织并通过 YAML 配置定义，Command 抽象统一描述命令签名和参数，多种 ParseFunction 适配不同模型的输出格式。

### 2.2 设计特点

| 特性 | 实现方式 | 优势 |
|------|---------|------|
| Bundle 系统 | 目录 + config.yaml | 模块化、可复用、版本控制友好 |
| Command 抽象 | Pydantic Model | 统一的参数定义和验证 |
| 多解析器 | ParseFunction 策略模式 | 适配不同模型能力 |
| 函数调用转换 | `get_function_calling_tool()` | 自动转换为 OpenAI 格式 |
| 安全过滤 | blocklist/blocklist_standalone | 阻止危险命令 |
| 多行支持 | end_name + here_doc | 支持复杂输入 |

---

## 3. Bundle 系统：工具包组织

### 3.1 目录结构

```
SWE-agent/tools/
├── registry/            # 注册表工具
├── windowed/            # 窗口化文件操作
├── web_browser/         # Web 浏览器自动化
└── [custom_bundle]/     # 自定义工具包
    ├── config.yaml      # 工具定义配置
    ├── bin/             # 可执行脚本
    │   └── tool_name
    └── install.sh       # 安装脚本 (可选)
```

### 3.2 Bundle 配置示例

```yaml
# tools/windowed/config.yaml
tools:
  open:
    docstring: Open a file in the windowed editor
    signature: "open <path>"
    arguments:
      - name: path
        type: string
        description: Path to the file to open
        required: true

  view:
    docstring: View the current window content
    signature: "view"
    arguments: []

  goto:
    docstring: Go to a specific line
    signature: "goto <line_number>"
    arguments:
      - name: line_number
        type: integer
        description: Line number to navigate to
        required: true

state_command: "state"  # 获取环境状态的命令
```

### 3.3 Bundle 加载流程

```python
class Bundle(BaseModel):
    path: Path
    hidden_tools: list[str] = Field(default_factory=list)
    _config: BundleConfig = PrivateAttr(default=None)

    @model_validator(mode="after")
    def validate_tools(self):
        self.path = _convert_path_to_abspath(self.path)
        config_path = self.path / "config.yaml"
        config_data = yaml.safe_load(config_path.read_text())
        self._config = BundleConfig(**config_data)
        # 验证 hidden_tools 有效性
        return self

    @property
    def commands(self) -> list[Command]:
        return [
            Command(name=tool, **tool_config)
            for tool, tool_config in self.config.tools.items()
            if tool not in self.hidden_tools
        ]
```

---

## 4. Command 抽象：统一命令定义

### 4.1 核心结构

```python
# sweagent/tools/commands.py
class Command(BaseModel):
    name: str                          # 命令名称
    docstring: str | None             # 功能描述
    signature: str | None             # 调用签名 (可选)
    end_name: str | None              # 多行结束标记 (可选)
    arguments: list[Argument] = []    # 参数定义

    @cached_property
    def invoke_format(self) -> str:
        """生成命令调用格式字符串"""
        if self.signature:
            # 将 <arg> 替换为 {arg}
            return re.sub(rf"\[?<({ARGUMENT_NAME_PATTERN})>\]?", r"{\1}", self.signature)
        else:
            # 默认格式: cmd {arg1} {arg2} ...
            return f"{self.name} " + " ".join(f"{{{arg.name}}}" for arg in self.arguments)

    def get_function_calling_tool(self) -> dict:
        """转换为 OpenAI Function Calling 格式"""
        tool = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.docstring or "",
            },
        }
        properties = {}
        required = []
        for arg in self.arguments:
            properties[arg.name] = {
                "type": arg.type,
                "description": arg.description
            }
            if arg.items:
                properties[arg.name]["items"] = arg.items
            if arg.enum:
                properties[arg.name]["enum"] = arg.enum
            if arg.required:
                required.append(arg.name)
        tool["function"]["parameters"] = {
            "type": "object",
            "properties": properties,
            "required": required
        }
        return tool
```

### 4.2 参数定义

```python
class Argument(BaseModel):
    name: str                    # 参数名
    type: str                    # 参数类型 (string, integer, array, ...)
    description: str             # 参数描述
    required: bool               # 是否必需
    enum: list[str] | None      # 枚举值 (可选)
    items: dict[str, str] | None # 数组项类型 (可选)
    argument_format: str = "{{value}}"  # Jinja2 格式模板
```

### 4.3 Bash 工具定义

```python
BASH_COMMAND = Command(
    name="bash",
    signature="<command>",
    docstring="runs the given command directly in bash",
    arguments=[
        Argument(
            name="command",
            type="string",
            description="The bash command to execute.",
            required=True,
        )
    ],
)
```

---

## 5. ToolConfig：配置组合

### 5.1 配置结构

```python
class ToolConfig(BaseModel):
    bundles: list[Bundle] = Field(default_factory=list)
    enable_bash_tool: bool = True
    filter: ToolFilterConfig = ToolFilterConfig()
    parse_function: ParseFunction = Field(default_factory=FunctionCallingParser)
    env_variables: dict[str, Any] = {...}  # 环境变量
    registry_variables: dict[str, Any] = {}  # 注册表变量
    submit_command: str = "submit"  # 提交命令名
    execution_timeout: int = 30  # 执行超时
```

### 5.2 工具过滤器

```python
class ToolFilterConfig(BaseModel):
    blocklist: list[str] = [      # 前缀匹配阻止
        "vim", "vi", "emacs", "nano",  # 交互式编辑器
        "nohup", "gdb", "less",        # 交互式工具
        "tail -f",                     # 持续输出
        "python -m venv", "make",      # 环境管理
    ]

    blocklist_standalone: list[str] = [  # 完全匹配阻止
        "python", "python3", "ipython",
        "bash", "sh", "/bin/bash",
        "vi", "vim", "emacs", "nano",
    ]

    block_unless_regex: dict[str, str] = {  # 条件阻止
        "radare2": r"\b(?:radare2)\b.*\s+-c\s+.*",
        "r2": r"\b(?:radare2)\b.*\s+-c\s+.*",
    }
```

### 5.3 工具合并

```python
@cached_property
def commands(self) -> list[Command]:
    commands = []
    tool_sources: dict[str, Path] = {}

    # 1. 添加 bash 工具
    if self.enable_bash_tool:
        commands.append(BASH_COMMAND)
        tool_sources[BASH_COMMAND.name] = Path("<builtin>")

    # 2. 从 bundles 收集命令
    for bundle in self.bundles:
        for command in bundle.commands:
            if command.name in tool_sources:
                # 重复定义检查
                raise ValueError(f"Tool '{command.name}' is defined multiple times")
            commands.append(command)
            tool_sources[command.name] = bundle.path

    return commands

@cached_property
def tools(self) -> list[dict]:
    """转换为 OpenAI Function Calling 格式"""
    return [command.get_function_calling_tool() for command in self.commands]
```

---

## 6. ParseFunction：多解析器支持

### 6.1 解析器类型

| 解析器 | 类型 | 适用场景 | 输出格式 |
|--------|------|----------|----------|
| `FunctionCallingParser` | function_calling | 支持工具的模型 | OpenAI function calling |
| `ThoughtActionParser` | thought_action | 通用模型 | 思考 + ```代码块 |
| `XMLThoughtActionParser` | xml_thought_action | XML 友好模型 | 思考 + <command> |
| `XMLFunctionCallingParser` | xml_function_calling | XML 工具调用 | <function> + <parameter> |
| `JsonParser` | json | JSON 友好模型 | {"thought": "...", "command": {...}} |
| `ActionParser` | action | 简单场景 | 单条命令 |
| `BashCodeBlockParser` | all_bash_code_blocks | Bash 专用 | ```bash 代码块 |

### 6.2 解析器接口

```python
class AbstractParseFunction(ABC):
    error_message: str

    @abstractmethod
    def __call__(self, model_response, commands: list[Command], strict=False) -> tuple[str, str]:
        """解析模型输出
        Returns: (thought, action)
        """
        raise NotImplementedError
```

### 6.3 FunctionCallingParser 实现

```python
class FunctionCallingParser(AbstractParseFunction, BaseModel):
    type: Literal["function_calling"] = "function_calling"

    def __call__(self, model_response: dict, commands: list[Command], strict=False):
        message = model_response["message"]
        tool_calls = model_response.get("tool_calls", None)

        if tool_calls is None or len(tool_calls) != 1:
            num_tools = len(tool_calls) if tool_calls else 0
            error_code = "missing" if num_tools == 0 else "multiple"
            raise FunctionCallingFormatError(...)

        tool_call = tool_calls[0]
        action = self._parse_tool_call(tool_call, commands)
        return message, action

    def _parse_tool_call(self, tool_call: dict, commands: list[Command]):
        name = tool_call["function"]["name"]
        command = {c.name: c for c in commands}.get(name)
        if not command:
            raise FunctionCallingFormatError(f"Command '{name}' not found")

        # 解析参数
        values = json.loads(tool_call["function"]["arguments"])

        # 验证必需参数
        required_args = {arg.name for arg in command.arguments if arg.required}
        missing_args = required_args - values.keys()
        if missing_args:
            raise FunctionCallingFormatError(f"Required argument(s) missing: {missing_args}")

        # 格式化参数 (使用 Jinja2 模板)
        formatted_args = {
            arg.name: Template(arg.argument_format).render(
                value=quote(values[arg.name]) if _should_quote(values[arg.name], command) else values[arg.name]
            )
            for arg in command.arguments if arg.name in values
        }

        return command.invoke_format.format(**formatted_args).strip()
```

### 6.4 ThoughtActionParser 实现

```python
class ThoughtActionParser(AbstractParseFunction, BaseModel):
    type: Literal["thought_action"] = "thought_action"

    def __call__(self, model_response: dict, commands: list[Command], strict=False):
        """解析思考-行动格式
        Example:
            Let's look at the files.

            ```
            ls -l
            ```
        """
        code_block_pat = re.compile(r"^```(\S*)\s*\n|^```\s*$", re.MULTILINE)
        stack = []
        last_valid_block = None

        # 匹配代码块 (支持嵌套)
        for match in code_block_pat.finditer(model_response["message"]):
            if stack and not match.group(1):  # 结束代码块
                start = stack.pop()
                if not stack:  # 不在嵌套中
                    last_valid_block = (start, match)
            elif match.group(1) is not None:  # 开始代码块
                stack.append(match)

        if last_valid_block:
            start, end = last_valid_block
            thought = model_response["message"][:start.start()] + model_response["message"][end.end():]
            action = model_response["message"][start.end():end.start()]
            return thought.strip(), action.strip()

        raise FormatError("No action found in model response")
```

---

## 7. ToolHandler：执行管理

### 7.1 生命周期

```python
class ToolHandler:
    def __init__(self, tools: ToolConfig):
        self.config = tools.model_copy(deep=True)
        self._reset_commands = []
        self._command_patterns = self._get_command_patterns()

    def install(self, env: SWEEnv) -> None:
        """安装工具到环境"""
        self._install_commands(env)
        self.reset(env)

    def reset(self, env: SWEEnv) -> None:
        """重置工具状态"""
        env.set_env_variables(self.config.env_variables)
        env.write_file("/root/.swe-agent-env", json.dumps(self.config.registry_variables))
        env.communicate(" && ".join(self._reset_commands))

    def get_state(self, env: SWEEnv) -> dict[str, str]:
        """获取环境状态"""
        for state_command in self.config.state_commands:
            env.communicate(state_command)
        return self._get_state(env)  # 读取 /root/state.json
```

### 7.2 Bundle 安装

```python
def _install_commands(self, env: SWEEnv) -> None:
    """安装 Bundle 到环境"""
    # 1. 上传 Bundle 文件
    asyncio.run(self._upload_bundles(env))

    for bundle in self.config.bundles:
        cmds = [
            f"export PATH=/root/tools/{bundle.path.name}/bin:$PATH",
            f"chmod +x /root/tools/{bundle.path.name}/bin/*",
        ]
        if (bundle.path / "install.sh").exists():
            cmds.append(f"cd /root/tools/{bundle.path.name} && source install.sh")
        env.communicate(" && ".join(cmds))

    # 2. 验证命令可用性
    asyncio.run(self._check_available_commands(env, {"PATH": path}))
```

### 7.3 命令阻止检查

```python
def should_block_action(self, action: str) -> bool:
    """检查命令是否应该被阻止"""
    action = action.strip()
    if not action:
        return False

    # 1. 前缀匹配阻止
    if any(action.startswith(f) for f in self.config.filter.blocklist):
        return True

    # 2. 完全匹配阻止
    if action in self.config.filter.blocklist_standalone:
        return True

    # 3. 条件阻止 (名称匹配但正则不匹配)
    name = action.split()[0]
    if name in self.config.filter.block_unless_regex:
        if not re.search(self.config.filter.block_unless_regex[name], action):
            return True

    return False
```

### 7.4 多行命令处理

```python
def guard_multiline_input(self, action: str) -> str:
    """处理多行命令，添加 here_doc 结束标记"""
    return _guard_multiline_input(action, self._get_first_multiline_cmd)

def _get_first_multiline_cmd(self, action: str) -> re.Match | None:
    """查找第一个多行命令"""
    patterns = {
        k: v for k, v in self._command_patterns.items()
        if k in self.config.multi_line_command_endings
    }
    matches = [pat.search(action) for pat in patterns.values() if pat.search(action)]
    if not matches:
        return None
    return sorted(matches, key=lambda x: x.start())[0]
```

---

## 8. 与其他组件的交互

### 8.1 与 Agent Loop 的交互

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Agent Loop │────▶│ ToolHandler │────▶│  SWEEnv     │
│             │     │ .parse_actions│    │ (container) │
└──────┬──────┘     └──────┬──────┘     └─────────────┘
       │                   │
       │ ◄─────────────────┘
       │   (thought, action)
       ▼
┌─────────────┐
│ should_block│
│ _action()   │
└──────┬──────┘
       │
  ┌────┴────┐
  ▼         ▼
 允许      阻止
  │         │
  ▼         ▼
执行      返回错误
```

### 8.2 解析流程

```
 model_response
       │
       ▼
┌─────────────────┐
│ parse_function  │
│ (model_response)│
│                 │
│ • FunctionCallingParser
│ • ThoughtActionParser
│ • JsonParser
│ • ...
└────────┬────────┘
         │
         ▼
    (thought, action)
         │
         ▼
┌─────────────────┐
│ guard_multiline │
│ _input(action)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ should_block    │
│ _action(action) │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
  执行      阻止
```

---

## 9. 架构特点总结

- **Bundle 系统**: 目录 + YAML 配置的模块化工具组织
- **Command 抽象**: Pydantic Model 统一描述命令签名和参数
- **自动格式转换**: `get_function_calling_tool()` 自动生成 OpenAI 格式
- **多解析器策略**: 支持 function calling、thought-action、JSON 等多种格式
- **安全过滤**: 三层过滤机制 (前缀、完全匹配、条件阻止)
- **多行支持**: here_doc 风格的多行输入处理
- **环境隔离**: 工具安装到容器环境，独立管理状态

---

## 10. 排障速查

- **命令未找到**: 检查 Bundle 是否正确安装和上传
- **参数解析失败**: 查看 `parse_function` 配置是否与模型输出匹配
- **命令被阻止**: 检查 `ToolFilterConfig` 的 blocklist 配置
- **多行命令错误**: 验证 `end_name` 和正则匹配模式
- **状态获取失败**: 检查 `state_command` 和 `/root/state.json`
- **工具重复定义**: 查看 `commands` 属性中的冲突检测
