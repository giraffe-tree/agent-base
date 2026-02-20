# Session 管理（swe-agent）

本文基于 `./swe-agent` 源码，解释 swe-agent 如何实现 session 生命周期管理、trajectory 持久化、多轮对话状态维护。
为适配"先看全貌再看细节"的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 Session 生命周期流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 创建 Session                                             │
│  ┌─────────────────┐                                            │
│  │ RunSingle/RunBatch│ ◄──── 入口：单实例或批量执行            │
│  │ 初始化 Agent + Env │                                          │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  SETUP: Session 初始化                                           │
│  ┌────────────────────────────────────────┐                     │
│  │ Agent.setup()                          │                     │
│  │  ├── 安装 tools 到 environment         │                     │
│  │  ├── 构建初始 history                  │                     │
│  │  │   ├── system message                │                     │
│  │  │   ├── demonstrations (few-shot)     │                     │
│  │  │   └── instance template (problem)   │                     │
│  │  ├── 初始化 trajectory = []            │                     │
│  │  └── 设置 traj_path (输出路径)         │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  EXECUTE: Agent Loop（多轮迭代）                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ Agent.run()  while not done:           │                     │
│  │  └── step() ◄─────────────────────────┼──┐  ◄── 循环点      │
│  │       ├── apply history processors     │  │                  │
│  │       │   └── 过滤/变换 history        │  │                  │
│  │       ├── forward_with_handling()      │  │                  │
│  │       │   ├── query model              │  │                  │
│  │       │   ├── parse action             │  │                  │
│  │       │   └── execute in env           │  │                  │
│  │       ├── add_step_to_history()        │  │                  │
│  │       ├── add_step_to_trajectory()     │  │                  │
│  │       ├── save_trajectory() ───────────┼──┤ 持久化检查点    │
│  │       └── check done?                  │  │                  │
│  │           ├── No ──────────────────────┘  │                  │
│  │           └── Yes → break                 │                  │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  PERSIST: 最终持久化                                             │
│  ┌────────────────────────────────────────┐                     │
│  │ save_trajectory()                      │                     │
│  │  ├── trajectory: 结构化执行步骤        │ ──► .traj 文件      │
│  │  ├── history: 完整对话记录             │                     │
│  │  └── info: 元数据与统计                │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CLEANUP: 资源清理                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ env.close()                            │                     │
│  │  └── 停止 deployment                   │ ──► 容器/进程清理   │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 函数/模块  ├──┤ 子步骤  ──► 流程  ◄──┘ 循环回流
```

### 1.2 核心数据结构关系图

```text
┌────────────────────────────────────────────────────────────────────┐
│ [A] Session 核心状态结构                                            │
└────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │  DefaultAgent   │
                    │  (session 主体) │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐   ┌─────────────────┐   ┌───────────────┐
│    history    │   │   trajectory    │   │     info      │
│   (对话历史)   │   │  (执行轨迹)      │   │   (元数据)    │
├───────────────┤   ├─────────────────┤   ├───────────────┤
│ list[dict]    │   │ list[Trajectory │   │ AgentInfo()   │
│ - role        │   │     Step]       │   │ - exit_status │
│ - content     │   │ - action        │   │ - submission  │
│ - message_type│   │ - observation   │   │ - model_stats │
│ - agent       │   │ - thought       │   │ - error       │
└───────────────┘   │ - state         │   └───────────────┘
                    │ - execution_time│
                    └─────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ [B] History Processor 链                                            │
└────────────────────────────────────────────────────────────────────┘

   Raw History          Processors              Model Input
       │                    │                        │
       ▼                    ▼                        ▼
┌─────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ 完整历史    │───▶│ LastNObservations│───▶│ 处理后历史      │
│ (所有消息)  │    │ (保留最近N条)    │    │ (用于模型输入)  │
└─────────────┘    ├─────────────────┤    └─────────────────┘
                   │ CacheControl    │
                   │ (添加缓存标记)  │
                   ├─────────────────┤
                   │ RemoveRegex     │
                   │ (移除特定内容)  │
                   └─────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ [C] Environment 状态层级                                            │
└────────────────────────────────────────────────────────────────────┘

   SWEEnv
      │
      ├── deployment ───────▶ 容器/进程生命周期
      │
      ├── repo ─────────────▶ 代码仓库信息
      │
      └── state ────────────▶ 工具状态 (open_file, working_dir)

图例: ───▶ 流向/依赖  ┌─┐ 数据结构
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 session 从创建到清理的完整流程）。
- **3 分钟版**：看 `1.1` + `1.2` + `3`（知道三大核心数据结构和生命周期方法）。
- **10 分钟版**：通读 `3~6`（能定位 session 相关问题）。

### 2.1 一句话定义

swe-agent 的 Session 是**"trajectory 为中心的有状态执行单元"**：Agent 维护 history 和 trajectory 两个并行数据结构，history 用于模型输入，trajectory 用于结构化持久化，每个 step 都会保存 checkpoint。

---

## 3. 核心数据结构

### 3.1 StepOutput - 单步输出

**文件**: `sweagent/types.py`

```python
class StepOutput(BaseModel):
    query: list[dict] = [{}]           # 发送给模型的完整消息
    thought: str = ""                  # 模型推理内容
    action: str = ""                   # 解析出的动作
    output: str = ""                   # 原始输出
    observation: str = ""              # 环境执行结果
    execution_time: float = 0.0        # 执行耗时
    done: bool = False                 # 是否结束
    exit_status: int | str | None = None  # 退出状态
    submission: str | None = None      # 最终提交内容
    state: dict[str, str] = {}         # 环境状态快照
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_ids: list[str] | None = None
    extra_info: dict[str, Any] = {}
```

### 3.2 HistoryItem - 历史记录项

```python
class HistoryItem(TypedDict):
    role: str                          # system/user/assistant/tool
    content: str | list[dict]          # 消息内容
    message_type: Literal["thought", "action", "observation"]
    agent: str                         # agent 名称
    is_demo: bool                      # 是否为演示数据
    thought: str                       # 推理内容
    action: str | None                 # 执行动作
    tool_calls: list[dict] | None      # 工具调用
    tool_call_ids: list[str] | None
    tags: list[str]                    # 标签（用于过滤）
    cache_control: dict | None         # Anthropic 缓存控制
```

### 3.3 TrajectoryStep - 轨迹步骤

```python
class TrajectoryStep(TypedDict):
    action: str                        # 执行的动作
    observation: str                   # 观察结果
    response: str                      # 模型响应
    state: dict[str, str]             # 环境状态
    thought: str                       # 推理过程
    execution_time: float              # 执行时间
    query: list[dict[str, Any]]       # 查询内容
    extra_info: dict[str, Any]         # 额外信息
```

---

## 4. Session 生命周期详解

### 4.1 创建与初始化

**入口**: `sweagent/agent/agents.py:setup()`

```python
def setup(self, env: SWEEnv, problem_statement: ProblemStatement, output_dir: Path):
    """Initialize a new session"""
    self._problem_statement = problem_statement
    self._env = env
    self.traj_path = output_dir / (self._problem_statement.id + ".traj")

    # 重置状态集合
    self.info = AgentInfo()
    self.history = []
    self._trajectory = []

    # 初始化环境
    self.tools.install(self._env)

    # 构建初始 history
    self.add_system_message_to_history()
    self.add_demonstrations_to_history()
    self.add_instance_template_to_history(state=self.tools.get_state(self._env))
```

### 4.2 单步执行

**核心方法**: `sweagent/agent/agents.py:step()`

```python
def step(self) -> StepOutput:
    """Execute one step in the session"""
    self._chook.on_step_start()

    # 1. 获取处理后的消息（应用 history processors）
    messages = self.messages

    # 2. 模型推理与错误处理
    step_output = self.forward_with_handling(messages)

    # 3. 更新 history 和 trajectory
    self.add_step_to_history(step_output)
    self.add_step_to_trajectory(step_output)

    # 4. 更新元数据
    self.info["submission"] = step_output.submission
    self.info["exit_status"] = step_output.exit_status
    self.info["model_stats"] = self.model.stats.model_dump()

    return step_output
```

### 4.3 History Processor 链

**文件**: `sweagent/agent/history_processors.py`

```python
@property
def messages(self) -> list[dict[str, Any]]:
    """获取处理后的 history 用于模型输入"""
    # 按 agent 名称过滤
    filtered_history = [entry for entry in self.history
                       if entry["agent"] == self.name]

    # 应用 processor 链
    messages = filtered_history
    for processor in self.history_processors:
        messages = processor(messages)

    return messages
```

**常用 Processors**:
- `LastNObservations`: 只保留最近 N 条 observation，控制上下文长度
- `CacheControlHistoryProcessor`: 添加 Anthropic 缓存控制标记
- `ClosedWindowHistoryProcessor`: 管理文件视图窗口
- `RemoveRegex`: 移除匹配特定模式的内容（如 `<diff>.*</diff>`）

### 4.4 持久化机制

**保存 Trajectory**:

```python
def get_trajectory_data(self) -> dict[str, Any]:
    """打包所有 session 数据"""
    return {
        "trajectory": self.trajectory,
        "history": self.history,
        "info": self.info,
        "replay_config": self.replay_config.model_dump_json() if self.replay_config else None,
        "environment": self._env.name,
    }

def save_trajectory(self) -> None:
    """持久化到磁盘"""
    data = self.get_trajectory_data()
    self.traj_path.write_text(json.dumps(data, indent=2))
```

**Trajectory 文件结构**:

```json
{
    "environment": "swe_main",
    "trajectory": [
        {
            "action": "ls -F\n",
            "observation": "AUTHORS.rst\nCHANGELOG.rst\n...",
            "response": "Let's list out some of the files...",
            "state": "{\"open_file\": \"n/a\", \"working_dir\": \"/marshmallow-code__marshmallow\"}",
            "thought": "Let's list out some of the files...",
            "execution_time": 0.5
        }
    ],
    "history": [...],
    "info": {
        "exit_status": "submitted",
        "submission": "<patch_content>",
        "model_stats": {...}
    }
}
```

---

## 5. Environment Session 管理

### 5.1 SWEEnv 生命周期

**文件**: `sweagent/environment/swe_env.py`

```python
class SWEEnv:
    def start(self) -> None:
        """Initialize environment session"""
        self._init_deployment()
        self.reset()

    def reset(self):
        """Reset to clean state (preserves deployment)"""
        self.communicate(input="cd /", check="raise")
        self._copy_repo()
        self._reset_repository()

    def hard_reset(self):
        """Complete reset including deployment"""
        self.close()
        self.start()

    def close(self) -> None:
        """Terminate session"""
        asyncio.run(self.deployment.stop())
```

### 5.2 状态快照

Environment 在每一步都会捕获工具状态：

```python
# tools.get_state(env) 返回状态快照
state = {
    "open_file": "n/a" or "/path/to/file",
    "working_dir": "/current/working/directory"
}
```

---

## 6. 批量 Session 管理

### 6.1 RunBatch 并行执行

**文件**: `sweagent/run/run_batch.py`

```python
class RunBatch:
    def main_multi_worker(self) -> None:
        """并行 session 执行"""
        with ThreadPoolExecutor(max_workers=self._num_workers) as executor:
            futures = [executor.submit(self.run_instance, instance)
                      for instance in self.instances]

    def run_instance(self, instance: BatchInstance) -> None:
        """执行单个实例 session"""
        # 每个实例获得全新的 agent 和 environment
        agent = get_agent_from_config(self.agent_config)
        env = SWEEnv.from_config(instance.env)

        try:
            env.start()
            result = agent.run(
                problem_statement=instance.problem_statement,
                env=env,
                output_dir=output_dir,
            )
        finally:
            env.close()  # 确保清理
```

### 6.2 Session 隔离

每个实例拥有：
- 独立的 Agent 实例
- 独立的 Environment（容器/进程）
- 独立的输出目录和 trajectory 文件
- 独立的模型统计信息

---

## 7. Hook 系统

### 7.1 Agent Hooks

**文件**: `sweagent/agent/hooks/abstract.py`

```python
class AbstractAgentHook:
    def on_init(self, *, agent: "DefaultAgent"): ...
    def on_run_start(self): ...
    def on_step_start(self): ...
    def on_actions_generated(self, *, step: StepOutput): ...
    def on_action_started(self, *, step: StepOutput): ...
    def on_action_executed(self, *, step: StepOutput): ...
    def on_step_done(self, *, step: StepOutput, info: AgentInfo): ...
    def on_run_done(self, *, trajectory: Trajectory, info: AgentInfo): ...
```

### 7.2 使用场景

- **日志记录**: 记录每个 step 的执行情况
- **监控**: 实时上报执行进度
- **调试**: 在关键节点捕获状态
- **回放**: 记录足够信息用于后续 replay

---

## 8. Session Replay

### 8.1 回放机制

**文件**: `sweagent/run/run_replay.py`

```python
class RunReplay:
    def _create_actions_file(self) -> None:
        """从 trajectory 提取动作用于回放"""
        actions = []
        for item in self._traj_data["history"]:
            if item["role"] == "assistant":
                action = {"message": item["content"]}
                if self._use_function_calling:
                    action["tool_calls"] = item["tool_calls"]
                actions.append(action)

    def main(self):
        """在全新环境中回放 trajectory"""
        self._create_actions_file()
        run_single = self._get_run_single()
        run_single.run()  # 执行回放
```

---

## 9. 排障速查

| 问题 | 检查点 |
|------|--------|
| history 过长 | 查看 `history_processors` 配置，特别是 `LastNObservations` |
| trajectory 未保存 | 检查 `traj_path` 是否正确设置，磁盘是否有空间 |
| session 状态不一致 | 检查 `history` vs `trajectory` 的同步情况 |
| environment 泄漏 | 确保 `env.close()` 在 finally 块中调用 |
| 多实例冲突 | 确认每个实例有独立的 output_dir |

---

## 10. 架构特点总结

1. **双轨数据结构**: history（对话）和 trajectory（执行）并行维护
2. **Processor 链**: 灵活的 history 变换管道
3. **频繁持久化**: 每步都保存 checkpoint，支持断点续传
4. **完全隔离**: 批量执行时每个 session 完全独立
5. **Hook 扩展**: 通过 hooks 实现日志、监控、调试
6. **可回放**: trajectory 包含完整信息用于重现执行
