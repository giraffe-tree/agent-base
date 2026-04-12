# 跨项目 WebSearch 实现对比分析

> **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 25-35 分钟 |
> | 前置文档 | 各项目 WebSearch 实现文档（见文末延伸阅读） |
> | 文档结构 | TL;DR → 架构分类 → 详细对比 → 设计取舍 → 决策建议 |

---

## TL;DR（结论先行）

**一句话总结**：7 个 AI Coding Agent 项目呈现三种 WebSearch 架构模式——厂商提供 WebSearch 接口（Codex、Gemini CLI、Claude Code）通过模型提供商原生搜索能力实现零维护成本；自助接入 WebSearch（Kimi CLI、OpenCode、Qwen Code）通过独立搜索服务获得灵活性和可控性；SWE-agent 则不具有 Web 搜索能力，只使用本地工具完成任务。

### 核心要点速览

| 维度 | 厂商提供 WebSearch 接口 | 自助接入 WebSearch | 无 Web 搜索能力 |
|-----|------------------------|-------------------|----------------|
| **代表项目** | Codex, Gemini CLI, Claude Code | Kimi CLI, OpenCode, Qwen Code | SWE-agent |
| **核心优势** | 零维护、低延迟、深度集成 | 灵活可控、多后端支持、可定制 | 零成本、离线可用、专注代码 |
| **主要劣势** | 供应商锁定、功能受限 | 运维成本、配置复杂 | 无法获取外部信息 |
| **适用场景** | 通用 AI 助手、快速部署 | 企业定制、多地区部署 | 纯代码库任务、离线环境 |

---

## 1. 整体架构分类

### 1.1 三种架构模式概览

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         WebSearch 架构模式分类                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐  │
│  │ 厂商提供 WebSearch 接口  │  │    自助接入 WebSearch    │  │    无 Web 搜索能力       │  │
│  │     (3个项目)           │  │     (3个项目)            │  │     (1个项目)            │  │
│  ├─────────────────────────┤  ├─────────────────────────┤  ├─────────────────────────┤  │
│  │ • Codex                 │  │ • Kimi CLI              │  │ • SWE-agent             │  │
│  │ • Gemini CLI            │  │ • OpenCode              │  │                         │  │
│  │ • Claude Code           │  │ • Qwen Code             │  │                         │  │
│  └────────────┬────────────┘  └────────────┬────────────┘  └────────────┬────────────┘  │
│               │                            │                            │               │
│               ▼                            ▼                            ▼               │
│  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐  │
│  │   模型 API 原生工具      │  │     独立搜索服务         │  │     本地代码搜索         │  │
│  │ • OpenAI web_search     │  │ • Moonshot API          │  │ • grep/find             │  │
│  │ • Gemini googleSearch   │  │ • Exa AI MCP            │  │ • Playwright            │  │
│  │ • Anthropic web_search  │  │ • Tavily/Google API     │  │   浏览器自动化          │  │
│  └─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 各项目在系统中的位置

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Agent Loop 层                                   │
│         (所有项目: 决策何时/如何调用搜索工具)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
│ 厂商提供 WebSearch 接口 │ │   自助接入 WebSearch   │ │    无 Web 搜索能力     │
├───────────────────────┤ ├───────────────────────┤ ├───────────────────────┤
│                       │ │                       │ │                       │
│ ┌───────────────────┐ │ │ ┌───────────────────┐ │ │ ┌───────────────────┐ │
│ │    工具注册层      │ │ │ │    工具注册层      │ │ │ │    工具注册层      │ │
│ │  (声明式/MCPT)    │ │ │ │    (声明式)        │ │ │ │   (Bundle机制)    │ │
│ └─────────┬─────────┘ │ │ └─────────┬─────────┘ │ │ └─────────┬─────────┘ │
│           │           │ │           │           │ │           │           │
│ ┌─────────▼─────────┐ │ │ ┌─────────▼─────────┐ │ │ ┌─────────▼─────────┐ │
│ │   模型 API 调用    │ │ │ │   HTTP/MCP 调用    │ │ │ │    本地命令执行    │ │
│ │    (内置工具)      │ │ │ │    (外部服务)      │ │ │ │   (grep/find)     │ │
│ └─────────┬─────────┘ │ │ └─────────┬─────────┘ │ │ └─────────┬─────────┘ │
│           │           │ │           │           │ │           │           │
│ ┌─────────▼─────────┐ │ │ ┌─────────▼─────────┐ │ │ ┌─────────▼─────────┐ │
│ │    模型提供商      │ │ │ │    搜索服务商      │ │ │ │    本地文件系统    │ │
│ │   搜索基础设施     │ │ │ │    (第三方)        │ │ │ │   / Playwright    │ │
│ └───────────────────┘ │ │ └───────────────────┘ │ │ └───────────────────┘ │
│                       │ │                       │ │                       │
│   Codex/Gemini/       │ │   Kimi/OpenCode/      │ │     SWE-agent         │
│   Claude Code         │ │   Qwen Code           │ │                       │
└───────────────────────┘ └───────────────────────┘ └───────────────────────┘
```

---

## 2. 核心维度详细对比

### 2.1 实现方式对比

| 项目 | 架构模式 | 搜索实现 | 配套工具 | 工具数量 |
|-----|---------|---------|---------|---------|
| **Codex** | 厂商提供 WebSearch 接口 | OpenAI Responses API `web_search` | 无（依赖模型能力） | 1 |
| **Gemini CLI** | 厂商提供 WebSearch 接口 | Gemini API `googleSearch` Grounding | `web_fetch` (URL获取) | 2 |
| **Claude Code** | 厂商提供 WebSearch 接口 | Anthropic API `web_search_20250305` | 无（依赖模型能力） | 1 |
| **Kimi CLI** | 自助接入 WebSearch | Moonshot 搜索服务 API | `FetchURL` (服务优先+本地降级) | 2 |
| **OpenCode** | 自助接入 WebSearch | Exa AI MCP 服务 | `webfetch` + `codesearch` | 3 |
| **Qwen Code** | 自助接入 WebSearch | Tavily/Google/DashScope Provider | `web_fetch` | 2 |
| **SWE-agent** | 无 Web 搜索能力 | 无搜索引擎 | `find_file`/`search_file`/`search_dir` + `web_browser` | 4 |

### 2.2 搜索 Provider 对比

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                          搜索 Provider 选择                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  厂商提供 WebSearch 接口        自助接入 WebSearch           无 Web 搜索能力  │
│  ┌──────────────┐             ┌──────────────┐             ┌──────────────┐ │
│  │ OpenAI       │             │ Moonshot     │             │ 无外部依赖   │ │
│  │ (Codex)      │             │ (Kimi CLI)   │             │ (SWE-agent)  │ │
│  └──────────────┘             ├──────────────┤             └──────────────┘ │
│  ┌──────────────┐             │ Exa AI       │                              │
│  │ Google       │             │ (OpenCode)   │                              │
│  │ (Gemini CLI) │             ├──────────────┤                              │
│  └──────────────┘             │ Tavily       │                              │
│  ┌──────────────┐             │ (Qwen Code)  │                              │
│  │ Anthropic    │             ├──────────────┤                              │
│  │ (Claude Code)│             │ Google CSE   │                              │
│  └──────────────┘             │ (Qwen Code)  │                              │
│                               ├──────────────┤                              │
│                               │ DashScope    │                              │
│                               │ (Qwen Code)  │                              │
│                               └──────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 协议与通信方式对比

| 项目 | 协议类型 | 传输格式 | 认证方式 | 超时设置 |
|-----|---------|---------|---------|---------|
| **Codex** | OpenAI Responses API | JSON | API Key | 未明确 |
| **Gemini CLI** | Gemini API (Grounding) | JSON | API Key | 未明确 |
| **Claude Code** | Anthropic Beta API | JSON (流式) | API Key | 未明确 |
| **Kimi CLI** | HTTP REST | JSON | API Key / OAuth | 30s |
| **OpenCode** | MCP JSON-RPC over HTTP | SSE | 无需认证 | 25s |
| **Qwen Code** | HTTP REST | JSON | API Key / OAuth | 未明确 |
| **SWE-agent** | 本地命令 / HTTP (浏览器) | 文本 / JSON | 无需认证 | 未明确 |

### 2.4 结果处理方式对比

| 项目 | 结果格式 | 引用标注 | 来源处理 | 特殊功能 |
|-----|---------|---------|---------|---------|
| **Codex** | 模型返回格式 | 依赖模型 | 模型处理 | 支持 Cached/Live 模式 |
| **Gemini CLI** | GroundingMetadata | 自动插入 [1][2] | 格式化 Sources 列表 | UTF-8 字节位置处理 |
| **Claude Code** | 结构化 + 文本混合 | Prompt 强制要求 | 格式化为 markdown | 流式进度显示 |
| **Kimi CLI** | 结构化 JSON | 手动格式化 | 标题+摘要+URL | Token 保护机制 |
| **OpenCode** | MCP 响应格式 | 依赖服务 | 直接返回 | 实时爬取选项 |
| **Qwen Code** | 多 Provider 统一 | 手动格式化 | 优先 answer 字段 | 智能降级摘要 |
| **SWE-agent** | 文本/截图 | 无 | 本地处理 | 浏览器截图反馈 |

### 2.5 权限与控制对比

| 项目 | 启用方式 | 域名过滤 | 并发限制 | 配置粒度 |
|-----|---------|---------|---------|---------|
| **Codex** | `web_search_mode` 配置 | 不支持 | 未明确 | Profile 级别 |
| **Gemini CLI** | 模型配置别名 | 不支持 | 未明确 | 全局配置 |
| **Claude Code** | 工具权限系统 | `allowed_domains`/`blocked_domains` | 最多 8 次/调用 | 用户权限 |
| **Kimi CLI** | `agent.yaml` 工具列表 | 不支持 | `limit` 参数 (1-20) | Agent 配置 |
| **OpenCode** | `OPENCODE_ENABLE_EXA` 环境变量 | 不支持 | `numResults` 参数 | 环境变量 |
| **Qwen Code** | `settings.json` / 环境变量 | 不支持 | 未明确 | 用户配置 |
| **SWE-agent** | Bundle 按需加载 | 不支持 | 100 行/文件限制 | Bundle 配置 |

---

## 3. 端到端数据流转对比

### 3.1 厂商提供 WebSearch 接口 - 数据流

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                 厂商提供 WebSearch 接口 (以 Claude Code 为例)                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Query                                                                 │
│      │                                                                      │
│      ▼                                                                      │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────┐   │
│  │ Agent Loop      │────▶│ WebSearchTool   │────▶│ Anthropic API       │   │
│  │                 │     │ - buildTool()   │     │ web_search_20250305 │   │
│  └─────────────────┘     │ - call()        │     └─────────────────────┘   │
│                          └─────────────────┘              │                 │
│                                                           │ 内部执行搜索    │
│                                                           ▼                 │
│                          ┌─────────────────┐     ┌─────────────────────┐   │
│                          │ 结果格式化      │◀────│ 流式响应处理        │   │
│                          │ - 解析内容块    │     │ - server_tool_use   │   │
│                          │ - 提取搜索命中  │     │ - web_search_result │   │
│                          │ - 组装 markdown │     │ - text/citation     │   │
│                          └─────────────────┘     └─────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│                          ┌─────────────────┐                                │
│                          │ 返回 LLM        │                                │
│                          │ (强制来源引用)  │                                │
│                          └─────────────────┘                                │
│                                                                             │
│  特点：单 API 调用完成搜索，流式处理，延迟最低                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 自助接入 WebSearch - 数据流

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                   自助接入 WebSearch (以 Qwen Code 为例)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Query                                                                 │
│      │                                                                      │
│      ▼                                                                      │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────┐   │
│  │ Agent Loop      │────▶│ WebSearchTool   │────▶│ Provider 选择       │   │
│  │                 │     │ - 参数校验      │     │ - Tavily            │   │
│  └─────────────────┘     │ - 创建 Provider │     │ - Google            │   │
│                          └─────────────────┘     │ - DashScope         │   │
│                                   │              └─────────────────────┘   │
│                                   │                       │                 │
│                                   ▼                       ▼                 │
│                          ┌─────────────────┐     ┌─────────────────────┐   │
│                          │ 结果格式化      │◀────│ HTTP API 调用       │   │
│                          │ - 优先 answer   │     │ - 认证              │   │
│                          │ - 回退摘要列表  │     │ - 超时控制          │   │
│                          │ - 构建 Sources  │     │ - 错误处理          │   │
│                          └─────────────────┘     └─────────────────────┘   │
│                                   │                                         │
│                                   ▼                                         │
│                          ┌─────────────────┐                                │
│                          │ 返回 LLM        │                                │
│                          │ (提示可用       │                                │
│                          │  web_fetch)     │                                │
│                          └─────────────────┘                                │
│                                                                             │
│  特点：多 Provider 支持，灵活可配置，需要维护多服务集成                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 无 Web 搜索能力 - 数据流

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                      无 Web 搜索能力 (SWE-agent)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Query                                                                 │
│      │                                                                      │
│      ▼                                                                      │
│  ┌─────────────────┐                                                        │
│  │ Agent Decision  │───┬─── 需要代码搜索? ───┬─── 需要网页浏览?            │
│  └─────────────────┘   │                     │                             │
│                        ▼                     ▼                             │
│              ┌─────────────────┐    ┌─────────────────┐                    │
│              │ 本地搜索工具    │    │ 浏览器工具      │                    │
│              │ - find_file     │    │ - open_site     │                    │
│              │ - search_file   │    │ - click_mouse   │                    │
│              │ - search_dir    │    │ - screenshot    │                    │
│              └────────┬────────┘    └────────┬────────┘                    │
│                       │                      │                             │
│                       ▼                      ▼                             │
│              ┌─────────────────┐    ┌─────────────────┐                    │
│              │ grep/find       │    │ Flask Server    │                    │
│              │ 本地文件系统    │    │ + Playwright    │                    │
│              └────────┬────────┘    └────────┬────────┘                    │
│                       │                      │                             │
│                       ▼                      ▼                             │
│              ┌─────────────────┐    ┌─────────────────┐                    │
│              │ 文本结果        │    │ 截图/页面文本   │                    │
│              │ (匹配行/文件)   │    │ (base64 PNG)    │                    │
│              └─────────────────┘    └─────────────────┘                    │
│                       │                      │                             │
│                       └──────────┬───────────┘                             │
│                                  ▼                                         │
│                          ┌─────────────────┐                               │
│                          │ Observation     │                               │
│                          │ (返回给 Agent)  │                               │
│                          └─────────────────┘                               │
│                                                                             │
│  特点：零外部依赖，专注代码库，浏览器工具作为补充                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 关键代码实现对比

### 4.1 工具定义方式对比

| 项目 | 定义方式 | 代码示例 | 特点 |
|-----|---------|---------|------|
| **Codex** | Rust Enum | `ToolSpec::WebSearch { external_web_access }` | 类型安全，编译期检查 |
| **Gemini CLI** | TypeScript Class | `class WebSearchTool extends BaseDeclarativeTool` | 声明式，支持继承 |
| **Claude Code** | TypeScript Factory | `buildTool({ name, call, ... })` | 函数式，灵活配置 |
| **Kimi CLI** | Python Class | `class SearchWeb(CallableTool2[Params])` | Pydantic 参数验证 |
| **OpenCode** | TypeScript Factory | `Tool.define("websearch", async () => {...})` | Zod 参数校验 |
| **Qwen Code** | TypeScript Class | `class WebSearchToolInvocation` | 策略模式支持 |
| **SWE-agent** | YAML Config | `config.yaml` 定义签名和文档 | 配置驱动，简单直观 |

### 4.2 搜索调用核心代码对比

**Codex (Rust)**:
```rust
// 工具注册时根据配置决定
match config.web_search_mode {
    Some(WebSearchMode::Cached) => {
        builder.push_spec(ToolSpec::WebSearch {
            external_web_access: Some(false),
        });
    }
    Some(WebSearchMode::Live) => {
        builder.push_spec(ToolSpec::WebSearch {
            external_web_access: Some(true),
        });
    }
    // ...
}
```

**Gemini CLI (TypeScript)**:
```typescript
// 使用模型配置别名启用 Grounding
const response = await geminiClient.generateContent(
  { model: 'web-search' },  // 启用 googleSearch 工具
  [{ role: 'user', parts: [{ text: this.params.query }] }],
  signal,
);
// 解析 groundingMetadata 并插入引用标记
```

**Claude Code (TypeScript)**:
```typescript
// 构建 Beta WebSearch Schema
function makeToolSchema(input: Input): BetaWebSearchTool20250305 {
  return {
    type: 'web_search_20250305',
    name: 'web_search',
    allowed_domains: input.allowed_domains,
    blocked_domains: input.blocked_domains,
    max_uses: 8, // 硬编码限制
  };
}
// 流式处理 server_tool_use 和 web_search_tool_result
```

**Kimi CLI (Python)**:
```python
# 调用 Moonshot 搜索服务
async with new_client_session() as session,
    session.post(
        self._base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Msh-Tool-Call-Id": tool_call.id,  # 链路追踪
        },
        json={
            "text_query": params.query,
            "limit": params.limit,
            "enable_page_crawling": params.include_content,
            "timeout_seconds": 30,
        },
    ) as response:
```

**OpenCode (TypeScript)**:
```typescript
// MCP JSON-RPC 请求
const searchRequest: McpSearchRequest = {
  jsonrpc: "2.0",
  id: 1,
  method: "tools/call",
  params: {
    name: "web_search_exa",
    arguments: {
      query: params.query,
      type: params.type || "auto",
      numResults: params.numResults || 8,
      livecrawl: params.livecrawl || "fallback",
    },
  },
};
// SSE 响应解析
```

**Qwen Code (TypeScript)**:
```typescript
// Provider 策略模式
private createProvider(config: WebSearchProviderConfig): WebSearchProvider {
  switch (config.type) {
    case 'tavily': return new TavilyProvider(config);
    case 'google': return new GoogleProvider(config);
    case 'dashscope': return new DashScopeProvider(config);
  }
}
// 结果智能格式化：优先 answer，回退摘要
```

**SWE-agent (Bash/Python)**:
```bash
# find_file 工具 (Bash)
local matches=$(find "$dir" -type f -name "$file_name")
# search_file 工具 (Bash)
local matches=$(grep -nH -- "$search_term" "$file")
# search_dir 工具 (Bash)
local matches=$(find "$dir" -type f ! -path '*/.*' -exec grep -nIH -- "$search_term" {} +)
```

---

## 5. 设计意图与 Trade-off

### 5.1 架构模式对比分析

| 维度 | 厂商提供 WebSearch 接口 | 自助接入 WebSearch | 无 Web 搜索能力 |
|-----|------------------------|-------------------|----------------|
| **维护成本** | 最低（零维护） | 中等（需管理多服务） | 最低（无外部依赖） |
| **部署复杂度** | 低（仅 API Key） | 高（多服务配置） | 低（内置工具） |
| **灵活性** | 低（供应商锁定） | 高（可切换 Provider） | 中（可扩展脚本） |
| **延迟** | 最低（单次调用） | 中等（多跳网络） | 低（本地执行） |
| **成本可控性** | 低（绑定模型计费） | 高（独立计费） | 最高（零成本） |
| **离线可用性** | 否 | 否 | 是 |
| **结果质量** | 高（模型优化） | 依赖 Provider | 中（纯文本匹配） |

### 5.2 各项目的设计取舍

**Codex - 简洁至上**
```
选择：OpenAI API 原生 web_search 工具（厂商提供 WebSearch 接口）
放弃：自助接入搜索服务的灵活性
原因：
  - 零维护成本，专注核心 Agent 能力
  - 与沙箱策略联动（Cached/Live 模式对应不同安全级别）
  - OpenAI 搜索基础设施质量有保障
```

**Gemini CLI - 生态整合**
```
选择：Gemini API Grounding 工具（厂商提供 WebSearch 接口）
放弃：自助接入搜索 Provider 选择
原因：
  - 充分利用 Google 搜索生态
  - GroundingMetadata 提供结构化引用信息
  - 自动引用标注，无需手动处理
```

**Claude Code - 原生深度集成**
```
选择：Anthropic Beta API 原生搜索（厂商提供 WebSearch 接口）
放弃：自助接入 MCP 扩展性
原因：
  - 最低延迟，单次 API 调用完成
  - 流式响应支持实时进度反馈
  - Prompt 强制来源引用，质量保证
```

**Kimi CLI - 服务优先+降级**
```
选择：自助接入 Moonshot 搜索服务 + 本地降级
放弃：厂商提供 WebSearch 接口的零维护优势
原因：
  - 服务可用时获得更好解析质量
  - 服务不可用时自动降级，确保可用性
  - 支持 OAuth 企业认证场景
```

**OpenCode - MCP 标准化**
```
选择：自助接入 Exa AI MCP 服务
放弃：单一 Provider 的简单性
原因：
  - 符合 Anthropic MCP 协议标准
  - SSE 流式响应预留扩展空间
  - 代码搜索独立工具，针对编程优化
```

**Qwen Code - 多 Provider 策略**
```
选择：自助接入 Tavily/Google/DashScope 多 Provider
放弃：单一 Provider 的深度优化
原因：
  - 国际用户：Tavily/Google
  - 国内用户：DashScope（夸克搜索）
  - OAuth 用户自动启用，零配置体验
```

**SWE-agent - 无 Web 搜索能力**
```
选择：本地代码搜索 + 浏览器自动化（无 Web 搜索）
放弃：搜索引擎 API 的广泛信息获取能力
原因：
  - 专注软件工程任务，代码库内搜索为主
  - 零外部依赖，离线可用
  - 零 API 成本
```

### 5.3 决策建议：什么场景选择什么模式

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                          架构模式选择决策树                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 是否需要获取互联网最新信息？                                            │
│     ├── 否 ──▶ SWE-agent 模式（无 Web 搜索能力）                                   │
│     │           适用：纯代码库任务、离线环境、零成本需求                     │
│     │                                                                        │
│     └── 是 ──▶ 2. 是否已绑定特定模型提供商？                                │
│                 ├── 是 ──▶ 厂商提供 WebSearch 接口                                       │
│                 │   ├── OpenAI ──▶ Codex 模式                               │
│                 │   ├── Google ──▶ Gemini CLI 模式                          │
│                 │   └── Anthropic ──▶ Claude Code 模式                      │
│                 │   适用：快速部署、低延迟、零维护需求                       │
│                 │                                                            │
│                 └── 否 ──▶ 3. 是否需要多地区/多 Provider 支持？             │
│                             ├── 是 ──▶ 自助接入 WebSearch                           │
│                             │   ├── 需 MCP 生态 ──▶ OpenCode 模式           │
│                             │   ├── 需国内服务 ──▶ Qwen Code 模式           │
│                             │   └── 需服务降级 ──▶ Kimi CLI 模式            │
│                             │   适用：企业定制、多地区部署、灵活可控         │
│                             │                                                │
│                             └── 否 ──▶ 根据团队技术栈选择                   │
│                                         Python ──▶ Kimi CLI 参考            │
│                                         TypeScript ──▶ Gemini CLI 参考      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 边界情况与错误处理对比

### 6.1 常见错误处理策略

| 项目 | 服务不可用 | 搜索无结果 | API 错误 | 超时处理 |
|-----|-----------|-----------|---------|---------|
| **Codex** | 配置级禁用 | 模型处理 | 模型处理 | 依赖 SDK |
| **Gemini CLI** | 抛出错误 | 返回提示文本 | 抛出错误 | 依赖 SDK |
| **Claude Code** | 工具禁用 | 返回空结果 | 错误日志+继续 | 依赖 SDK |
| **Kimi CLI** | `SkipThisTool` 跳过 | 返回提示 | 返回错误信息 | 30s 超时 |
| **OpenCode** | 工具过滤不加载 | 返回 "No results" | 抛出错误 | 25s 超时 |
| **Qwen Code** | Provider 回退 | 返回提示 | Provider 切换 | 依赖 Provider |
| **SWE-agent** | N/A | "No matches found" | 命令错误码 | 未明确 |

### 6.2 降级机制对比

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                            降级机制对比                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Kimi CLI (双层架构)                                                         │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │ Moonshot     │────▶│ 失败?        │────▶│ 本地 HTTP    │                │
│  │ 服务         │     │              │     │ 抓取         │                │
│  └──────────────┘     └──────────────┘     └──────────────┘                │
│                                                                             │
│  Qwen Code (多 Provider)                                                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │ Tavily       │────▶│ 失败?        │────▶│ Google       │                │
│  │              │     │              │     │              │                │
│  └──────────────┘     └──────────────┘     └───────┬──────┘                │
│                                                    │                        │
│                                              ┌─────┴─────┐                  │
│                                              ▼           ▼                  │
│                                        ┌──────────┐  ┌──────────┐           │
│                                        │ DashScope│  │ 失败     │           │
│                                        └──────────┘  └──────────┘           │
│                                                                             │
│  OpenCode (功能开关)                                                         │
│  ┌──────────────┐     ┌──────────────┐                                      │
│  │ OPENCODE_    │────▶│ 未启用?      │────▶│ 工具不加载   │                │
│  │ ENABLE_EXA   │     │              │     │ (静默跳过)   │                │
│  └──────────────┘     └──────────────┘     └──────────────┘                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. 关键代码索引

### 7.1 各项目核心文件位置

| 项目 | 核心实现文件 | 配置/注册文件 | 类型定义文件 |
|-----|-------------|--------------|-------------|
| **Codex** | `codex/codex-rs/core/src/tools/spec.rs` | `codex/codex-rs/core/src/config/mod.rs` | `codex/codex-rs/protocol/src/config_types.rs` |
| **Gemini CLI** | `packages/core/src/tools/web-search.ts` | `packages/core/src/config/defaultModelConfigs.ts` | `packages/core/src/tools/definitions/base-declarations.ts` |
| **Claude Code** | `claude-code/src/tools/WebSearchTool/WebSearchTool.ts` | `claude-code/src/tools.ts` | `claude-code/src/constants/betas.ts` |
| **Kimi CLI** | `kimi-cli/src/kimi_cli/tools/web/search.py` | `kimi-cli/src/kimi_cli/agents/default/agent.yaml` | `kimi-cli/src/kimi_cli/config.py` |
| **OpenCode** | `packages/opencode/src/tool/websearch.ts` | `packages/opencode/src/tool/registry.ts` | `packages/opencode/src/flag/flag.ts` |
| **Qwen Code** | `packages/core/src/tools/web-search/index.ts` | `packages/cli/src/config/webSearch.ts` | `packages/core/src/tools/web-search/types.ts` |
| **SWE-agent** | `tools/search/bin/*` | `tools/search/config.yaml` | `sweagent/tools/bundle.py` |

### 7.2 跨项目通用概念映射

| 概念 | Codex | Gemini CLI | Claude Code | Kimi CLI | OpenCode | Qwen Code | SWE-agent |
|-----|-------|-----------|-------------|----------|----------|-----------|-----------|
| 工具定义 | `ToolSpec` | `DeclarativeTool` | `buildTool` | `CallableTool2` | `Tool.define` | `WebSearchTool` | `config.yaml` |
| 搜索工具 | `WebSearch` | `WebSearchTool` | `WebSearchTool` | `SearchWeb` | `websearch` | `web_search` | `search_dir` |
| 获取工具 | N/A | `WebFetchTool` | N/A | `FetchURL` | `webfetch` | `web_fetch` | `web_browser` |
| 配置方式 | `web_search_mode` | 模型别名 | 权限系统 | `agent.yaml` | 环境变量 | `settings.json` | Bundle 加载 |

---

## 8. 延伸阅读

### 8.1 各项目详细文档

- [Codex WebSearch 实现](../codex/questions/codex-websearch-implementation.md)
- [Gemini CLI WebSearch 实现](../gemini-cli/questions/gemini-cli-websearch-implementation.md)
- [Kimi CLI WebSearch 实现](../kimi-cli/questions/kimi-cli-websearch-implementation.md)
- [OpenCode WebSearch 实现](../opencode/questions/opencode-websearch-implementation.md)
- [SWE-agent WebSearch 实现](../swe-agent/questions/swe-agent-websearch-implementation.md)
- [Qwen Code WebSearch 实现](../qwen-code/questions/qwen-code-websearch-implementation.md)
- [Claude Code WebSearch 实现](../claude-code/questions/claude-websearch-implementation.md)

### 8.2 相关跨项目对比文档

- [MCP 集成对比](../06-comm-mcp-integration.md)
- [工具系统对比](../comm-tool-system.md)
- [Agent Loop 对比](../04-comm-agent-loop.md)

### 8.3 外部参考

- [OpenAI Web Search 工具文档](https://platform.openai.com/docs/guides/tools-web-search)
- [Gemini API Grounding 文档](https://ai.google.dev/docs/grounding)
- [Anthropic Beta Features](https://docs.anthropic.com/claude/docs/beta-features)
- [Model Context Protocol 规范](https://modelcontextprotocol.io/)
- [Tavily AI 搜索 API](https://docs.tavily.com/)
- [Exa AI 文档](https://docs.exa.ai/)

---

*文档基于以下项目版本分析*
| 项目 | 版本/日期 |
|-----|----------|
| Codex | 2026-02-08 |
| Gemini CLI | 2026-02-08 |
| Kimi CLI | 2025-02-15 |
| OpenCode | 2026-02-08 |
| SWE-agent | 2026-02-08 |
| Qwen Code | 2025-02-23 |
| Claude Code | 2026-04-12 |

*最后更新：2026-04-12*
