# CLI Entry（kimi-cli）

本文基于 `src/kimi_cli/cli/` 源码，解释 kimi-cli 的命令行接口设计、参数解析机制和命令分发流程。

---

## 1. 先看全局（流程图）

### 1.1 CLI 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│  ENTRY: kimi [options] [command]                                │
│  ┌─────────────────┐                                            │
│  │ __main__.py     │ ◄──── 入口包装器                          │
│  │   └── cli()     │                                            │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ROUTER: src/kimi_cli/cli/__init__.py                           │
│  ┌────────────────────────────────────────┐                     │
│  │ typer.Typer()                          │                     │
│  │  ├── callback(kimi)                    │ ◄── 主命令回调      │
│  │  │   └── 参数解析与验证                │                     │
│  │  ├── add_typer(info_cli)               │                     │
│  │  ├── add_typer(mcp_cli)                │                     │
│  │  ├── add_typer(web_cli)                │                     │
│  │  └── 命令: login/logout/term/acp/...   │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  UI 模式选择                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │  shell   │ │  print   │ │   acp    │ │   wire   │           │
│  │ (默认)   │ │(非交互)  │ │(服务器)  │ │(实验性)  │           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘           │
│       │            │            │            │                  │
│       ▼            ▼            ▼            ▼                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │交互式   │  │批处理   │  │ACP协议  │  │Wire协议 │            │
│  │Shell UI│  │文本/JSON│  │服务器   │  │服务器   │            │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘            │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ Typer 模块  ──┤ 子命令  ▼ 执行流向
```

### 1.2 命令处理流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│ [A] 主命令回调流程                                               │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ kimi callback   │ ◄── invoke_without_command=True
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 参数验证         │
    │ ├─ 冲突检查      │ ◄── --print 与 --acp 互斥
    │ ├─ 模式验证      │ ◄── input-format 需 print 模式
    │ └─ 配置解析      │ ◄─ --config 或 --config-file
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ UI 模式确定      │
    ├─────────────────┤
    │ --print → print │
    │ --acp   → acp   │
    │ --wire  → wire  │
    │ 默认    → shell │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 会话管理         │
    │ ├─ --session    │ ◄── 指定会话 ID
    │ ├─ --continue   │ ◄── 继续上次会话
    │ └─ 默认         │ ◄── 创建新会话
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ KimiCLI.create  │ ◄── 初始化 CLI 实例
    │ 并运行对应模式   │
    └─────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [B] MCP 配置管理流程                                             │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ kimi mcp [cmd]  │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 全局配置路径     │ ◄── ~/.kimi/mcp.json
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 子命令处理       │
    ├─────────────────┤
    │ add    → 添加   │
    │ remove → 移除   │
    │ list   → 列出   │
    │ auth   → OAuth  │
    │ test   → 测试   │
    └─────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│ [C] Web 服务器启动流程                                           │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │ kimi web [opts] │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ 参数解析         │
    │ ├─ --host       │
    │ ├─ --port       │ (默认 5494)
    │ ├─ --network    │ ◄── 绑定 0.0.0.0
    │ └─ --auth-token │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ run_web_server  │ ◄── FastAPI + uvicorn
    └─────────────────┘

图例: callback 处理主命令逻辑，subcommand 处理子命令
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道 4 种 UI 模式和主要子命令）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道会话管理和 MCP 配置）。
- **10 分钟版**：通读 `3~8`（能添加新命令和修改参数解析）。

### 2.1 一句话定义

kimi-cli 采用「**Typer 框架 + 多模式架构**」设计：使用 Typer 处理参数解析和命令路由，支持 shell/print/acp/wire 四种运行模式，集成会话管理和 MCP 服务器配置。

---

## 3. 核心组件

### 3.1 Typer CLI 配置

**文件**: `src/kimi_cli/cli/__init__.py:34-41`

```python
cli = typer.Typer(
    epilog="""\
Documentation:        https://moonshotai.github.io/kimi-cli/\n
LLM friendly version: https://moonshotai.github.io/kimi-cli/llms.txt""",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Kimi, your next CLI agent.",
)
```

### 3.2 主命令回调

**文件**: `src/kimi_cli/cli/__init__.py:54-303`

```python
@cli.callback(invoke_without_command=True)
def kimi(
    ctx: typer.Context,
    # Meta 选项
    version: Annotated[bool, typer.Option("--version", "-V", ...)] = False,
    verbose: Annotated[bool, typer.Option("--verbose", ...)] = False,
    debug: Annotated[bool, typer.Option("--debug", ...)] = False,

    # 基本配置
    local_work_dir: Annotated[Path | None, typer.Option("--work-dir", "-w", ...)] = None,
    session_id: Annotated[str | None, typer.Option("--session", "-S", ...)] = None,
    continue_: Annotated[bool, typer.Option("--continue", "-C", ...)] = False,

    # 运行模式
    print_mode: Annotated[bool, typer.Option("--print", ...)] = False,
    acp_mode: Annotated[bool, typer.Option("--acp", ...)] = False,
    wire_mode: Annotated[bool, typer.Option("--wire", ...)] = False,

    # 提示与自动化
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", ...)] = None,
    yolo: Annotated[bool, typer.Option("--yolo", "-y", "--yes", ...)] = False,

    # 自定义
    agent: Annotated[Literal["default", "okabe"] | None, typer.Option("--agent", ...)] = None,
    agent_file: Annotated[Path | None, typer.Option("--agent-file", ...)] = None,
    mcp_config_file: Annotated[list[Path] | None, typer.Option("--mcp-config-file", ...)] = None,

    # 循环控制
    max_steps_per_turn: Annotated[int | None, typer.Option("--max-steps-per-turn", ...)] = None,
    max_retries_per_step: Annotated[int | None, typer.Option("--max-retries-per-step", ...)] = None,
    ...
):
    """Kimi, your next CLI agent."""
    if ctx.invoked_subcommand is not None:
        return  # 有子命令时跳过

    # 参数验证与处理...
    # UI 模式确定...
    # 会话管理...
    # 启动对应模式...
```

### 3.3 参数冲突检查

**文件**: `src/kimi_cli/cli/__init__.py:357-382`

```python
conflict_option_sets = [
    {
        "--print": print_mode,
        "--acp": acp_mode,
        "--wire": wire_mode,
    },
    {
        "--agent": agent is not None,
        "--agent-file": agent_file is not None,
    },
    {
        "--continue": continue_,
        "--session": session_id is not None,
    },
    {
        "--config": config_string is not None,
        "--config-file": config_file is not None,
    },
]
for option_set in conflict_option_sets:
    active_options = [flag for flag, active in option_set.items() if active]
    if len(active_options) > 1:
        raise typer.BadParameter(
            f"Cannot combine {', '.join(active_options)}.",
            param_hint=active_options[0],
        )
```

### 3.4 子命令注册

**文件**: `src/kimi_cli/cli/__init__.py:623-761`

```python
# 添加子命令组
cli.add_typer(info_cli, name="info")
cli.add_typer(mcp_cli, name="mcp")
cli.add_typer(web_cli, name="web")

# 独立子命令
@cli.command()
def login(json: bool = typer.Option(False, "--json", ...)) -> None:
    """Login to your Kimi account."""
    ...

@cli.command()
def logout(...) -> None:
    """Logout from your Kimi account."""
    ...

@cli.command()
def term(ctx: typer.Context) -> None:
    """Run Toad TUI backed by Kimi Code CLI ACP server."""
    ...

@cli.command()
def acp() -> None:
    """Run Kimi Code CLI ACP server."""
    ...
```

---

## 4. 命令详解

### 4.1 kimi（默认命令）

交互式 shell 模式（默认）：

```bash
# 进入交互式 shell
kimi

# 指定工作目录
kimi --work-dir /path/to/project

# 继续上次会话
kimi --continue

# 指定会话
kimi --session <session-id>

# 单次执行（非交互）
kimi --prompt "解释这段代码" --print

# 自动确认所有操作
kimi --yolo

# 指定模型
kimi --model kimi-k2-0711-preview

# 启用/禁用思考模式
kimi --thinking
kimi --no-thinking
```

### 4.2 kimi login / logout

认证管理：

```bash
# 交互式登录
kimi login

# JSON 格式输出（用于脚本）
kimi login --json

# 登出
kimi logout
```

### 4.3 kimi mcp（MCP 服务器管理）

**文件**: `src/kimi_cli/cli/mcp.py`

```bash
# 添加 stdio MCP 服务器
kimi mcp add my-server -- npx my-mcp-server

# 添加 HTTP MCP 服务器
kimi mcp add context7 https://mcp.context7.com/mcp --transport http

# 带环境变量
kimi mcp add my-server --env KEY=VALUE -- command

# 带 HTTP 头
kimi mcp add my-server https://... --header "Authorization: Bearer token"

# OAuth 认证
kimi mcp add linear https://mcp.linear.app/mcp --transport http --auth oauth

# 列出服务器
kimi mcp list

# 移除服务器
kimi mcp remove my-server

# OAuth 授权
kimi mcp auth my-server

# 测试连接
kimi mcp test my-server

# 重置授权
kimi mcp reset-auth my-server
```

### 4.4 kimi web（Web 界面）

**文件**: `src/kimi_cli/cli/web.py`

```bash
# 启动 Web 服务器（默认本地访问）
kimi web

# 允许网络访问
kimi web --network

# 指定端口
kimi web --port 8080

# 指定绑定地址
kimi web --host 0.0.0.0

# 禁用自动打开浏览器
kimi web --no-open

# 设置认证令牌
kimi web --auth-token my-secret-token

# 允许公网访问（默认仅局域网）
kimi web --public
```

### 4.5 kimi acp（ACP 服务器）

```bash
# 启动 ACP 服务器
kimi acp
```

### 4.6 kimi term（Toad TUI）

```bash
# 启动 Toad TUI
kimi term
```

### 4.7 kimi info

```bash
# 显示系统信息
kimi info

# 显示版本
kimi --version
```

---

## 5. 会话管理集成

### 5.1 会话生命周期

**文件**: `src/kimi_cli/cli/__init__.py:457-482`

```python
async def _run(session_id: str | None) -> tuple[Session, bool]:
    """Create/load session and run the CLI instance."""
    if session_id is not None:
        # 查找或创建指定会话
        session = await Session.find(work_dir, session_id)
        if session is None:
            session = await Session.create(work_dir, session_id)
    elif continue_:
        # 继续上次会话
        session = await Session.continue_(work_dir)
        if session is None:
            raise typer.BadParameter("No previous session found")
    else:
        # 创建新会话
        session = await Session.create(work_dir)

    # 创建 KimiCLI 实例并运行
    instance = await KimiCLI.create(session, ...)
    ...
```

### 5.2 会话持久化

**文件**: `src/kimi_cli/cli/__init__.py:528-556`

```python
async def _post_run(last_session: Session, succeeded: bool) -> None:
    metadata = load_metadata()
    work_dir_meta = metadata.get_work_dir_meta(last_session.work_dir)

    if last_session.is_empty():
        # 空会话，删除
        await last_session.delete()
        work_dir_meta.last_session_id = None
    else:
        # 保存会话 ID
        work_dir_meta.last_session_id = last_session.id

    save_metadata(metadata)
```

---

## 6. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| 参数冲突错误 | 冲突选项检查 | 不能同时用 `--print` 和 `--acp` |
| 会话找不到 | `--continue` 无历史 | 先创建会话再使用 `--continue` |
| MCP 连接失败 | 配置文件格式 | 检查 `~/.kimi/mcp.json` JSON 格式 |
| Web 无法访问 | 绑定地址 | 使用 `--network` 或 `--host 0.0.0.0` |
| 安静模式冲突 | 选项组合 | `--quiet` 不能与 `--acp` 一起使用 |
| 输入格式错误 | 模式限制 | `--input-format` 需要 `--print` 模式 |

### 6.1 配置验证

```python
# MCP 配置验证
kimi mcp list  # 会显示配置文件路径和解析错误

# 测试 MCP 连接
kimi mcp test <server-name>
```

### 6.2 调试信息

```bash
# 启用调试日志
kimi --debug

# 同时打印日志到 stderr
kimi --debug --verbose

# 查看日志文件位置
cat ~/.kimi/logs/kimi.log
```

---

## 7. 架构特点总结

- **Typer 框架**：类型安全的参数解析，自动帮助生成
- **多模式架构**：shell（交互）、print（批处理）、acp（协议服务器）、wire（实验性）
- **会话管理**：自动会话创建、恢复、清理
- **MCP 集成**：完整的 MCP 服务器生命周期管理
- **配置分层**：命令行参数 > 配置文件 > 环境变量 > 默认值
- **冲突检查**：显式参数互斥验证
- **异常处理**：友好的错误提示和日志记录
- **重载机制**：支持配置热重载（Reload 异常）

---

## 8. 参考文件

| 文件 | 职责 |
|------|------|
| `src/kimi_cli/cli/__main__.py` | 入口包装器 |
| `src/kimi_cli/cli/__init__.py` | 主 CLI 定义、参数解析、命令分发 |
| `src/kimi_cli/cli/mcp.py` | MCP 子命令组 |
| `src/kimi_cli/cli/web.py` | Web 子命令组 |
| `src/kimi_cli/cli/info.py` | Info 子命令组 |
| `src/kimi_cli/cli/toad.py` | Toad TUI 实现 |
| `src/kimi_cli/session.py` | 会话管理 |
| `src/kimi_cli/config.py` | 配置加载 |
| `pyproject.toml` | 入口点定义：`kimi = "kimi_cli.cli:cli"` |
