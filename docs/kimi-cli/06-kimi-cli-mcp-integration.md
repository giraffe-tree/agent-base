# MCP 集成（kimi-cli）

本文基于 `./kimi-cli` 源码，解释 Kimi CLI 如何实现 MCP (Model Context Protocol) 接入，重点介绍其 ACP (Agent Connect Protocol) 到 MCP 的桥接设计。

---

## 1. 先看全局（流程图）

### 1.1 ACP 到 MCP 配置转换流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  ACP Server 返回的 MCP 配置                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ ACP Response:                          │                     │
│  │  {                                     │                     │
│  │    "mcpServers": [                     │                     │
│  │      { "type": "HttpMcpServer", ... }, │                     │
│  │      { "type": "SseMcpServer", ... },  │                     │
│  │      { "type": "McpServerStdio", ... } │                     │
│  │    ]                                   │                     │
│  │  }                                     │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ACP 到 MCP 配置转换                                             │
│  ┌────────────────────────────────────────┐                     │
│  │ acp_mcp_servers_to_mcp_config()        │                     │
│  │  └── _convert_acp_mcp_server()         │                     │
│  │       ├── HttpMcpServer                │                     │
│  │       │   └── {url, transport: "http"} │                     │
│  │       ├── SseMcpServer                 │                     │
│  │       │   └── {url, transport: "sse"}  │                     │
│  │       └── McpServerStdio               │                     │
│  │           └── {command, args, env,     │                     │
│  │               transport: "stdio"}      │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  fastmcp 库处理                                                  │
│  ┌────────────────────────────────────────┐                     │
│  │ MCPConfig.model_validate(config)       │                     │
│  │  └── 实际 MCP 工具调用由 fastmcp 执行  │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 CLI 配置管理流程

```text
┌─────────────────────────────────────────────────────────────────┐
│                      用户 CLI 操作                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  mcp add    │  │  mcp remove │  │  mcp list / mcp info    │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
└─────────┼────────────────┼─────────────────────┼───────────────┘
          │                │                     │
          ▼                ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    mcp.json 配置文件                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ~/.kimi/mcp.json (通过 get_global_mcp_config_file())    │   │
│  │  {                                                      │   │
│  │    "mcpServers": {                                      │   │
│  │      "server1": { "url": "...", "transport": "http" },   │   │
│  │      "server2": { "command": "...", "transport": "stdio"} │   │
│  │    }                                                    │   │
│  │  }                                                      │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1`（知道 Kimi CLI 使用 ACP 协议桥接 MCP，依赖 fastmcp 库执行）。
- **3 分钟版**：看 `1.1` + `1.2` + `3`（知道配置转换逻辑和 CLI 管理方式）。
- **10 分钟版**：通读全文（能配置本地和远程 MCP 服务器，理解架构设计）。

### 2.1 一句话定义

Kimi CLI 的 MCP 集成采用"**ACP 桥接 + fastmcp 执行**"的设计：通过 ACP (Agent Connect Protocol) 协议获取 MCP 服务器配置，转换为标准 MCP 配置后，由 `fastmcp` 库负责实际的 MCP 工具调用执行。

---

## 3. 核心组件详解

### 3.1 ACP 到 MCP 配置转换

**文件**: `src/kimi_cli/acp/mcp.py`

这是 Kimi CLI MCP 集成的核心——将 ACP 协议的 MCP 服务器配置转换为标准 MCP 配置：

```python
from __future__ import annotations
from typing import Any
import acp.schema
from fastmcp.mcp_config import MCPConfig
from pydantic import ValidationError

from kimi_cli.acp.types import MCPServer
from kimi_cli.exception import MCPConfigError


def acp_mcp_servers_to_mcp_config(mcp_servers: list[MCPServer]) -> MCPConfig:
    """将 ACP MCP 服务器列表转换为 fastmcp 的 MCPConfig"""
    if not mcp_servers:
        return MCPConfig()

    try:
        return MCPConfig.model_validate(
            {"mcpServers": {server.name: _convert_acp_mcp_server(server) for server in mcp_servers}}
        )
    except ValidationError as exc:
        raise MCPConfigError(f"Invalid MCP config from ACP client: {exc}") from exc


def _convert_acp_mcp_server(server: MCPServer) -> dict[str, Any]:
    """将单个 ACP MCP 服务器转换为字典表示"""
    match server:
        case acp.schema.HttpMcpServer():
            return {
                "url": server.url,
                "transport": "http",
                "headers": {header.name: header.value for header in server.headers},
            }
        case acp.schema.SseMcpServer():
            return {
                "url": server.url,
                "transport": "sse",
                "headers": {header.name: header.value for header in server.headers},
            }
        case acp.schema.McpServerStdio():
            return {
                "command": server.command,
                "args": server.args,
                "env": {item.name: item.value for item in server.env},
                "transport": "stdio",
            }
```

**支持的传输类型**:

| ACP 类型 | MCP 配置 | 说明 |
|---------|---------|------|
| `HttpMcpServer` | `transport: "http"` | HTTP 流式传输 |
| `SseMcpServer` | `transport: "sse"` | Server-Sent Events |
| `McpServerStdio` | `transport: "stdio"` | 标准输入输出 |

### 3.2 CLI 配置管理

**文件**: `src/kimi_cli/cli/mcp.py`

Kimi CLI 提供了一组 CLI 命令来管理 MCP 服务器配置：

```python
import json
from pathlib import Path
from typing import Annotated, Any, Literal
import typer

cli = typer.Typer(help="Manage MCP server configurations.")

Transport = Literal["stdio", "http"]


def get_global_mcp_config_file() -> Path:
    """获取全局 MCP 配置文件路径"""
    from kimi_cli.share import get_share_dir
    return get_share_dir() / "mcp.json"


def _load_mcp_config() -> dict[str, Any]:
    """从全局 MCP 配置文件加载配置"""
    from fastmcp.mcp_config import MCPConfig
    from pydantic import ValidationError

    mcp_file = get_global_mcp_config_file()
    if not mcp_file.exists():
        return {"mcpServers": {}}

    config = json.loads(mcp_file.read_text(encoding="utf-8"))
    MCPConfig.model_validate(config)  # 验证配置有效性
    return config


def _save_mcp_config(config: dict[str, Any]) -> None:
    """保存 MCP 配置到默认文件"""
    mcp_file = get_global_mcp_config_file()
    mcp_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
```

### 3.3 mcp add 命令

**文件**: `src/kimi_cli/cli/mcp.py:83-150`

```python
@cli.command("add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="MCP server name")],
    server_args: Annotated[list[str] | None, typer.Argument(help="Server arguments")] = None,
    transport: Transport = "stdio",
    env: Annotated[list[str] | None, typer.Option(help="Environment variables (KEY=VALUE)")] = None,
    header: Annotated[list[str] | None, typer.Option(help="HTTP headers (KEY:VALUE)")] = None,
    auth: Annotated[str | None, typer.Option(help="Auth type (currently only 'oauth')")] = None,
):
    """添加一个 MCP 服务器"""
    config = _load_mcp_config()

    if transport == "stdio":
        # stdio 传输: 需要 command 和 args
        if not server_args:
            raise typer.BadParameter("stdio transport requires command arguments")
        command = server_args[0]
        command_args = server_args[1:]
        server_config = {"command": command, "args": command_args}
        if env:
            server_config["env"] = _parse_key_value_pairs(env, "env")
    else:
        # HTTP 传输: 需要 URL
        if not server_args:
            raise typer.BadParameter("http transport requires URL argument")
        server_config = {"url": server_args[0], "transport": "http"}
        if header:
            server_config["headers"] = _parse_key_value_pairs(header, "header", separator=":")
        if auth:
            server_config["auth"] = auth

    config["mcpServers"][name] = server_config
    _save_mcp_config(config)
    typer.echo(f"Added MCP server '{name}' with {transport} transport.")
```

**使用示例**:

```bash
# 添加 HTTP MCP 服务器
kimi mcp add --transport http context7 https://mcp.context7.com/mcp \
  --header "CONTEXT7_API_KEY: ctx7sk-your-key"

# 添加带 OAuth 的 HTTP MCP 服务器
kimi mcp add --transport http --auth oauth linear https://mcp.linear.app/mcp

# 添加 stdio MCP 服务器
kimi mcp add --transport stdio chrome-devtools -- npx chrome-devtools-mcp@latest
```

### 3.4 MCP 配置结构

Kimi CLI 使用的 MCP 配置格式（存储在 `~/.kimi/mcp.json`）：

```json
{
  "mcpServers": {
    "context7": {
      "url": "https://mcp.context7.com/mcp",
      "transport": "http",
      "headers": {
        "CONTEXT7_API_KEY": "ctx7sk-xxxx"
      }
    },
    "linear": {
      "url": "https://mcp.linear.app/mcp",
      "transport": "http",
      "auth": "oauth"
    },
    "chrome-devtools": {
      "command": "npx",
      "args": ["chrome-devtools-mcp@latest"],
      "transport": "stdio"
    }
  }
}
```

---

## 4. fastmcp 集成

Kimi CLI 使用 `fastmcp` 库来处理实际的 MCP 协议通信：

### 4.1 配置验证

```python
from fastmcp.mcp_config import MCPConfig
from pydantic import ValidationError

# 验证配置
config = MCPConfig.model_validate({
    "mcpServers": {
        "server1": {"url": "...", "transport": "http"}
    }
})
```

### 4.2 执行流程

1. Kimi CLI 从 ACP Server 获取 MCP 服务器列表
2. 调用 `acp_mcp_servers_to_mcp_config()` 转换为 `MCPConfig`
3. `fastmcp` 库根据配置创建 MCP 客户端连接
4. 工具调用由 `fastmcp` 转发到相应的 MCP 服务器

---

## 5. 架构特点总结

| 特性 | 实现方式 | 说明 |
|-----|---------|------|
| **协议桥接** | ACP → MCP | 通过 ACP 协议获取配置，转换为标准 MCP |
| **传输支持** | HTTP / SSE / Stdio | 三种传输方式 |
| **配置管理** | CLI + JSON 文件 | `kimi mcp add/remove/list` 命令 |
| **执行引擎** | fastmcp 库 | 由 fastmcp 处理实际 MCP 通信 |
| **认证支持** | OAuth / Headers | 支持 OAuth 和自定义 Header 认证 |

---

## 6. 与其他 Agent 的对比

| 特性 | Kimi CLI | Codex | Gemini CLI | OpenCode |
|-----|----------|-------|------------|----------|
| **集成层级** | 配置层 | 完整实现 | 完整实现 | 完整实现 |
| **执行引擎** | fastmcp | 自研 RmcpClient | 自研 McpClient | @modelcontextprotocol/sdk |
| **传输支持** | HTTP/SSE/Stdio | HTTP/SSE/Stdio | HTTP/SSE/Stdio/WebSocket | HTTP/SSE/Stdio |
| **配置来源** | ACP + CLI | 配置文件 | 配置文件 + Extension | 多级配置 |
| **OAuth** | 支持 | 支持 | 完整 OAuth 2.0 | 动态注册 |

---

## 7. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| MCP 配置无效 | 检查 `MCPConfig.model_validate()` 错误 | `cli/mcp.py:31` |
| ACP 转换失败 | 检查 ACP 服务器返回的 type 字段 | `acp/mcp.py:27` |
| 配置文件位置 | 查看 `~/.kimi/mcp.json` | `cli/mcp.py:14` |
| 传输类型错误 | 确认 type 为 http/sse/stdio 之一 | `acp/mcp.py` |
