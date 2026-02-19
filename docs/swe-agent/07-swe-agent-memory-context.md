# Memory Context 管理（swe-agent）

本文基于 `./swe-agent` 源码，解释 SWE-agent 如何实现 History 管理、Trajectory 持久化和 Replay 功能。

---

## 1. 先看全局（流程图）

### 1.1 History → Trajectory → State 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│  Agent Loop 执行                                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ Agent.run()                            │                     │
│  │  ├── step()                            │                     │
│  │  │   ├── model.query()                │                     │
│  │  │   ├── parse_action()               │                     │
│  │  │   ├── env.step()                   │                     │
│  │  │   └── create_history_item()        │                     │
│  │  └── append to history[]               │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  History Processors 链                                            │
│  ┌────────────────────────────────────────┐                     │
│  │ for processor in history_processors:   │                     │
│  │   history = processor(history)         │                     │
│  │                                        │                     │
│  │ 支持的处理器:                          │                     │
│  │ - LastNObservations                    │                     │
│  │ - CacheControlHistoryProcessor         │                     │
│  │ - ClosedWindowHistoryProcessor         │                     │
│  │ - RemoveRegex                          │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Trajectory 持久化                                                │
│  ┌────────────────────────────────────────┐                     │
│  │ {session_name}.traj                    │                     │
│  │  └── JSON 格式                         │                     │
│  │      {                                 │                     │
│  │        "trajectory": [...],            │                     │
│  │        "info": {...},                  │                     │
│  │        "config": {...}                 │                     │
│  │      }                                 │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 History 结构

```text
History = list[HistoryItem]

HistoryItem {
  role: "user" | "assistant" | "system" | "tool"
  content: str | list[dict]           # 消息内容
  message_type: "thought" | "action" | "observation"

  # 可选字段
  agent: str                          # 代理名称
  is_demo: bool                       # 是否为演示数据
  thought: str                       # 思考过程
  action: str | None                 # 执行的动作
  tool_calls: list[dict] | None     # 工具调用
  tool_call_ids: list[str] | None   # 工具调用 ID
  tags: list[str]                    # 处理器标签
  cache_control: dict | None         # 缓存控制（Claude）
}
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `1.2`（知道 History 结构和 Processor 链）。
- **3 分钟版**：看 `1.1` + `1.2` + `3` + `4`（知道 Processors、Trajectory 和 Replay）。
- **10 分钟版**：通读全文（能配置和扩展 History Processors）。

### 2.1 一句话定义

SWE-agent 的 Memory Context 采用"**可配置的 Processor 链 + Trajectory 持久化**"的设计：通过链式 History Processors 对对话历史进行转换和压缩，以 Trajectory 格式持久化到 `.traj` 文件，并支持完整的 Replay 功能。

---

## 3. 核心组件详解

### 3.1 History 类型定义

**文件**: `sweagent/types.py:44-77`

```python
from typing import TypedDict, Literal

class _HistoryItem(TypedDict):
    """必需字段"""
    role: str                          # user | assistant | system | tool
    content: str | list[dict[str, Any]]
    message_type: Literal["thought", "action", "observation"]

class HistoryItem(_HistoryItem, total=False):
    """可选字段"""
    agent: str
    is_demo: bool
    thought: str
    action: str | None
    tool_calls: list[dict[str, str]] | None
    tool_call_ids: list[str] | None
    tags: list[str]
    cache_control: dict[str, Any] | None
    thinking_blocks: list[dict[str, Any]] | None

History = list[HistoryItem]

class TrajectoryStep(TypedDict):
    """Trajectory 中的单步记录"""
    action: str
    observation: str
    response: str
    state: dict[str, str]
    thought: str
    execution_time: float
    query: list[dict[str, Any]]
    extra_info: dict[str, Any]

Trajectory = list[TrajectoryStep]
```

### 3.2 History Processors

**文件**: `sweagent/agent/history_processors.py`

#### DefaultHistoryProcessor

```python
class DefaultHistoryProcessor(BaseModel):
    """默认处理器，不做任何修改"""
    type: Literal["default"] = "default"

    def __call__(self, history: History) -> History:
        return history
```

#### LastNObservations

```python
class LastNObservations(BaseModel):
    """
    只保留最近的 N 个 observations，其余用摘要替代。

    这是 SWE-agent 论文中使用的经典处理器，
    默认保留最近 5 个 observations。

    配置示例:
    ```yaml
    agent:
      history_processors:
        - type: last_n_observations
          n: 5
    ```
    """
    n: int                           # 保留的 observation 数量
    polling: int = 1                # 更新间隔（用于缓存优化）
    always_remove_output_for_tags: set[str] = {"remove_output"}
    always_keep_output_for_tags: set[str] = {"keep_output"}
    type: Literal["last_n_observations"] = "last_n_observations"

    def __call__(self, history: History) -> History:
        new_history = []
        omit_content_idxs = self._get_omit_indices(history)

        for idx, entry in enumerate(history):
            tags = set(entry.get("tags", []))

            if (idx not in omit_content_idxs or
                tags & self.always_keep_output_for_tags) and not (
                tags & self.always_remove_output_for_tags
            ):
                new_history.append(entry)
            else:
                # 替换为摘要
                num_text_lines, num_images = _get_content_stats(entry)
                entry["content"] = f"Old environment output: ({num_text_lines} lines omitted)"
                new_history.append(entry)

        return new_history
```

#### CacheControlHistoryProcessor

```python
class CacheControlHistoryProcessor(BaseModel):
    """
    为 Anthropic Claude 添加手动缓存控制标记。

    配置示例:
    ```yaml
    agent:
      history_processors:
        - type: cache_control
          last_n_messages: 2
    ```
    """
    last_n_messages: int = 2
    last_n_messages_offset: int = 0
    tagged_roles: list[str] = ["user", "tool"]
    type: Literal["cache_control"] = "cache_control"

    def __call__(self, history: History) -> History:
        new_history = []
        n_tagged = 0

        for i_entry, entry in enumerate(reversed(history)):
            # 清除之前的缓存标记
            _clear_cache_control(entry)

            # 为最近 N 条消息添加缓存标记
            if (n_tagged < self.last_n_messages and
                entry["role"] in self.tagged_roles and
                i_entry >= self.last_n_messages_offset):
                _set_cache_control(entry)
                n_tagged += 1

            new_history.append(entry)

        return list(reversed(new_history))
```

### 3.3 其他 Processors

| Processor | 功能 | 配置 |
|-----------|------|------|
| `TagToolCallObservations` | 为特定工具调用的 observations 添加标签 | `function_names: set[str]` |
| `ClosedWindowHistoryProcessor` | 替换已关闭的文件窗口内容 | - |
| `RemoveRegex` | 使用正则表达式移除内容 | `remove: list[str]` |
| `ImageParsingHistoryProcessor` | 解析内嵌的 base64 图片 | `allowed_mime_types: set[str]` |

---

## 4. Trajectory 持久化

### 4.1 Trajectory 文件格式

**文件**: `sweagent/run/run_replay.py`

```python
# {session_name}.traj
{
  "trajectory": [
    {
      "action": "edit 12:12\n<<<<<<< SEARCH...",
      "observation": "[File: /path/to/file.py (10 lines total)]\n1|def foo():\n2|    pass",
      "response": "I'll edit the file...",
      "state": {"open_file": "/path/to/file.py"},
      "thought": "I need to add a new function...",
      "execution_time": 1.23,
      "query": [{"role": "user", "content": "..."}],
      "extra_info": {}
    }
  ],
  "info": {
    "model_stats": {"cost": 0.5, "tokens": 1000},
    "exit_status": "success",
    "submission": "The fix..."
  },
  "config": {
    "agent": "sweagent",
    "model": "gpt-4"
  }
}
```

### 4.2 保存 Trajectory

```python
from sweagent.agent.agents import Agent

class Agent:
    def save_trajectory(self, trajectory_path: Path) -> None:
        """保存 trajectory 到文件"""
        data = {
            "trajectory": self.trajectory,
            "info": self.info,
            "config": self.config.model_dump(),
        }
        with open(trajectory_path, "w") as f:
            json.dump(data, f, indent=2)
```

---

## 5. Replay 功能

### 5.1 从 Trajectory 重放

**文件**: `sweagent/run/run_replay.py`

```python
async def run_replay(
    trajectory_path: Path,
    env: Environment,
    agent: Agent,
) -> None:
    """
    从 trajectory 文件重放执行过程

    用途:
    1. 复现 bug
    2. 验证修复
    3. 生成演示
    """
    # 1. 加载 trajectory
    with open(trajectory_path) as f:
        data = json.load(f)

    trajectory = data["trajectory"]

    # 2. 逐步重放
    for step in trajectory:
        # 设置环境状态
        env.set_state(step["state"])

        # 执行动作
        observation = await env.step(step["action"])

        # 验证 observation 是否匹配
        if observation != step["observation"]:
            logger.warning(f"Observation mismatch at step {step}")

    logger.info("Replay completed successfully")
```

### 5.2 Trajectory 转 Demo

```python
# sweagent/run/run_traj_to_demo.py

def convert_trajectory_to_demo(trajectory_path: Path) -> Demo:
    """
    将 trajectory 转换为可演示的格式

    过滤掉:
    - 失败的步骤
    - 重复的动作
    - 系统消息
    """
    with open(trajectory_path) as f:
        data = json.load(f)

    demo_steps = []
    for step in data["trajectory"]:
        if step.get("exit_status") == "success":
            demo_steps.append({
                "action": step["action"],
                "observation": step["observation"],
            })

    return Demo(steps=demo_steps)
```

---

## 6. 与 Agent Loop 的集成

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                     │
│  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ Step 1      │──▶│ model.query()       │──▶│ Parse Action        │  │
│  │             │  │ (with history)      │  │                     │  │
│  └─────────────┘  └─────────────────────┘  └─────────────────────┘  │
│         │                                              │            │
│         │                                              ▼            │
│         │                                   ┌─────────────────────┐  │
│         │                                   │ Environment.step()  │  │
│         │                                   └─────────────────────┘  │
│         │                                              │            │
│         ▼                                              ▼            │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ create_history_item()                                          │  │
│  │   └── Add to history[]                                         │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ History Processors                                             │  │
│  │   - LastNObservations (keep last 5)                            │  │
│  │   - CacheControl (for Claude)                                  │  │
│  │   - TagToolCallObservations                                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Processed history -> Next model query                          │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 7. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| History 过长 | 调整 `last_n_observations.n` | `agent/history_processors.py:86` |
| 缓存未命中 | 检查 `cache_control` 标记 | `agent/history_processors.py:261` |
| Replay 失败 | 验证环境状态和 observation 匹配 | `run/run_replay.py` |
| Trajectory 损坏 | 检查 JSON 格式和必需字段 | `types.py:44` |
| Processor 未生效 | 验证配置文件中的 `type` 字段 | `agent/history_processors.py:390` |

---

## 8. 架构特点总结

- **链式 Processors**: 可配置、可组合的 History 转换链
- **可插拔设计**: 通过配置添加/移除 Processors
- **多目标优化**: 支持 Token 压缩、缓存优化、内容过滤
- **完整持久化**: Trajectory 包含完整执行信息
- **可重放性**: 支持从任意 trajectory 重放
- **Demo 生成**: 可从成功执行生成演示数据
