# Tool System（kimi-cli）

本文基于 `./kimi-cli` 源码，解释 Kimi CLI 的工具系统架构——从工具定义、参数提取到 ACP MCP 集成的完整链路。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────┐
│  工具层：按功能域组织的工具模块                                     │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │
│  │  file/      │ │  shell/     │ │   web/      │ │ multiagent/ │ │
│  │  • read     │ │  • command  │ │  • fetch    │ │  • task     │ │
│  │  • write    │ │             │ │  • search   │ │  • create   │ │
│  │  • replace  │ │             │ │             │ │             │ │
│  │  • glob     │ │             │ │             │ │             │ │
│  │  • grep     │ │             │ │             │ │             │ │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ │
│  ┌─────────────┐ ┌─────────────┐                                 │
│  │   think/    │ │    todo/    │                                 │
│  │  • Think    │ │  • SetTodoList│                                │
│  └─────────────┘ └─────────────┘                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  工具元数据层：提取关键参数用于 UI 展示                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  extract_key_argument()                                    │ │
│  │  ├── 解析 JSON 参数                                         │ │
│  │  ├── 按工具类型提取关键字段                                  │ │
│  │  │   ├── Shell → command                                   │ │
│  │  │   ├── ReadFile → path                                   │ │
│  │  │   ├── Task → description                                │ │
│  │  │   ├── SearchWeb → query                                 │ │
│  │  │   └── ...                                               │ │
│  │  └── 截断显示 (shorten_middle, width=50)                   │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  MCP 集成层：通过 ACP 协议扩展工具能力                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  acp_mcp_servers_to_mcp_config()                           │ │
│  │  ├── 支持 HTTP MCP Server                                  │ │
│  │  ├── 支持 SSE MCP Server                                   │ │
│  │  └── 支持 Stdio MCP Server (command + args)               │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念与设计哲学

### 2.1 一句话定义

Kimi CLI 的工具系统是「**模块化功能域 + 统一参数提取 + ACP 协议扩展**」的三层架构：工具按功能分组实现，通过 `extract_key_argument` 统一提取关键参数用于 UI 展示，通过 ACP 协议集成外部 MCP 服务。

### 2.2 设计特点

| 特性 | 实现方式 | 优势 |
|------|---------|------|
| 模块化组织 | 按功能域分目录 (`file/`, `shell/`, `web/` 等) | 清晰的代码结构，易于扩展 |
| 参数提取 | `extract_key_argument()` 统一处理 | UI 一致性，支持流式 JSON 解析 |
| MCP 集成 | ACP (Agent Communication Protocol) | 标准化协议，支持多种传输方式 |
| 错误处理 | `SkipThisTool` 异常 | 工具可主动跳过自身加载 |

---

## 3. 工具定义与组织

### 3.1 目录结构

```
kimi-cli/src/kimi_cli/tools/
├── __init__.py          # 工具初始化 + 参数提取
├── file/                # 文件操作工具
│   ├── read.py          # ReadFile, ReadMediaFile
│   ├── write.py         # WriteFile
│   ├── replace.py       # StrReplaceFile
│   ├── glob.py          # Glob
│   └── grep_local.py    # Grep
├── shell/               # Shell 执行工具
│   └── ...              # Shell
├── web/                 # Web 操作工具
│   ├── fetch.py         # FetchURL
│   └── search.py        # SearchWeb
├── multiagent/          # 多 Agent 协作工具
│   ├── task.py          # Task
│   └── create.py        # CreateSubagent
├── think/               # 思考工具
│   └── ...              # Think
└── todo/                # 待办工具
    └── ...              # SetTodoList
```

### 3.2 内置工具清单

| 工具名 | 功能域 | 关键参数 | 说明 |
|--------|--------|----------|------|
| `Shell` | shell | command | 执行 shell 命令 |
| `ReadFile` | file | path | 读取文件内容 |
| `ReadMediaFile` | file | path | 读取媒体文件 |
| `WriteFile` | file | path | 写入文件 |
| `StrReplaceFile` | file | path | 字符串替换 |
| `Glob` | file | pattern | 文件匹配 |
| `Grep` | file | pattern | 文件搜索 |
| `SearchWeb` | web | query | 网页搜索 |
| `FetchURL` | web | url | URL 内容获取 |
| `Task` | multiagent | description | 创建子任务 |
| `CreateSubagent` | multiagent | name | 创建子 Agent |
| `Think` | think | thought | 思考记录 |
| `SetTodoList` | todo | - | 设置待办列表 |
| `SendDMail` | comm | - | 发送邮件 |

---

## 4. 关键参数提取机制

### 4.1 核心函数

```python
# kimi-cli/src/kimi_cli/tools/__init__.py
def extract_key_argument(
    json_content: str | streamingjson.Lexer,
    tool_name: str
) -> str | None:
    """从工具调用的 JSON 参数中提取关键参数，用于 UI 展示。"""
```

### 4.2 提取逻辑

| 工具类型 | 提取字段 | 示例输出 |
|----------|----------|----------|
| Shell | command | `ls -la` |
| ReadFile | path | `src/kimi_cli/app.py` |
| Task | description | `修复登录 bug` |
| SearchWeb | query | `Python asyncio` |
| FetchURL | url | `https://example.com` |
| CreateSubagent | name | `code_reviewer` |
| Think | thought | `让我分析一下...` |
| 其他 | 完整 JSON | 截断后显示 |

### 4.3 流式 JSON 支持

```python
if isinstance(json_content, streamingjson.Lexer):
    json_str = json_content.complete_json()
else:
    json_str = json_content
```

支持流式 JSON 解析，可在参数未完全接收时实时提取。

---

## 5. ACP MCP 集成

### 5.1 架构位置

```
┌─────────────────────────────────────────┐
│  Kimi CLI Core                          │
│  ┌─────────────────────────────────────┐│
│  │  Tool Registry (built-in tools)    ││
│  └─────────────────────────────────────┘│
│                   │                     │
│                   ▼                     │
│  ┌─────────────────────────────────────┐│
│  │  ACP Client                         ││
│  │  ┌───────────────────────────────┐  ││
│  │  │  acp_mcp_servers_to_mcp_config│  ││
│  │  └───────────────────────────────┘  ││
│  └─────────────────────────────────────┘│
│                   │                     │
│                   ▼                     │
│  ┌─────────────────────────────────────┐│
│  │  External MCP Servers               ││
│  │  • HTTP Transport                   ││
│  │  • SSE Transport                    ││
│  │  • Stdio Transport                  ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

### 5.2 MCP Server 配置转换

```python
# kimi-cli/src/kimi_cli/acp/mcp.py
def acp_mcp_servers_to_mcp_config(mcp_servers: list[MCPServer]) -> MCPConfig:
    """将 ACP MCP Server 配置转换为内部 MCPConfig。"""
```

支持的传输方式：

| 传输类型 | 配置字段 | 说明 |
|----------|----------|------|
| HTTP | url, headers | 直接 HTTP 连接 |
| SSE | url, headers | Server-Sent Events |
| Stdio | command, args, env | 本地子进程 |

### 5.3 Server 类型匹配

```python
match server:
    case acp.schema.HttpMcpServer():
        return {"url": server.url, "transport": "http", ...}
    case acp.schema.SseMcpServer():
        return {"url": server.url, "transport": "sse", ...}
    case acp.schema.McpServerStdio():
        return {"command": server.command, "transport": "stdio", ...}
```

---

## 6. 工具调用流程

### 6.1 完整流程图

```text
用户输入
    │
    ▼
┌─────────────────┐
│ LLM 生成工具调用 │
│ (JSON 格式参数)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ extract_key_arg │◄── 提取关键参数用于 UI
│ (显示工具调用)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  工具分发执行    │
│  • 内置工具     │
│  • MCP 工具     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  返回执行结果    │
│  (追加到历史)   │
└─────────────────┘
```

### 6.2 错误处理

```python
class SkipThisTool(Exception):
    """Raised when a tool decides to skip itself from the loading process."""
```

工具可在加载过程中主动跳过自身，用于条件化工具可用性。

---

## 7. 与其他组件的交互

### 7.1 与 Agent Loop 的关系

Kimi CLI 的 Tool System 被 Agent Loop 调用：

1. **调用前**: `extract_key_argument` 提取参数用于 UI 预览
2. **调用中**: 执行内置工具或转发 MCP 工具
3. **调用后**: 结果格式化并追加到对话历史

### 7.2 与 MCP 的协作

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Kimi CLI   │────▶│  ACP Client │────▶│ MCP Servers │
│  Tool Call  │     │  (convert)  │     │  (external) │
└─────────────┘     └─────────────┘     └─────────────┘
       │                                            │
       │◄───────────────────────────────────────────┘
       │              工具执行结果
       ▼
┌─────────────┐
│  Response   │
│  to LLM     │
└─────────────┘
```

---

## 8. 架构特点总结

- **模块化组织**: 工具按功能域分组，代码结构清晰
- **统一参数提取**: `extract_key_argument` 提供一致的 UI 体验
- **流式 JSON 支持**: 支持实时解析部分接收的参数
- **ACP 协议集成**: 标准化 MCP 集成，支持多种传输方式
- **工具自跳过**: `SkipThisTool` 支持条件化工具加载

---

## 9. 排障速查

- **工具参数显示异常**: 检查 `extract_key_argument` 中的工具类型匹配
- **MCP 工具无法调用**: 检查 `acp_mcp_servers_to_mcp_config` 的转换结果
- **路径显示过长**: 检查 `_normalize_path` 是否生效
- **工具加载跳过**: 查看是否抛出 `SkipThisTool`
