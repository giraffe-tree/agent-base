# 工具系统对比

## 1. 概念定义

**工具系统（Tool System）** 是 Agent 与环境交互的桥梁，负责定义、注册、执行和管理 Agent 可调用的功能。工具让 Agent 能够操作文件系统、执行命令、访问网络等。

### 核心职责

- **工具定义**：描述工具的功能、参数、返回类型
- **工具注册**：将工具添加到可用工具集
- **工具解析**：从模型输出中提取工具调用
- **工具执行**：实际执行工具并返回结果
- **权限控制**：控制工具的可访问性和执行权限

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 使用 **Bundle + Command + ParseFunction** 三层架构。工具通过 YAML 配置定义，支持多种解析器适配不同模型。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  ToolConfig (配置层)                                      │
│  ├── bundles: list[Bundle]      工具包列表              │
│  ├── parse_function             输出解析器              │
│  └── filter                     命令过滤器              │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Bundle (工具包层)                                        │
│  ├── config.yaml                工具定义                │
│  ├── commands: list[Command]    命令列表                │
│  └── install.sh (可选)          安装脚本                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Command (命令层)                                         │
│  ├── name                       命令名                  │
│  ├── signature                  调用签名                │
│  ├── arguments: list[Argument]  参数定义                │
│  └── get_function_calling_tool() OpenAI 格式转换       │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ParseFunction (解析层)                                   │
│  ├── FunctionCallingParser      函数调用格式            │
│  ├── ThoughtActionParser        思考-行动格式           │
│  ├── JsonParser                 JSON 格式               │
│  └── ...                        更多解析器              │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| ToolConfig | `sweagent/tools/tools.py` | 1 | 工具配置类 |
| Bundle | `sweagent/tools/bundle.py` | 1 | 工具包类 |
| Command | `sweagent/tools/commands.py` | 1 | 命令定义 |
| ToolHandler | `sweagent/tools/tools.py` | 400 | 执行管理 |

**工具定义示例**

```yaml
# tools/windowed/config.yaml
tools:
  open:
    docstring: Open a file in the windowed editor
    signature: "open <path>"
    arguments:
      - name: path
        type: string
        required: true
```

### 2.2 Codex

**实现概述**

Codex 使用 **Trait-based Handler + Registry + Router** 架构。工具通过实现 `ToolHandler` trait 定义，使用 `ToolRegistry` 管理注册。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  ToolsConfig (配置层)                                     │
│  ├── shell_type                 Shell 执行方式          │
│  ├── apply_patch_tool_type      补丁工具格式            │
│  └── experimental_supported_tools 实验性工具            │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ToolRegistry (注册层)                                    │
│  ├── handlers: HashMap          Handler 映射            │
│  ├── register_handler()         注册处理器              │
│  └── dispatch()                 分发执行                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ToolHandler Trait (定义层)                               │
│  ├── kind() -> ToolKind          工具类型               │
│  ├── is_mutating()              是否变异操作            │
│  └── handle()                   执行逻辑                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ToolRouter (路由层)                                      │
│  ├── from_config()              从配置创建              │
│  ├── build_tool_call()          解析工具调用            │
│  └── dispatch_tool_call()       分发执行                │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| ToolHandler | `codex-rs/core/src/tools/registry.rs` | 50 | Handler trait |
| ToolRegistry | `codex-rs/core/src/tools/registry.rs` | 100 | 注册表 |
| ToolRouter | `codex-rs/core/src/tools/router.rs` | 50 | 路由器 |
| ToolPayload | `codex-rs/core/src/tools/context.rs` | 50 | 调用负载 |

**Handler 实现示例**

```rust
#[async_trait]
impl ToolHandler for BashToolHandler {
    fn kind(&self) -> ToolKind {
        ToolKind::Function
    }

    async fn is_mutating(&self,
        invocation: &ToolInvocation
    ) -> bool {
        // 判断是否为变异操作
        true
    }

    async fn handle(
        &self,
        invocation: ToolInvocation
    ) -> Result<ToolOutput, FunctionCallError> {
        // 执行 bash 命令
        let output = execute_bash(invocation).await?;
        Ok(ToolOutput::Function { body: output, success: true })
    }
}
```

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 使用 **声明式基类 + Registry + 三层工具来源** 架构。工具通过继承 `DeclarativeTool` 定义，支持 Built-in、Discovered、MCP 三类工具。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  DeclarativeTool (定义层)                                 │
│  ├── name                       API 名称                │
│  ├── displayName                展示名称                │
│  ├── kind: Kind                 工具分类                │
│  ├── parameterSchema            JSON Schema             │
│  ├── validateToolParams()       参数验证                │
│  └── build() -> ToolInvocation   构建调用               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ToolRegistry (注册层)                                    │
│  ├── allKnownTools: Map         工具映射                │
│  ├── registerTool()             注册工具                │
│  ├── discoverAllTools()         发现工具                │
│  └── getFunctionDeclarations()  生成 LLM Schema         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  三层工具来源                                             │
│  ├── Built-in (0)               内置工具                │
│  ├── Discovered (1)             项目发现工具            │
│  └── MCP (2)                    MCP 工具                │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| DeclarativeTool | `packages/core/src/tools/tools.ts` | 80 | 声明式工具基类 |
| ToolRegistry | `packages/core/src/tools/registry.ts` | 50 | 工具注册表 |
| Kind | `packages/core/src/tools/kind.ts` | 1 | 工具分类枚举 |

**工具定义示例**

```typescript
class ReadFileTool extends DeclarativeTool<ReadFileParams, ReadFileResult> {
    name = 'read_file';
    displayName = 'Read File';
    kind = Kind.Read;

    parameterSchema = {
        type: 'object',
        properties: {
            path: { type: 'string' }
        },
        required: ['path']
    };

    protected createInvocation(params) {
        return new ReadFileInvocation(params);
    }
}
```

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 使用 **模块化功能域 + 统一参数提取** 架构。工具按功能分组（file/、shell/、web/ 等），通过 `extract_key_argument` 统一提取关键参数用于 UI 展示。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  工具模块 (按功能域组织)                                    │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │ file/       │ │ shell/      │ │ web/        │       │
│  │ • read      │ │ • command   │ │ • fetch     │       │
│  │ • write     │ │             │ │ • search    │       │
│  │ • replace   │ │             │ │             │       │
│  └─────────────┘ └─────────────┘ └─────────────┘       │
│  ┌─────────────┐ ┌─────────────┐                        │
│  │ multiagent/ │ │ think/      │                        │
│  │ • task      │ │ • think     │                        │
│  │ • create    │ │             │                        │
│  └─────────────┘ └─────────────┘                        │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  extract_key_argument()                                   │
│  ├── 解析 JSON 参数                                       │
│  ├── 按工具类型提取关键字段                               │
│  └── 截断显示 (shorten_middle)                            │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| tools init | `kimi-cli/src/kimi_cli/tools/__init__.py` | 1 | 工具初始化 |
| file tools | `kimi-cli/src/kimi_cli/tools/file/` | - | 文件工具 |
| shell tools | `kimi-cli/src/kimi_cli/tools/shell/` | - | Shell 工具 |
| web tools | `kimi-cli/src/kimi_cli/tools/web/` | - | Web 工具 |

**工具定义示例**

```python
class Shell(Tool):
    """Execute shell commands."""

    async def execute(self, command: str) -> str:
        # 执行命令
        result = await subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout
```

### 2.5 OpenCode

**实现概述**

OpenCode 使用 **Zod Schema + 工厂函数 + 动态注册** 架构。工具通过 `Tool.define` 使用 Zod 定义参数 Schema，支持从文件系统和插件动态加载。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Tool.define() (定义层)                                   │
│  ├── id                         工具 ID                 │
│  ├── parameters: z.ZodType      参数 Schema             │
│  ├── description                功能描述                │
│  ├── execute(args, ctx)         执行逻辑                │
│  │   ├── ctx.ask()              权限请求                │
│  │   └── ctx.metadata()         元数据更新              │
│  └── 自动截断输出                                         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ToolRegistry (注册层)                                    │
│  ├── state()                    初始化状态              │
│  │   ├── 加载自定义工具         ~/tools/*.{js,ts}       │
│  │   └── 加载插件工具                                     │
│  ├── register(tool)             动态注册                │
│  └── tools(model, agent)        获取过滤后的工具        │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Tool.define | `packages/opencode/src/tool/tool.ts` | 75 | 工厂函数 |
| ToolRegistry | `packages/opencode/src/tool/registry.ts` | 1 | 注册表 |
| BashTool | `packages/opencode/src/tool/bash.ts` | 1 | Bash 工具示例 |

**工具定义示例**

```typescript
export const BashTool = Tool.define("bash", async () => {
    return {
        description: "Execute shell commands",
        parameters: z.object({
            command: z.string().describe("The command to execute"),
            timeout: z.number().optional(),
            workdir: z.string().optional(),
        }),

        async execute(params, ctx) {
            // 1. 解析命令，识别路径
            const tree = await parser().then((p) => p.parse(params.command));

            // 2. 请求权限
            await ctx.ask({
                permission: "bash",
                patterns: [params.command],
            });

            // 3. 执行命令
            const result = await exec(params.command);

            return {
                title: params.description,
                metadata: {},
                output: result.stdout
            };
        }
    };
});
```

---

## 3. 相同点总结

### 3.1 通用工具类别

所有 Agent 都提供以下核心工具类别：

| 类别 | 典型工具 | 用途 |
|------|----------|------|
| 文件操作 | read、write、edit、glob、grep | 代码读写 |
| Shell 执行 | bash、command | 命令执行 |
| 代码搜索 | search、codesearch | 语义/文本搜索 |
| 网络访问 | fetch、websearch | 获取外部信息 |
| 任务完成 | submit、done | 标记任务完成 |

### 3.2 工具调用流程

```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   LLM       │────▶│ 解析工具调用 │────▶│ 验证参数   │
│  输出       │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                                               ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  返回结果   │◄────│ 格式化输出 │◄────│ 执行工具   │
│  给 LLM     │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 3.3 权限控制机制

| Agent | 控制粒度 | 确认方式 |
|-------|----------|----------|
| SWE-agent | 命令过滤 | 预定义 blocklist |
| Codex | 变异检测 | tool_call_gate |
| Gemini CLI | Kind 分类 | 策略引擎 |
| Kimi CLI | 简单确认 | 用户确认 |
| OpenCode | 细粒度权限 | PermissionNext |

---

## 4. 不同点对比

### 4.1 定义方式对比

| Agent | 定义方式 | 参数验证 | 类型安全 |
|-------|----------|----------|----------|
| SWE-agent | YAML + Python | Pydantic | 中 |
| Codex | Rust Trait | 编译时 | 高 |
| Gemini CLI | TypeScript 类 | JSON Schema | 高 |
| Kimi CLI | Python 类 | Pydantic | 中 |
| OpenCode | Zod Schema | 运行时 | 高 |

### 4.2 扩展性对比

| Agent | 扩展方式 | 动态加载 | MCP 支持 |
|-------|----------|----------|----------|
| SWE-agent | Bundle 配置 | 否 | 否 |
| Codex | 注册 Handler | 是 | 是 |
| Gemini CLI | 内置 + 发现 | 是 | 是 |
| Kimi CLI | 模块化 | 否 | ACP |
| OpenCode | 文件 + 插件 | 是 | 是 |

### 4.3 工具分类系统

| Agent | 分类方式 | 分类粒度 | 用途 |
|-------|----------|----------|------|
| SWE-agent | 无 | - | - |
| Codex | is_mutating | 二值 | 并发控制 |
| Gemini CLI | Kind 枚举 | 多值 | 权限管理 |
| Kimi CLI | 目录分组 | 功能域 | 代码组织 |
| OpenCode | 无显式分类 | - | - |

### 4.4 参数解析对比

| Agent | 解析方式 | 支持格式 | 特点 |
|-------|----------|----------|------|
| SWE-agent | ParseFunction | function_calling, thought_action, JSON | 多解析器 |
| Codex | 内置序列化 | JSON | 统一 |
| Gemini CLI | JSON Schema | JSON | 标准 |
| Kimi CLI | Pydantic | JSON | Pythonic |
| OpenCode | Zod | JSON | 类型安全 |

### 4.5 自定义工具支持

| Agent | 自定义方式 | 位置 | 热加载 |
|-------|------------|------|--------|
| SWE-agent | Bundle | 项目目录 | 否 |
| Codex | 无 | - | - |
| Gemini CLI | Discovery | 项目目录 | 是 |
| Kimi CLI | 无 | - | - |
| OpenCode | 文件 + 插件 | ~/.opencode/tools | 是 |

---

## 5. 源码索引

### 5.1 工具定义

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/tools/commands.py` | 1 | Command 类 |
| Codex | `codex-rs/core/src/tools/registry.rs` | 50 | ToolHandler trait |
| Gemini CLI | `packages/core/src/tools/tools.ts` | 80 | DeclarativeTool |
| Kimi CLI | `kimi-cli/src/kimi_cli/tools/__init__.py` | 1 | 工具初始化 |
| OpenCode | `packages/opencode/src/tool/tool.ts` | 75 | Tool.define |

### 5.2 工具注册

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/tools/tools.py` | 280 | commands 属性 |
| Codex | `codex-rs/core/src/tools/registry.rs` | 100 | ToolRegistry |
| Gemini CLI | `packages/core/src/tools/registry.ts` | 50 | ToolRegistry |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/kosong.py` | 50 | toolset 初始化 |
| OpenCode | `packages/opencode/src/tool/registry.ts` | 1 | ToolRegistry |

### 5.3 工具执行

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/environment/swe_env.py` | 100 | communicate |
| Codex | `codex-rs/core/src/tools/registry.rs` | 200 | dispatch |
| Gemini CLI | `packages/core/src/scheduler/scheduler.ts` | 100 | schedule |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/kosong.py` | 200 | step |
| OpenCode | `packages/opencode/src/session/processor.ts` | 200 | tool-call event |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 学术研究 | SWE-agent | Bundle 配置，易于复现 |
| 企业级应用 | Codex | Rust 类型安全，高性能 |
| IDE 集成 | Gemini CLI | Kind 分类，权限精细 |
| 快速原型 | Kimi CLI | Python，易于扩展 |
| 自定义工具 | OpenCode | 动态加载，插件系统 |
