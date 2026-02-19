# Tool System（opencode）

本文基于 `./opencode/packages/opencode/src/tool` 源码，解释 OpenCode 的工具系统架构——从 Zod Schema 定义、动态注册到权限控制的完整链路。

---

## 1. 先看全局（架构图）

```text
┌─────────────────────────────────────────────────────────────────────┐
│  工具定义层：Zod Schema + 工厂函数                                    │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Tool.define(id, init)                                          ││
│  │  ├── parameters: z.ZodType      (参数 Schema)                   ││
│  │  ├── description: string        (功能描述)                       ││
│  │  └── execute(args, ctx)         (执行逻辑)                       ││
│  │      ├── ctx.ask()              (权限请求)                       ││
│  │      ├── ctx.metadata()         (元数据更新)                     ││
│  │      └── ctx.abort              (取消信号)                       ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  工具注册层：ToolRegistry 支持动态扩展                                │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ToolRegistry                                                   ││
│  │  ├── state()                    初始化状态                       ││
│  │  │   ├── 加载自定义工具 (~/tools/*.{js,ts})                      ││
│  │  │   └── 加载插件工具 (Plugin.list())                           ││
│  │  ├── register(tool)             动态注册工具                     ││
│  │  └── tools(model, agent)        获取工具列表                     ││
│  │      └── 根据模型过滤 (apply_patch vs edit/write)               ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP 集成层：外部工具服务扩展                                         │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  MCP Server (可选)                                              ││
│  │  ├── mcp-server.ts              服务器实现                       ││
│  │  └── 通过 MCP 协议接入外部工具                                   ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心概念与设计哲学

### 2.1 一句话定义

OpenCode 的工具系统是「**Zod Schema 类型安全 + 动态注册扩展 + 权限驱动执行**」的架构：工具通过 `Tool.define` 使用 Zod 定义参数 Schema，支持从文件系统和插件动态加载，执行时通过 `ctx.ask()` 进行细粒度权限控制。

### 2.2 设计特点

| 特性 | 实现方式 | 优势 |
|------|---------|------|
| 类型安全 | Zod Schema | 运行时类型验证，自动错误提示 |
| 动态注册 | ToolRegistry.register() | 支持运行时扩展 |
| 自定义工具 | ~/tools/*.ts 自动加载 | 用户可自定义工具 |
| 插件扩展 | Plugin 系统 | 第三方工具集成 |
| 权限控制 | ctx.ask() 细粒度请求 | 安全可控 |
| 输出截断 | Truncate 自动处理 | 防止上下文溢出 |

---

## 3. 工具定义架构

### 3.1 核心接口

```typescript
// packages/opencode/src/tool/tool.ts
namespace Tool {
  export interface Info<Parameters extends z.ZodType = z.ZodType, M extends Metadata = Metadata> {
    id: string;
    init: (ctx?: InitContext) => Promise<{
      description: string;
      parameters: Parameters;
      execute(
        args: z.infer<Parameters>,
        ctx: Context,
      ): Promise<{
        title: string;
        metadata: M;
        output: string;
        attachments?: ...;
      }>;
    }>;
  }

  export type Context<M extends Metadata = Metadata> = {
    sessionID: string;
    messageID: string;
    agent: string;
    abort: AbortSignal;
    callID?: string;
    messages: MessageV2.WithParts[];
    metadata(input: { title?: string; metadata?: M }): void;
    ask(input: Omit<PermissionNext.Request, "id" | "sessionID" | "tool">): Promise<void>;
  };
}
```

### 3.2 工厂函数

```typescript
export function define<Parameters extends z.ZodType, Result extends Metadata>(
  id: string,
  init: Info<Parameters, Result>["init"] | Awaited<ReturnType<Info<Parameters, Result>["init"]>>,
): Info<Parameters, Result> {
  return {
    id,
    init: async (initCtx) => {
      const toolInfo = init instanceof Function ? await init(initCtx) : init;

      // 包装 execute 函数，添加参数验证和截断
      toolInfo.execute = async (args, ctx) => {
        // 1. Zod 参数验证
        try {
          toolInfo.parameters.parse(args);
        } catch (error) {
          if (error instanceof z.ZodError && toolInfo.formatValidationError) {
            throw new Error(toolInfo.formatValidationError(error), { cause: error });
          }
          throw new Error(`The ${id} tool was called with invalid arguments: ${error}`);
        }

        // 2. 执行工具
        const result = await execute(args, ctx);

        // 3. 自动截断输出
        if (result.metadata.truncated !== undefined) {
          return result;  // 工具自己处理截断
        }
        const truncated = await Truncate.output(result.output, {}, initCtx?.agent);
        return {
          ...result,
          output: truncated.content,
          metadata: { ...result.metadata, truncated: truncated.truncated },
        };
      };
      return toolInfo;
    },
  };
}
```

### 3.3 工具定义示例（BashTool）

```typescript
// packages/opencode/src/tool/bash.ts
export const BashTool = Tool.define("bash", async () => {
  const shell = Shell.acceptable();

  return {
    description: DESCRIPTION.replaceAll("${directory}", Instance.directory),
    parameters: z.object({
      command: z.string().describe("The command to execute"),
      timeout: z.number().optional().describe("Optional timeout in milliseconds"),
      workdir: z.string().optional().describe("The working directory"),
      description: z.string().describe("Clear, concise description of what this command does"),
    }),

    async execute(params, ctx) {
      const cwd = params.workdir || Instance.directory;
      const timeout = params.timeout ?? DEFAULT_TIMEOUT;

      // 1. 解析命令，识别文件操作
      const tree = await parser().then((p) => p.parse(params.command));
      const directories = new Set<string>();
      const patterns = new Set<string>();

      // 2. 识别 cd/rm/cp/mv 等命令的路径参数
      for (const node of tree.rootNode.descendantsOfType("command")) {
        // ... 路径解析逻辑
      }

      // 3. 请求外部目录权限
      if (directories.size > 0) {
        await ctx.ask({
          permission: "external_directory",
          patterns: Array.from(directories).map(dir => path.join(dir, "*")),
          always: globs,
          metadata: {},
        });
      }

      // 4. 请求 bash 执行权限
      if (patterns.size > 0) {
        await ctx.ask({
          permission: "bash",
          patterns: Array.from(patterns),
          always: Array.from(always),
          metadata: {},
        });
      }

      // 5. 执行命令
      const proc = spawn(params.command, { shell, cwd, ... });

      // 6. 流式输出处理
      // ...

      return { title: params.description, metadata: {...}, output };
    },
  };
});
```

---

## 4. ToolRegistry：动态注册与加载

### 4.1 核心结构

```typescript
namespace ToolRegistry {
  const log = Log.create({ service: "tool.registry" });

  // 状态管理
  export const state = Instance.state(async () => {
    const custom = [] as Tool.Info[];

    // 1. 从用户目录加载自定义工具
    const glob = new Bun.Glob("{tool,tools}/*.{js,ts}");
    const matches = await Config.directories().then((dirs) =>
      dirs.flatMap((dir) => [...glob.scanSync({ cwd: dir, absolute: true })]),
    );

    for (const match of matches) {
      const namespace = path.basename(match, path.extname(match));
      const mod = await import(match);
      for (const [id, def] of Object.entries<ToolDefinition>(mod)) {
        custom.push(fromPlugin(id === "default" ? namespace : `${namespace}_${id}`, def));
      }
    }

    // 2. 从插件加载工具
    const plugins = await Plugin.list();
    for (const plugin of plugins) {
      for (const [id, def] of Object.entries(plugin.tool ?? {})) {
        custom.push(fromPlugin(id, def));
      }
    }

    return { custom };
  });

  // 动态注册
  export async function register(tool: Tool.Info) {
    const { custom } = await state();
    const idx = custom.findIndex((t) => t.id === tool.id);
    if (idx >= 0) {
      custom.splice(idx, 1, tool);  // 替换已存在
    } else {
      custom.push(tool);  // 新增
    }
  }
}
```

### 4.2 工具列表组装

```typescript
async function all(): Promise<Tool.Info[]> {
  const custom = await state().then((x) => x.custom);
  const config = await Config.get();
  const question = ["app", "cli", "desktop"].includes(Flag.OPENCODE_CLIENT)
    || Flag.OPENCODE_ENABLE_QUESTION_TOOL;

  return [
    InvalidTool,
    ...(question ? [QuestionTool] : []),
    BashTool,
    ReadTool,
    GlobTool,
    GrepTool,
    EditTool,
    WriteTool,
    TaskTool,
    WebFetchTool,
    TodoWriteTool,
    WebSearchTool,
    CodeSearchTool,
    SkillTool,
    ApplyPatchTool,
    ...(Flag.OPENCODE_EXPERIMENTAL_LSP_TOOL ? [LspTool] : []),
    ...(config.experimental?.batch_tool === true ? [BatchTool] : []),
    ...(Flag.OPENCODE_EXPERIMENTAL_PLAN_MODE ? [PlanExitTool, PlanEnterTool] : []),
    ...custom,  // 自定义工具
  ];
}
```

### 4.3 模型特定过滤

```typescript
export async function tools(model: { providerID: string; modelID: string }, agent?: Agent.Info) {
  const tools = await all();

  return Promise.all(
    tools
      .filter((t) => {
        // 1. 特殊工具权限控制 (codesearch/websearch)
        if (t.id === "codesearch" || t.id === "websearch") {
          return model.providerID === "opencode" || Flag.OPENCODE_ENABLE_EXA;
        }

        // 2. GPT 模型使用 apply_patch 替代 edit/write
        const usePatch = model.modelID.includes("gpt-")
          && !model.modelID.includes("oss")
          && !model.modelID.includes("gpt-4");
        if (t.id === "apply_patch") return usePatch;
        if (t.id === "edit" || t.id === "write") return !usePatch;

        return true;
      })
      .map(async (t) => {
        // 初始化工具，触发 tool.definition 插件事件
        const tool = await t.init({ agent });
        await Plugin.trigger("tool.definition", { toolID: t.id }, output);
        return { id: t.id, ...tool };
      }),
  );
}
```

---

## 5. 权限控制系统

### 5.1 权限请求流程

```
 execute(params, ctx)
       │
       ├──► 解析命令/操作
       │     └── 识别受影响的文件/目录
       │
       ├──► ctx.ask({
       │       permission: "bash" | "external_directory" | ...,
       │       patterns: ["rm -rf *", "cd /etc"],
       │       always: [...],  // "总是允许" 模式
       │       metadata: {}
       │     })
       │
       ├──► 等待用户确认
       │     └── PermissionNext 系统处理
       │
       └──► 执行或拒绝
```

### 5.2 权限类型

| 权限类型 | 说明 | 示例场景 |
|----------|------|----------|
| `bash` | Shell 命令执行 | 执行任意 shell 命令 |
| `external_directory` | 外部目录访问 | 访问项目目录外的文件 |
| `write` | 文件写入 | 修改代码文件 |
| `delete` | 文件删除 | 删除文件 |

### 5.3 Bash 工具权限识别示例

```typescript
// 使用 Tree-sitter 解析 bash 命令
const tree = await parser().then((p) => p.parse(params.command));

for (const node of tree.rootNode.descendantsOfType("command")) {
  const command = [];
  for (let i = 0; i < node.childCount; i++) {
    const child = node.child(i);
    if (["command_name", "word", "string"].includes(child.type)) {
      command.push(child.text);
    }
  }

  // 识别文件操作命令
  if (["cd", "rm", "cp", "mv", "mkdir", "touch"].includes(command[0])) {
    for (const arg of command.slice(1)) {
      if (arg.startsWith("-")) continue;
      const resolved = await $`realpath ${arg}`.cwd(cwd).quiet().nothrow().text();
      if (resolved && !Instance.containsPath(resolved)) {
        directories.add(resolved);  // 标记外部目录
      }
    }
  }

  // 添加 bash 权限模式
  if (command.length && command[0] !== "cd") {
    patterns.add(commandText);
    always.add(BashArity.prefix(command).join(" ") + " *");
  }
}
```

---

## 6. 内置工具清单

### 6.1 核心工具

| 工具 ID | 功能 | 关键参数 |
|---------|------|----------|
| `bash` | Shell 执行 | command, timeout, workdir |
| `read` | 文件读取 | file_path, offset, limit |
| `write` | 文件写入 | file_path, content |
| `edit` | 文件编辑 | file_path, old_string, new_string |
| `apply_patch` | 补丁应用 | path, diff |
| `glob` | 文件匹配 | pattern, path |
| `grep` | 内容搜索 | pattern, path |

### 6.2 扩展工具

| 工具 ID | 功能 | 说明 |
|---------|------|------|
| `webfetch` | Web 内容获取 | URL 内容拉取 |
| `websearch` | Web 搜索 | 需要 opencode provider 或 ENABLE_EXA flag |
| `codesearch` | 代码搜索 | 语义代码搜索 |
| `todo` | 待办管理 | 任务追踪 |
| `task` | 子任务 | 子 Agent 任务委派 |
| `skill` | 技能管理 | 加载/卸载技能 |
| `lsp` | LSP 工具 | 实验性功能 |
| `batch` | 批量操作 | 实验性功能 |
| `plan` | 计划模式 | 实验性功能 (CLI only) |

### 6.3 模型特定工具选择

```typescript
// GPT 模型使用 apply_patch 替代 edit/write
const usePatch = model.modelID.includes("gpt-")
  && !model.modelID.includes("oss")
  && !model.modelID.includes("gpt-4");
```

---

## 7. 插件系统扩展

### 7.1 插件工具转换

```typescript
function fromPlugin(id: string, def: ToolDefinition): Tool.Info {
  return {
    id,
    init: async (initCtx) => ({
      parameters: z.object(def.args),
      description: def.description,
      execute: async (args, ctx) => {
        const pluginCtx = {
          ...ctx,
          directory: Instance.directory,
          worktree: Instance.worktree,
        } as PluginToolContext;

        const result = await def.execute(args as any, pluginCtx);
        const out = await Truncate.output(result, {}, initCtx?.agent);

        return {
          title: "",
          output: out.truncated ? out.content : result,
          metadata: { truncated: out.truncated, outputPath: out.outputPath },
        };
      },
    }),
  };
}
```

### 7.2 插件事件

```typescript
// 工具定义事件
await Plugin.trigger("tool.definition", { toolID: t.id }, output);

// Shell 环境事件
const shellEnv = await Plugin.trigger("shell.env", { cwd }, { env: {} });
```

---

## 8. 与其他组件的交互

### 8.1 与 Agent Loop 的交互

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Agent Loop │────▶│ToolRegistry │────▶│   tools()   │
│             │     │   .tools()  │     │  (filtered) │
└──────┬──────┘     └─────────────┘     └─────────────┘
       │
       │ ◄──────────────────────────────────────────────┐
       │              工具调用结果                       │
       ▼                                              │
┌─────────────┐                                       │
│  Tool.execute                                       │
│  • Zod 验证  │                                       │
│  • ctx.ask() │                                       │
│  • 执行      │───────────────────────────────────────┘
│  • 截断      │        返回 {title, metadata, output}
└─────────────┘
```

### 8.2 与权限系统的交互

```
┌─────────────────┐
│   Tool.execute  │
│  ctx.ask({
│    permission,
│    patterns
│  })             │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ PermissionNext  │
│  系统           │
│  • 检查缓存     │
│  • 用户确认     │
│  • 记录决策     │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
  允许       拒绝
```

---

## 9. 架构特点总结

- **Zod 类型安全**: 参数 Schema 定义提供运行时验证
- **工厂函数定义**: `Tool.define()` 提供一致的 API
- **动态注册**: 支持自定义工具和插件扩展
- **细粒度权限**: `ctx.ask()` 支持操作级权限控制
- **自动截断**: 输出自动截断防止上下文溢出
- **模型适配**: 根据模型类型选择合适工具 (apply_patch vs edit)
- **插件事件**: 工具定义和执行钩子支持扩展

---

## 10. 排障速查

- **参数验证失败**: 检查 Zod Schema 定义和输入数据类型
- **工具未加载**: 检查 `~/tools/` 目录或插件配置
- **权限请求失败**: 检查 `ctx.ask()` 调用和 PermissionNext 系统
- **输出截断异常**: 查看 `Truncate.output` 配置
- **Bash 解析错误**: 检查 Tree-sitter 解析器初始化
- **模型特定工具不显示**: 检查 `tools()` 中的过滤逻辑
