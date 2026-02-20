# CLI Entry（swe-agent）

本文基于 `sweagent/run/` 源码，解释 swe-agent 的命令行接口设计、参数解析机制和命令分发流程。

---

## 1. 先看全局（流程图）

### 1.1 CLI 命令结构图

```text
┌─────────────────────────────────────────────────────────────────┐
│  ENTRY: sweagent <command> [options]                            │
│  ┌─────────────────┐                                            │
│  │ __main__.py     │ ◄──── 入口包装器                          │
│  │   └── main()    │                                            │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ROUTER: sweagent/run/run.py                                    │
│  ┌────────────────────────────────────────┐                     │
│  │ get_cli()                              │                     │
│  │  ├── ArgumentParser                    │                     │
│  │  └── choices=["run","run-batch",...]  │                     │
│  │                                        │                     │
│  │ main()                                 │                     │
│  │  ├── parse_known_args()                │                     │
│  │  └── 命令分发(dispatch)                 │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
    ┌───────┼───────┬───────────┬───────────┬───────────┐
    ▼       ▼       ▼           ▼           ▼           ▼
┌───────┐┌───────┐┌───────┐ ┌───────┐  ┌───────┐  ┌───────────┐
│run    ││run-   ││inspect│ │inspector│  │shell  │  │ 其他工具   │
│       ││batch  ││       │ │         │  │       │  │ 命令      │
└───┬───┘└───┬───┘└───┬───┘ └────┬────┘  └───┬───┘  └─────┬─────┘
    │        │        │          │           │            │
    ▼        ▼        ▼          ▼           ▼            ▼
run_single run_batch inspector_cli server   run_shell   ...

图例: ┌─┐ 模块  ──┤ 子命令分发  ▼ 执行流向
```

### 1.2 参数解析流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│ [A] 配置加载层级（高 -> 低优先级）                                 │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │  命令行参数      │
    │  --agent.model  │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │  --config 文件   │
    │  (可多个，合并)   │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 默认配置文件      │
    │ config/default.yaml
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ pydantic 字段默认 │
    └─────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [B] BasicCLI 参数解析流程                                        │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ 原始命令行参数   │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ _parse_args_to  │
    │ _nested_dict()  │ ◄── 点号解析为嵌套结构
    │                 │     --agent.model.name gpt-4o
    │                 │        ↓
    │                 │     {"agent":{"model":{"name":"gpt-4o"}}}
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 合并多个配置源   │
    │ merge_nested_dicts()
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ pydantic-settings│
    │ BaseSettings    │ ◄── 验证 & 类型转换
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 配置对象实例     │
    │ (RunSingleConfig)│
    └─────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [C] 错误处理与自动修正                                             │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ 参数解析错误     │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ ValidationError │
    │ SettingsError   │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 友好错误提示     │
    │ ├─ 显示合并配置  │
    │ ├─ 验证错误详情  │
    │ └─ 常见错误提示  │
    │     - 连字符vs下划线
    │     - 层级结构错误 │
    └─────────────────┘

图例: 高优先级配置覆盖低优先级
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道有哪些命令和基本参数格式）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道命令结构和配置加载机制）。
- **10 分钟版**：通读 `3~8`（能定位参数解析问题和添加新命令）。

### 2.1 一句话定义

swe-agent CLI 采用「**双层路由 + pydantic-settings 配置**」设计：顶层用 `argparse` 做命令分发，底层用 `pydantic-settings` 做类型安全的配置解析。

---

## 3. 核心组件

### 3.1 argparse 路由配置

**文件**: `sweagent/run/run.py:37-67`

```python
def get_cli():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "command",
        choices=[
            "run", "r",              # 单实例运行
            "run-batch", "b",        # 批量运行
            "run-replay",            # 重放轨迹
            "traj-to-demo",          # 轨迹转演示
            "run-api",               # API 服务器
            "merge-preds",           # 合并预测
            "inspect", "i",          # TUI 查看器
            "inspector", "I",        # Web 查看器
            "extract-pred",          # 提取预测
            "compare-runs", "cr",    # 对比运行
            "remove-unfinished", "ru",  # 清理未完成
            "quick-stats", "qs",     # 快速统计
            "shell", "sh",           # 交互式 Shell
        ],
        nargs="?"
    )
```

特点：
- 使用 `choices` 限制有效命令
- 支持命令别名（如 `r` = `run`）
- `nargs="?"` 允许无命令时显示帮助

### 3.2 命令分发机制

**文件**: `sweagent/run/run.py:70-147`

```python
def main(args: list[str] | None = None):
    cli = get_cli()
    parsed_args, remaining_args = cli.parse_known_args(args)
    command = parsed_args.command

    # 延迟导入减少启动时间
    if command in ["run", "r"]:
        from sweagent.run.run_single import run_from_cli
        run_from_cli(remaining_args)
    elif command in ["run-batch", "b"]:
        from sweagent.run.run_batch import run_from_cli
        run_from_cli(remaining_args)
    # ... 其他命令
```

分发特点：
1. **延迟导入**：命令处理模块按需加载
2. **剩余参数传递**：`remaining_args` 传给子命令解析器
3. **统一入口**：每个子命令提供 `run_from_cli` 函数

### 3.3 pydantic-settings 配置基类

**文件**: `sweagent/run/common.py:187-200`

```python
class BasicCLI:
    def __init__(
        self,
        config_type: type[BaseSettings],
        *,
        default_settings: bool = True,
        help_text: str | None = None,
        default_config_file: Path = CONFIG_DIR / "default.yaml",
    ):
```

配置层级（高到低）：
1. 命令行参数（`--agent.model.name gpt-4o`）
2. `--config` 指定的配置文件
3. 默认配置文件 `~/.swe-agent/config/default.yaml`
4. pydantic 字段默认值

### 3.4 嵌套参数解析

**文件**: `sweagent/run/common.py:149-183`

```python
def _parse_args_to_nested_dict(args):
    """Parse the command-line arguments into a nested dictionary."""
    result = _nested_dict()
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg.startswith("--"):
            i += 1
            continue

        # 处理 --key=value 格式
        if "=" in arg:
            key, value = arg[2:].split("=", 1)
        # 处理 --key value 格式
        else:
            key = arg[2:]
            i += 1
            value = args[i]

        # 点号分隔转为嵌套结构
        keys = key.split(".")
        current = result
        for k in keys[:-1]:
            current = current[k]
        current[keys[-1]] = value
        i += 1
```

---

## 4. 命令详解

### 4.1 run / r（单实例运行）

**文件**: `sweagent/run/run_single.py`

运行 swe-agent 处理单个问题（如 GitHub Issue）：

```bash
sweagent run \
  --agent.model.name gpt-4o \
  --agent.model.per_instance_cost_limit 2.00 \
  --problem_statement.github_url https://github.com/org/repo/issues/1
```

配置类：`RunSingleConfig`（继承自 `BaseSettings`）

### 4.2 run-batch / b（批量运行）

**文件**: `sweagent/run/run_batch.py`

批量处理多个实例（如 SWE-bench 数据集）：

```bash
sweagent run-batch \
  --agent.model.name gpt-4o \
  --instances.type swe_bench \
  --instances.filter org/repo
```

配置类：`RunBatchConfig`

### 4.3 inspect / i（TUI 查看器）

**文件**: `sweagent/run/inspector_cli.py`

基于 Textual 的终端界面查看轨迹文件：

```bash
sweagent inspect trajectory.json
# 或
sweagent i trajectory.json
```

特性：
- Vim 风格快捷键（j/k 滚动，h/l 导航）
- 语法高亮
- 交互式探索

### 4.4 inspector / I（Web 查看器）

**文件**: `sweagent/inspector/server.py`

基于 Flask 的 Web 界面查看轨迹：

```bash
sweagent inspector --directory ./trajectories
# 或
sweagent I --directory ./trajectories
```

### 4.5 run-replay（轨迹重放）

**文件**: `sweagent/run/run_replay.py`

重放轨迹文件或演示文件：

```bash
sweagent run-replay \
  --trajectory_path trajectory.json \
  --config_file config.yaml
```

### 4.6 shell / sh（交互式 Shell）

**文件**: `sweagent/run/run_shell.py`

启动交互式环境：

```bash
sweagent shell --container_name my_env
```

---

## 5. 配置加载

### 5.1 配置文件格式

支持 YAML 和 JSON 格式：

```yaml
# config.yaml
agent:
  model:
    name: gpt-4o
    temperature: 0.0
    per_instance_cost_limit: 2.00

environment:
  type: docker
  image: sweagent/swe-agent:latest
```

### 5.2 环境变量支持

环境变量前缀 `SWE_AGENT_`：

```bash
export SWE_AGENT_AGENT__MODEL__NAME=gpt-4o
export SWE_AGENT_AGENT__MODEL__TEMPERATURE=0.0
sweagent run --problem_statement.github_url ...
```

注意：双下划线 `__` 表示层级分隔（pydantic-settings 约定）。

### 5.3 Union 类型处理

对于联合类型配置，pydantic 会尝试每种可能的类型：

```python
# 例如：部署可以是 OpenAI、Azure 等
deployment: OpenAIDeployment | AzureDeployment | ...
```

验证错误会显示所有尝试过的类型错误，帮助用户定位问题。

---

## 6. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| 参数不识别 | 检查层级路径 | 使用 `sweagent run --help` 查看正确路径 |
| 连字符 vs 下划线 | 参数名格式 | swe-agent 使用下划线：`--num_workers` 而非 `--num-workers` |
| 配置未生效 | 配置合并顺序 | 检查 `--config` 文件路径和内容 |
| Union 类型错误 | 验证错误详情 | 查看 pydantic 错误中所有尝试的类型 |
| 启动慢 | 延迟导入机制 | 正常现象，首次导入相关模块 |

### 6.1 常见错误示例

```bash
# 错误：使用连字符
sweagent run --agent.model-name gpt-4o

# 正确：使用下划线
sweagent run --agent.model.name gpt-4o
```

```bash
# 错误：层级缺失
sweagent run --model.name gpt-4o

# 正确：完整层级
sweagent run --agent.model.name gpt-4o
```

---

## 7. 架构特点总结

- **双层路由**：顶层 `argparse` 命令分发 + 底层 `pydantic-settings` 配置解析
- **延迟加载**：命令模块按需导入，减少启动时间
- **类型安全**：基于 pydantic 的配置验证和自动类型转换
- **层级配置**：点号分隔的参数路径映射到嵌套配置对象
- **多源合并**：命令行、配置文件、环境变量、默认值多层合并
- **友好错误**：自动错误提示和常见错误纠正建议
- **别名支持**：命令支持短别名（如 `r` = `run`）

---

## 8. 参考文件

| 文件 | 职责 |
|------|------|
| `sweagent/__main__.py` | 入口包装器 |
| `sweagent/run/run.py` | 主 CLI 路由 |
| `sweagent/run/common.py` | BasicCLI 基类、参数解析 |
| `sweagent/run/run_single.py` | run 命令实现 |
| `sweagent/run/run_batch.py` | run-batch 命令实现 |
| `sweagent/run/inspector_cli.py` | TUI 查看器 |
| `sweagent/inspector/server.py` | Web 查看器 |
