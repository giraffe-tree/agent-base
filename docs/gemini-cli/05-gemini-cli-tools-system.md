# Tool System（gemini-cli）

本文基于 `./gemini-cli/packages/core/src/tools` 源码，解释 Gemini CLI 的工具系统架构——从声明式工具定义、注册发现到 MCP 集成的完整链路。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────────┐
│  工具定义层：声明式工具基类                                           │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  DeclarativeTool<TParams, TResult>                              ││
│  │  ├── name: string              (API 名称)                       ││
│  │  ├── displayName: string       (展示名称)                       ││
│  │  ├── description: string       (功能描述)                       ││
│  │  ├── kind: Kind                (工具分类)                       ││
│  │  ├── parameterSchema: JSONSchema                              ││
│  │  ├── validateToolParams()      → ToolInvocation                ││
│  │  └── buildAndExecute()         便捷执行方法                     ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  工具注册层：ToolRegistry 管理工具生命周期                             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolRegistry                                                   ││
│  │  ├── registerTool()            注册工具                         ││
│  │  ├── discoverAllTools()        发现外部工具                     ││
│  │  │   ├── 项目命令发现 (DiscoveredTool)                         ││
│  │  │   └── MCP 工具发现 (DiscoveredMCPTool)                      ││
│  │  ├── getActiveTools()          获取可用工具                     ││
│  │  └── getFunctionDeclarations() 生成 LLM Schema                  ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP 管理层：McpClientManager 管理外部工具服务                         │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  McpClientManager                                               ││
│  │  ├── startConfiguredMcpServers()  启动配置的服务器               ││
│  │  ├── startExtension()            加载扩展的 MCP 服务            ││
│  │  ├── maybeDiscoverMcpServer()    发现并注册工具                 ││
│  │  └── stop()                      清理连接                       ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念与设计哲学

### 2.1 一句话定义

Gemini CLI 的工具系统是「**声明式定义 + 分离式执行 + 三层工具来源**」的架构：工具通过 `DeclarativeTool` 声明式定义，执行拆分为 `validate` → `build` → `execute` 三阶段，支持 Built-in、Discovered、MCP 三类工具来源。

### 2.2 工具分类（Kind）

```typescript
enum Kind {
  Read = 'read',           // 读取操作
  Edit = 'edit',           // 编辑操作
  Delete = 'delete',       // 删除操作
  Move = 'move',           // 移动操作
  Search = 'search',       // 搜索操作
  Execute = 'execute',     // 执行操作
  Think = 'think',         // 思考
  Fetch = 'fetch',         // 网络获取
  Communicate = 'communicate', // 通信
  Plan = 'plan',           // 规划
  Other = 'other',         // 其他
}

const MUTATOR_KINDS = [Kind.Edit, Kind.Delete, Kind.Move, Kind.Execute];
```

### 2.3 设计特点

| 特性 | 实现方式 | 优势 |
|------|---------|------|
| 声明式定义 | `DeclarativeTool` 基类 | 类型安全，易于扩展 |
| 分离式执行 | validate → build → execute | 支持预执行验证和确认 |
| 三层工具来源 | Built-in / Discovered / MCP | 灵活的工具扩展能力 |
| 权限控制 | Kind-based 分类 | 精细化权限管理 |
| 确认流程 | MessageBus 事件驱动 | 解耦 UI 与核心逻辑 |

---

## 3. 工具定义架构

### 3.1 核心接口

```typescript
// packages/core/src/tools/tools.ts
interface ToolBuilder<TParams extends object, TResult extends ToolResult> {
  name: string;                    // API 名称
  displayName: string;             // 展示名称
  description: string;             // 功能描述
  kind: Kind;                      // 工具分类
  getSchema(modelId?: string): FunctionDeclaration;
  build(params: TParams): ToolInvocation<TParams, TResult>;
}

interface ToolInvocation<TParams, TResult> {
  params: TParams;
  getDescription(): string;        // 获取操作描述
  toolLocations(): ToolLocation[]; // 获取影响路径
  shouldConfirmExecute(signal: AbortSignal): Promise<...>;
  execute(signal: AbortSignal, updateOutput?): Promise<TResult>;
}
```

### 3.2 声明式基类

```typescript
abstract class BaseDeclarativeTool<TParams, TResult> extends DeclarativeTool<TParams, TResult> {
  // 1. 验证参数
  validateToolParams(params: TParams): string | null {
    const errors = SchemaValidator.validate(this.schema.parametersJsonSchema, params);
    return errors || this.validateToolParamValues(params);
  }

  // 2. 构建调用对象
  build(params: TParams): ToolInvocation<TParams, TResult> {
    const validationError = this.validateToolParams(params);
    if (validationError) throw new Error(validationError);
    return this.createInvocation(params, this.messageBus, this.name, this.displayName);
  }

  // 3. 子类实现具体调用
  protected abstract createInvocation(...): ToolInvocation<TParams, TResult>;
}
```

### 3.3 执行结果格式

```typescript
interface ToolResult {
  llmContent: PartListUnion;       // LLM 历史内容
  returnDisplay: ToolResultDisplay; // 用户展示内容
  error?: { message: string; type?: ToolErrorType };
  data?: Record<string, unknown>;
}
```

---

## 4. ToolRegistry：工具注册与发现

### 4.1 核心结构

```typescript
class ToolRegistry {
  private allKnownTools: Map<string, AnyDeclarativeTool> = new Map();
  private config: Config;
  private messageBus: MessageBus;

  // 工具排序：Built-in (0) < Discovered (1) < MCP (2)
  sortTools(): void {
    const getPriority = (tool: AnyDeclarativeTool): number => {
      if (tool instanceof DiscoveredMCPTool) return 2;
      if (tool instanceof DiscoveredTool) return 1;
      return 0;
    };
    // ...
  }
}
```

### 4.2 工具来源优先级

| 优先级 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| 0 | Built-in | 内置工具 | shell, read_file, write_file |
| 1 | Discovered | 项目发现工具 | 项目自定义工具 |
| 2 | MCP | 外部 MCP 工具 | 来自 MCP Server 的工具 |

### 4.3 发现流程

```
 discoverAllTools()
       │
       ├──► removeDiscoveredTools()    // 清理旧发现工具
       │
       ├──► discoverAndRegisterToolsFromCommand()
       │       │
       │       ├──► 执行 discovery command
       │       ├──► 解析 JSON 输出
       │       └──► 注册为 DiscoveredTool
       │
       └──► MCP 工具发现 (通过 McpClientManager)
```

---

## 5. MCP 集成：McpClientManager

### 5.1 架构位置

```
┌─────────────────────────────────────────────────────────┐
│                    Gemini CLI Core                      │
│  ┌─────────────────┐                                    │
│  │   ToolRegistry  │◄── 注册并管理所有工具              │
│  └────────┬────────┘                                    │
│           │                                             │
│           │ getFunctionDeclarations()                   │
│           ▼                                             │
│  ┌─────────────────┐     ┌─────────────────────────────┐│
│  │  McpClientManager│────►│  MCP Server 1 (stdio)       ││
│  │                 │     │  MCP Server 2 (http)        ││
│  │ • startConfigured │    │  MCP Server 3 (sse)         ││
│  │ • startExtension  │    └─────────────────────────────┘│
│  │ • stop()          │                                   │
│  └─────────────────┘                                     │
└─────────────────────────────────────────────────────────┘
```

### 5.2 生命周期管理

```typescript
class McpClientManager {
  private clients: Map<string, McpClient> = new Map();
  private allServerConfigs: Map<string, MCPServerConfig> = new Map();

  // 启动配置的 MCP 服务器
  async startConfiguredMcpServers(): Promise<void> {
    if (!this.cliConfig.isTrustedFolder()) return;
    // ...
  }

  // 加载扩展的 MCP 服务
  async startExtension(extension: GeminiCLIExtension): Promise<void> {
    for (const [name, config] of Object.entries(extension.mcpServers ?? {})) {
      await this.maybeDiscoverMcpServer(name, { ...config, extension });
    }
  }

  // 停止所有连接
  async stop(): Promise<void> {
    const disconnectionPromises = Array.from(this.clients.entries()).map(
      async ([name, client]) => { /* ... */ }
    );
    await Promise.all(disconnectionPromises);
  }
}
```

### 5.3 发现状态机

```typescript
enum MCPDiscoveryState {
  NOT_STARTED = 'not_started',
  IN_PROGRESS = 'in_progress',
  COMPLETED = 'completed',
}
```

---

## 6. 工具确认流程

### 6.1 确认决策流程

```text
 shouldConfirmExecute()
         │
         ▼
 ┌──────────────────┐
 │ getMessageBusDecision() │
 └────────┬─────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
 ┌──────┐   ┌──────┐   ┌──────────┐
 │ALLOW │   │DENY  │   │ ASK_USER │
 └──┬───┘   └──┬───┘   └────┬─────┘
    │          │            │
    ▼          ▼            ▼
 直接执行   抛出拒绝    getConfirmationDetails()
                          │
                          ▼
                    显示确认 UI
                          │
                    ┌─────┴─────┐
                    ▼           ▼
                 允许         拒绝
```

### 6.2 确认结果类型

```typescript
enum ToolConfirmationOutcome {
  ProceedOnce = 'proceed_once',               // 仅本次允许
  ProceedAlways = 'proceed_always',           // 总是允许（会话）
  ProceedAlwaysAndSave = 'proceed_always_and_save', // 总是允许并保存
  ProceedAlwaysServer = 'proceed_always_server',    // 允许此服务器所有工具
  ProceedAlwaysTool = 'proceed_always_tool',        // 允许此工具
  ModifyWithEditor = 'modify_with_editor',    // 使用编辑器修改
  Cancel = 'cancel',                          // 取消
}
```

---

## 7. 工具调用完整流程

### 7.1 主流程图

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        工具调用主流程                                │
└─────────────────────────────────────────────────────────────────────┘

  LLM 生成工具调用
        │
        ▼
┌───────────────────┐
│ ToolRegistry.getTool(name) │
│  • 精确匹配       │
│  • 检查别名 (TOOL_LEGACY_ALIASES) │
│  • MCP 全限定名匹配 │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ tool.build(params) │
│  • Schema 验证    │
│  • 自定义验证     │
│  → ToolInvocation │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ shouldConfirmExecute() │
│  • Policy 决策    │
│  • 用户确认 (如需要) │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ invocation.execute() │
│  • 实际执行       │
│  • 流式输出 (可选) │
│  → ToolResult     │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ 格式化结果返回 LLM │
└───────────────────┘
```

### 7.2 错误处理

```typescript
enum ToolErrorType {
  INVALID_TOOL_PARAMS = 'invalid_tool_params',
  EXECUTION_FAILED = 'execution_failed',
  DISCOVERED_TOOL_EXECUTION_ERROR = 'discovered_tool_execution_error',
  // ...
}
```

---

## 8. 与其他组件的交互

### 8.1 与 Agent Loop 的交互

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Agent Loop  │────▶│ ToolRegistry│────▶│   LLM API   │
│             │     │ (getSchema) │     │(FunctionDecl)│
└──────┬──────┘     └─────────────┘     └─────────────┘
       │
       │ ◄──────────────────────────────────────────────┐
       │              工具调用请求                      │
       ▼                                              │
┌─────────────┐                                       │
│ ToolBuilder │                                       │
│ .build()    │                                       │
└──────┬──────┘                                       │
       │                                              │
       ▼                                              │
┌─────────────┐     ┌─────────────┐                  │
│ToolInvocation│────▶│  execute()  │──────────────────┘
│             │     │             │     工具执行结果
└─────────────┘     └─────────────┘
```

### 8.2 与确认系统的交互

```
┌─────────────────┐
│ ToolInvocation  │
│ shouldConfirmExecute()
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   MessageBus    │
│ publish(request)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Policy Engine  │
│  / UI Layer     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Response      │
│ subscribe(resp) │
└─────────────────┘
```

---

## 9. 架构特点总结

- **声明式定义**: `DeclarativeTool` 提供类型安全的工具定义方式
- **分离式执行**: validate → build → execute 三阶段分离，支持预执行确认
- **三层工具来源**: Built-in、Discovered、MCP 支持灵活扩展
- **Kind 分类系统**: 精细化工具权限控制
- **事件驱动确认**: MessageBus 解耦确认流程与核心逻辑
- **状态机管理**: MCP 发现状态明确管理 (NOT_STARTED → IN_PROGRESS → COMPLETED)

---

## 10. 排障速查

- **工具未找到**: 检查 `getTool` 的别名解析和 MCP 全限定名匹配
- **参数验证失败**: 查看 `SchemaValidator.validate` 错误输出
- **确认流程异常**: 检查 MessageBus 事件订阅/发布
- **MCP 工具不显示**: 查看 `McpClientManager` 的发现状态和权限设置
- **工具排序异常**: 检查 `sortTools` 的优先级逻辑
