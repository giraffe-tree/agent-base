# SWE-agent 概述文档

## 1. 项目简介

**SWE-agent** 是一个学术研究型 Python Agent，专注于软件工程任务（Software Engineering），特别是自动化代码修复和 GitHub Issue 解决。

### 项目定位和目标
- 学术研究驱动的 Agent，发表于顶级会议
- 专注于软件工程任务（SWE-bench 基准测试）
- 提供可复现、可扩展的 Agent 研究框架
- 支持多种模型（OpenAI、Anthropic、本地模型等）
- 提供细粒度的工具控制和环境管理

### 技术栈
- **语言**: Python 3.11+
- **核心依赖**:
  - `pydantic` - 配置和数据验证
  - `jinja2` - 提示模板
  - `swe-rex` - 环境运行时（容器化执行）
  - `tenacity` - 重试逻辑
  - `litellm` - 多模型统一接口

### 官方仓库
- https://github.com/SWE-agent/SWE-agent
- 文档: https://swe-agent.com

---

## 2. 架构概览

### 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI Layer                              │
│  (SWE-agent/sweagent/__main__.py:1)                        │
│  └─ sweagent/run/run.py: main()                            │
│     - 命令解析                                              │
│     - 配置加载 (Pydantic)                                   │
│     - 子命令分发                                            │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                     Run Layer                               │
│  (SWE-agent/sweagent/run/)                                 │
│  ├─ run_single.py - 单实例运行                              │
│  ├─ run_batch.py  - 批量运行                                │
│  └─ run_replay.py - 轨迹重放                                │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Agent Layer                              │
│  (SWE-agent/sweagent/agent/agents.py:443)                  │
│  ├─ DefaultAgent: 主 Agent 实现                             │
│  ├─ RetryAgent:  重试机制封装                               │
│  └─ ShellAgent:  纯 Shell 模式                              │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Tools Layer                               │
│  (SWE-agent/sweagent/tools/)                               │
│  ├─ tools.py:75 - ToolConfig                                │
│  ├─ commands.py - 命令定义                                  │
│  └─ parsing.py  - 输出解析                                  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                Environment Layer                            │
│  (SWE-agent/sweagent/environment/)                         │
│  ├─ swe_env.py - SWEEnv 环境管理                            │
│  └─ 基于 swe-rex 的容器化执行                               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   Model Layer                               │
│  (SWE-agent/sweagent/agent/models.py)                      │
│  ├─ LiteLLMModel: 统一模型接口                              │
│  ├─ HumanModel: 人工介入模式                                │
│  └─ 支持多提供商 (OpenAI, Anthropic, etc.)                  │
└─────────────────────────────────────────────────────────────┘
```

### 各层职责说明

| 层级 | 文件路径 | 核心职责 |
|------|----------|----------|
| CLI | `sweagent/__main__.py` | 入口点，转发到 run 模块 |
| Run | `sweagent/run/run.py` | 命令解析、配置管理、执行编排 |
| Agent | `sweagent/agent/agents.py` | Agent 逻辑、循环控制、历史管理 |
| Tools | `sweagent/tools/tools.py` | 工具配置、命令定义、输出解析 |
| Environment | `sweagent/environment/swe_env.py` | 容器环境、命令执行、状态管理 |
| Model | `sweagent/agent/models.py` | 模型调用、流式响应、Token 追踪 |

### 核心组件列表

1. **DefaultAgent** (agents.py:443) - 主 Agent 实现
2. **ToolHandler** (tools/tools.py:200+) - 工具执行处理器
3. **SWEEnv** (environment/swe_env.py) - 沙箱环境
4. **LiteLLMModel** (agent/models.py) - 模型客户端
5. **Trajectory** (types.py) - 执行轨迹记录
6. **RetryAgent** (agents.py:257) - 带重试的 Agent 包装

---

## 3. 入口与 CLI

### 入口文件路径
```
SWE-agent/sweagent/__main__.py:1
SWE-agent/sweagent/run/run.py (主逻辑)
```

### CLI 参数解析方式

使用 `simple_parsing` 库进行命令解析：

```python
# run/run.py
import simple_parsing
from sweagent.run.run import RunConfig

@dataclass
class RunConfig:
    """全局配置"""
    agent: AgentConfig
    env: EnvironmentConfig
    instances: list[ProblemStatementConfig]
    # ...

# 解析方式
parser = simple_parsing.ArgumentParser()
parser.add_arguments(RunConfig, dest="config")
args = parser.parse_args()
```

### 启动流程

```
sweagent/__main__.py:1
       │
       ▼
sweagent/run/run.py: main()
       │
       ├─ 解析命令行参数 (simple_parsing)
       │
       ├─ match 子命令:
       │   ├─ "run" ──▶ RunSingle.from_config(config).run()
       │   ├─ "batch" ──▶ RunBatch.from_config(config).run()
       │   ├─ "replay" ──▶ RunReplay.from_config(config).run()
       │   └─ ...
       │
       └─ 执行对应 Runner
           │
           ▼
    Agent.setup() ──▶ Agent.run() 或 Agent.step() 循环
```

---

## 4. Agent 循环机制

### 主循环代码位置

```
SWE-agent/sweagent/agent/agents.py:443 (DefaultAgent)
SWE-agent/sweagent/agent/agents.py:400-434 (RetryAgent.run)
```

### 流程图（文本形式）

```
┌─────────────────┐
│   Agent.setup() │
│   (初始化)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 安装工具        │
│ tools.install() │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 添加系统消息    │
│ 添加 Demonstrations
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│         主循环 (while not done)      │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ step_output = self.step()     │  │
│  │                               │  │
│  │ 1. 格式化当前历史为消息       │  │
│  │ 2. 调用 model.query()         │  │
│  │ 3. 解析模型输出 (thought/action)│ │
│  │ 4. 执行工具调用               │  │
│  │ 5. 获取 Observation           │  │
│  │ 6. 添加到历史                 │  │
│  │ 7. 检查是否完成               │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ save_trajectory() 保存轨迹    │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
         │
         ▼ (done=True)
┌─────────────────┐
│ finalize_run()  │
│ 保存最终轨迹    │
└─────────────────┘
```

### 单次循环的执行步骤

**step() 方法** (agents.py:800+):

```python
def step(self) -> StepOutput:
    # 1. 准备消息历史
    messages = self.messages  # 经过 history_processors 处理

    # 2. 调用模型
    model_response = self.model.query(messages)

    # 3. 解析输出
    thought, action, output = self.tools.parse_model_output(model_response)

    # 4. 执行动作
    observation = self.tools.execute(action, self._env)

    # 5. 检查终止条件
    done = self.tools.is_submission(action)

    # 6. 构建 StepOutput
    step_output = StepOutput(
        thought=thought,
        action=action,
        observation=observation,
        output=output,
        done=done,
    )

    # 7. 添加到历史
    self.add_step_to_history(step_output)

    return step_output
```

### 循环终止条件

- **提交命令** - 执行 `submit` 工具表示任务完成
- **最大步数** - 达到配置的 `max_iterations`
- **错误退出** - 连续错误超过 `max_requeries`
- **超时** - 总执行时间超过限制
- **成本限制** - Token 成本超过预算

---

## 5. 工具系统

### 工具定义方式

```python
# sweagent/tools/tools.py:75
class ToolConfig(BaseModel):
    """工具配置"""
    bundles: list[Bundle] = Field(default_factory=list)
    enable_bash_tool: bool = True
    submit_command: str = "submit"
    parse_function: ParseFunction = Field(default_factory=FunctionCallingParser)
    execution_timeout: int = 30
    # ...
```

工具命令定义：

```python
# sweagent/tools/commands.py
@dataclass
class Command:
    name: str                    # 命令名称
    code: str                    # 可执行代码
    docstring: str               # 文档字符串
    arguments: ArgumentFormat    # 参数格式
    # ...

# 示例: bash 命令
BASH_COMMAND = Command(
    name="bash",
    code="""bash -c {command}""",
    docstring="执行 bash 命令",
    arguments=ArgumentFormat(...),
)
```

### 工具注册表位置

```
SWE-agent/sweagent/tools/tools.py:200+
```

```python
class ToolHandler:
    def __init__(self, config: ToolConfig):
        self.config = config
        self.commands: dict[str, Command] = {cmd.name: cmd for cmd in config.commands}

    def execute(self, action: str, env: SWEEnv) -> str:
        """执行动作，返回 observation"""
        command, args = self._parse_action(action)
        return self._run_command(command, args, env)

    def parse_model_output(self, output: str) -> tuple[str, str, str]:
        """解析模型输出为 (thought, action, raw_output)"""
        return self.config.parse_function.parse(output)
```

### 工具执行流程

```
模型输出
    │
    ▼
┌─────────────────┐
│ parse_function  │  ──▶ ThoughtActionParser / FunctionCallingParser
│ 解析thought/    │     (sweagent/tools/parsing.py)
│ action          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ToolHandler     │
│ ::execute()     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 查找 Command    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 在 SWEEnv 中    │  ──▶ sweagent/environment/swe_env.py
│ 执行命令        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 返回 Observation│
│ (字符串)        │
└─────────────────┘
```

### 审批机制

SWE-agent 通过配置控制审批：

```python
# 通过 ToolFilterConfig 阻止危险命令
class ToolFilterConfig(BaseModel):
    blocklist: list[str] = ["vim", "vi", "emacs", "nano", ...]
    blocklist_standalone: list[str] = ["python", "bash", "su", ...]
```

- 拦截列表阻止交互式命令
- 语法检查（bash -n）防止错误命令
- 超时控制防止长时间执行

---

## 6. 状态管理

### Session 状态存储位置

```
SWE-agent/sweagent/types.py
SWE-agent/sweagent/agent/agents.py:481-483
```

```python
class DefaultAgent:
    def __init__(self, ...):
        self.history = []           # 消息历史
        self._trajectory = []       # 执行轨迹
        self.info = AgentInfo()     # Agent 元信息

class StepOutput(BaseModel):
    """单步输出"""
    thought: str
    action: str
    observation: str
    output: str
    done: bool

class TrajectoryStep(BaseModel):
    """轨迹步骤"""
    step: int
    thought: str
    action: str
    observation: str
    response: str
```

### Checkpoint 机制

**轨迹文件 (.traj)**:

```python
# 保存位置: output_dir / {instance_id}.traj
# 格式: JSON
trajectory_data = {
    "trajectory": self._trajectory,
    "history": self.history,
    "info": self.info,
    "replay_config": self.replay_config.model_dump(),
    "environment": self._env.name,
}
```

### 历史记录管理

```python
# agents.py:556-559
def _append_history(self, item: dict[str, Any]) -> None:
    """添加历史项"""
    self._chook.on_query_message_added(**item)
    self.history.append(item)

# agents.py:540-551
@property
def messages(self) -> list[dict[str, Any]]:
    """返回经 history_processors 处理的消息"""
    filtered_history = [entry for entry in self.history if entry["agent"] == self.name]
    messages = filtered_history
    for processor in self.history_processors:
        messages = processor(messages)
    return messages
```

### 状态恢复方式

**重放模式 (Replay)**:

```
1. 加载 .traj 文件
2. 解析 history 和 trajectory
3. 使用 replay_config 重建环境
4. 逐步重放 action，验证 observation
```

---

## 7. 模型调用方式

### 支持的模型提供商

通过 LiteLLM 支持多种提供商：

- **OpenAI** - GPT-4, GPT-3.5
- **Anthropic** - Claude 系列
- **本地模型** - 通过 Hugging Face/ollama
- **Azure** - Azure OpenAI
- **其他** - 任何 LiteLLM 支持的提供商

### 模型调用封装位置

```
SWE-agent/sweagent/agent/models.py
```

```python
class LiteLLMModel(AbstractModel):
    """使用 LiteLLM 的统一模型接口"""

    def __init__(self, config: ModelConfig):
        self.config = config
        self._stats = InstanceStats()

    def query(self, messages: list[dict[str, str]]) -> str:
        """同步查询"""
        response = litellm.completion(
            model=self.config.name,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        self._update_stats(response)
        return response.choices[0].message.content

    async def aquery(self, messages: list[dict[str, str]]) -> str:
        """异步查询"""
        ...
```

### 流式响应处理

```python
# 流式输出支持
def query_stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
    response = litellm.completion(
        model=self.config.name,
        messages=messages,
        stream=True,
        ...
    )
    for chunk in response:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
```

### Token 管理

```python
# sweagent/agent/models.py
class InstanceStats(BaseModel):
    """实例统计"""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0

    def update(self, usage: dict):
        """更新 Token 统计"""
        self.total_tokens += usage.get("total_tokens", 0)
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        self.total_cost += self._calculate_cost(usage)
```

---

## 8. 数据流转图

```
┌────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                    │
└────────────────────────────────────────────────────────────────────────┘

配置文件 (YAML/命令行)
       │
       ▼
┌─────────────────┐
│ RunConfig       │  ──▶  run/run.py
│ 配置聚合        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ AgentConfig     │  ──▶  agent/agents.py:149
│ ├─ TemplateConfig
│ ├─ ToolConfig   │  ──▶  tools/tools.py:75
│ ├─ ModelConfig  │  ──▶  agent/models.py
│ └─ ...          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ DefaultAgent    │  ──▶  agents.py:443
│ ::from_config() │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ setup()         │  ──▶  agents.py:561
│ 初始化环境      │
│ 加载工具        │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│           主循环                     │
│  while not step_output.done:        │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ step()                        │  │
│  │                               │  │
│  │ 1. messages = self.messages   │  │
│  │   (经 history_processors)     │  │
│  │                               │  │
│  │ 2. model_response =           │  │
│  │    model.query(messages)      │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ LiteLLM     │             │  │
│  │   │ ::completion│             │  │
│  │   └─────────────┘             │  │
│  │                               │  │
│  │ 3. parse_model_output()       │  │
│  │   (thought, action)           │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ ThoughtAction│            │  │
│  │   │ Parser      │             │  │
│  │   └─────────────┘             │  │
│  │                               │  │
│  │ 4. tools.execute(action)      │  │
│  │       │                       │  │
│  │       ▼                       │  │
│  │   ┌─────────────┐             │  │
│  │   │ SWEEnv      │             │  │
│  │   │ (swe-rex)   │             │  │
│  │   └─────────────┘             │  │
│  │                               │  │
│  │ 5. observation = 执行结果     │  │
│  │                               │  │
│  │ 6. add_step_to_history()      │  │
│  │                               │  │
│  │ 7. done = is_submission()     │  │
│  │                               │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ save_trajectory()             │  │
│  │ 保存 .traj 文件               │  │
│  └───────────────────────────────┘  │
│                                     │
└─────────────────────────────────────┘
         │
         ▼ (完成)
┌─────────────────┐
│ get_trajectory  │
│ _data()         │
└─────────────────┘
```

### 关键数据结构定义

```python
# sweagent/types.py
class StepOutput(BaseModel):
    """单步输出"""
    thought: str           # 模型思考过程
    action: str            # 执行的动作
    observation: str       # 执行结果
    output: str            # 原始模型输出
    done: bool             # 是否完成
    tool_calls: list[dict] | None = None

class TrajectoryStep(BaseModel):
    """轨迹步骤"""
    step: int
    thought: str
    action: str
    observation: str
    response: str
    state: dict[str, str]  # 环境状态

class AgentInfo(BaseModel):
    """Agent 信息"""
    swe_agent_version: str
    swe_agent_hash: str
    model_stats: InstanceStats
    submission: str | None = None
    submission_patch: str | None = None
```

---

## 9. 源码索引

### 核心文件

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| 入口 | `sweagent/__main__.py` | 1 | 主入口 |
| Run 主逻辑 | `sweagent/run/run.py` | - | 命令解析与分发 |
| 单实例运行 | `sweagent/run/run_single.py` | - | RunSingle 类 |
| DefaultAgent | `sweagent/agent/agents.py` | 443 | 主 Agent |
| RetryAgent | `sweagent/agent/agents.py` | 257 | 重试包装 Agent |
| AgentConfig | `sweagent/agent/agents.py` | 149 | Agent 配置 |
| ToolConfig | `sweagent/tools/tools.py` | 75 | 工具配置 |
| ToolHandler | `sweagent/tools/tools.py` | 200+ | 工具处理器 |
| Command | `sweagent/tools/commands.py` | - | 命令定义 |
| Parsing | `sweagent/tools/parsing.py` | - | 输出解析 |
| SWEEnv | `sweagent/environment/swe_env.py` | - | 环境管理 |
| LiteLLMModel | `sweagent/agent/models.py` | - | 模型客户端 |
| StepOutput | `sweagent/types.py` | - | 单步输出 |
| Trajectory | `sweagent/types.py` | - | 轨迹类型 |

### 配置类

| 配置 | 文件路径 | 说明 |
|------|----------|------|
| RunConfig | `sweagent/run/common.py` | 全局运行配置 |
| AgentConfig | `sweagent/agent/agents.py` | Agent 配置 |
| ToolConfig | `sweagent/tools/tools.py` | 工具配置 |
| ModelConfig | `sweagent/agent/models.py` | 模型配置 |
| EnvironmentConfig | `sweagent/environment/swe_env.py` | 环境配置 |

### Hook 系统

| Hook | 文件路径 | 说明 |
|------|----------|------|
| AbstractAgentHook | `sweagent/agent/hooks/abstract.py` | Hook 基类 |
| CombinedAgentHook | `sweagent/agent/hooks/abstract.py` | Hook 组合 |

---

## 总结

SWE-agent 是一个面向软件工程研究的 Python Agent 框架：

1. **研究导向** - 支持可复现实验，详细的轨迹记录
2. **灵活配置** - Pydantic 配置系统，支持多种模型和环境
3. **工具丰富** - 细粒度的 bash/file/edit 工具集
4. **环境隔离** - 基于 swe-rex 的容器化执行环境
5. **可扩展** - Hook 系统和模板系统支持自定义行为
