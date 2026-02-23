# CLI 入口（Qwen Code）

本文分析 Qwen Code 的 CLI 入口流程，包括程序启动、参数解析、初始化流程和 UI 渲染。

---

## 1. 先看全局（流程图）

### 1.1 启动流程图

```text
┌─────────────────────────────────────────────────────────────────────┐
│  ENTRY: packages/cli/index.ts:14                                     │
│  ┌─────────────────────────────────────────┐                        │
│  │ #!/usr/bin/env node                     │ ◄── shebang 入口        │
│  │ import { main } from './src/gemini.js'  │                        │
│  │ main().catch(handleFatalError)          │ ◄── 全局异常捕获        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MAIN: packages/cli/src/gemini.tsx:209                               │
│  ┌─────────────────────────────────────────┐                        │
│  │ setupUnhandledRejectionHandler()        │ ◄── Promise 异常处理    │
│  │ loadSettings()                          │ ◄── 加载用户设置        │
│  │ cleanupCheckpoints()                    │ ◄── 清理旧 checkpoint   │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  参数解析与模式选择                                                   │
│  ┌─────────────────────────────────────────┐                        │
│  │ parseArguments()                        │ ◄── meow 解析 CLI 参数  │
│  │                                         │                        │
│  │ ┌─────────────────────────────────────┐ │                        │
│  │ │ 交互模式?                           │ │                        │
│  │ │   ├─Yes──► startInteractiveUI()    │ │ ◄── Ink React UI       │
│  │ │   └─No                              │ │                        │
│  │ │       ├─ stream-json ──► runAcpAgent│ │ ◄── ACP 协议模式       │
│  │ │       └─ non-interactive            │ │                        │
│  │ │            runNonInteractive()      │ │ ◄── 一次性执行         │
│  │ └─────────────────────────────────────┘ │                        │
│  └────────┬────────────────────────────────┘                        │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼ (交互模式)
┌─────────────────────────────────────────────────────────────────────┐
│  交互式 UI 初始化                                                     │
│  ┌─────────────────────────────────────────┐                        │
│  │ initializeApp()                         │ ◄── 初始化认证、主题等  │
│  │   │                                     │                        │
│  │   ├── initializeI18n()                  │ ◄── 多语言初始化        │
│  │   ├── performInitialAuth()              │ ◄── 认证流程            │
│  │   └── validateTheme()                   │ ◄── 主题验证            │
│  │                                         │                        │
│  │ render(<AppWrapper />)                  │ ◄── Ink 渲染            │
│  │   ├── SettingsContext.Provider          │                        │
│  │   ├── KeypressProvider                  │                        │
│  │   ├── SessionStatsProvider              │                        │
│  │   └── VimModeProvider                   │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块/函数  ├──┤ 子步骤  ──► 流程分支
```

### 1.2 沙盒与进程管理

```text
┌─────────────────────────────────────────────────────────────────────┐
│  沙盒启动流程 (如果启用)                                               │
└─────────────────────────────────────────────────────────────────────┘

main() 启动
    │
    ▼
检查 SANDBOX 环境变量?
    │
    ├── 未设置 ─────────────────┐
    │                          ▼
    │   ┌───────────────────────────────┐
    │   │ loadSandboxConfig()           │
    │   │ 检查 settings.json 沙盒配置    │
    │   └────────┬──────────────────────┘
    │            │
    │            ▼
    │   ┌───────────────────────────────┐
    │   │ 沙盒已启用?                   │
    │   │   ├─Yes──► start_sandbox()   │
    │   │   │        (子进程隔离)        │
    │   │   └─No──► relaunchAppInChildProcess()
    │   │            (仅内存优化重启)    │
    │   └───────────────────────────────┘
    │
    └── 已设置 ──► 正常运行 (已在沙盒内)

图例: 沙盒使用 Docker 容器隔离文件系统
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道入口是 `index.ts`，主逻辑在 `gemini.tsx` 的 `main()` 函数。
- **3 分钟版**：看 `1.1` + `1.2` + `3.1` 节，了解初始化流程和沙盒机制。
- **10 分钟版**：通读全文，掌握完整的启动流程、参数解析和错误处理。

### 2.1 一句话定义

Qwen Code 的 CLI 入口是「**双进程沙盒可选的 React 终端应用**」：入口文件处理全局异常，主程序负责参数解析和模式分发，交互模式下渲染 Ink UI，支持沙盒隔离和内存优化重启。

---

## 3. 关键源码解析

### 3.1 入口文件 (index.ts)

✅ **Verified**: `qwen-code/packages/cli/index.ts:14`

```typescript
#!/usr/bin/env node

import { main } from './src/gemini.js';
import { FatalError } from '@qwen-code/qwen-code-core';

// --- Global Entry Point ---
main().catch((error) => {
  if (error instanceof FatalError) {
    // 可预期的致命错误，显示用户友好信息
    console.error(errorMessage);
    process.exit(error.exitCode);
  }
  // 未预期的错误，显示完整堆栈
  console.error('An unexpected critical error occurred:');
  console.error(error.stack);
  process.exit(1);
});
```

**关键职责**：
1. 全局异常捕获，区分 FatalError 和未预期错误
2. 支持 `NO_COLOR` 环境变量控制输出颜色
3. 确保进程正确退出（非零退出码表示错误）

### 3.2 主程序 (gemini.tsx)

✅ **Verified**: `qwen-code/packages/cli/src/gemini.tsx:209`

```typescript
export async function main() {
  setupUnhandledRejectionHandler();  // 设置 Promise 异常处理
  const settings = loadSettings();    // 加载用户设置
  await cleanupCheckpoints();         // 清理旧 checkpoint

  let argv = await parseArguments();  // 解析 CLI 参数

  // --- 初始化流程 ---
  dns.setDefaultResultOrder(validateDnsResolutionOrder(...));
  themeManager.loadCustomThemes(settings.merged.ui?.customThemes);

  // --- 沙盒检查 ---
  if (!process.env['SANDBOX']) {
    const sandboxConfig = await loadSandboxConfig(settings.merged, argv);
    if (sandboxConfig) {
      // 启动沙盒子进程
      await relaunchOnExitCode(() =>
        start_sandbox(sandboxConfig, memoryArgs, partialConfig, sandboxArgs)
      );
      process.exit(0);
    } else {
      // 普通子进程重启（内存优化）
      await relaunchAppInChildProcess(memoryArgs, []);
    }
  }

  // --- 会话恢复 ---
  if (argv.resume === '') {
    const selectedSessionId = await showResumeSessionPicker();
    argv = { ...argv, resume: selectedSessionId };
  }

  // --- 加载配置 ---
  const config = await loadCliConfig(settings.merged, argv, ...);
  registerCleanup(() => config.shutdown());  // 注册清理钩子

  // --- 模式分发 ---
  if (config.isInteractive()) {
    await startInteractiveUI(config, settings, startupWarnings, ...);
  } else {
    await runNonInteractive(nonInteractiveConfig, settings, input, prompt_id);
  }
}
```

### 3.3 初始化器 (initializer.ts)

✅ **Verified**: `qwen-code/packages/cli/src/core/initializer.ts:33`

```typescript
export async function initializeApp(
  config: Config,
  settings: LoadedSettings,
): Promise<InitializationResult> {
  // 初始化 i18n 系统
  const languageSetting = process.env['QWEN_CODE_LANG'] ||
    settings.merged.general?.language || 'auto';
  await initializeI18n(languageSetting as SupportedLanguage | 'auto');

  // 执行初始认证
  const authType = config.getModelsConfig().getCurrentAuthType();
  const authError = await performInitialAuth(config, authType);

  // 验证主题
  const themeError = validateTheme(settings);

  // IDE 模式连接
  if (config.getIdeMode()) {
    const ideClient = await IdeClient.getInstance();
    await ideClient.connect();
    logIdeConnection(config, new IdeConnectionEvent(IdeConnectionType.START));
  }

  return {
    authError,
    themeError,
    shouldOpenAuthDialog: !config.getModelsConfig().wasAuthTypeExplicitlyProvided() || !!authError,
    geminiMdFileCount: config.getGeminiMdFileCount(),
  };
}
```

### 3.4 交互式 UI 启动

✅ **Verified**: `qwen-code/packages/cli/src/gemini.tsx:139`

```typescript
export async function startInteractiveUI(
  config: Config,
  settings: LoadedSettings,
  startupWarnings: string[],
  workspaceRoot: string,
  initializationResult: InitializationResult,
) {
  const AppWrapper = () => {
    const kittyProtocolStatus = useKittyKeyboardProtocol();
    return (
      <SettingsContext.Provider value={settings}>
        <KeypressProvider kittyProtocolEnabled={kittyProtocolStatus.enabled} ...>
          <SessionStatsProvider sessionId={config.getSessionId()}>
            <VimModeProvider settings={settings}>
              <AppContainer
                config={config}
                settings={settings}
                startupWarnings={startupWarnings}
                version={version}
                initializationResult={initializationResult}
              />
            </VimModeProvider>
          </SessionStatsProvider>
        </KeypressProvider>
      </SettingsContext.Provider>
    );
  };

  const instance = render(
    process.env['DEBUG'] ? (
      <React.StrictMode><AppWrapper /></React.StrictMode>
    ) : (
      <AppWrapper />
    ),
    {
      exitOnCtrlC: false,  // 禁用默认 Ctrl+C 退出
      isScreenReaderEnabled: config.getScreenReader(),
    },
  );

  // 注册清理钩子
  registerCleanup(() => instance.unmount());
}
```

---

## 4. 参数解析

### 4.1 CLI 参数来源

Qwen Code 使用 `meow` 库进行参数解析，参数来源优先级：

1. 命令行参数（最高优先级）
2. 环境变量
3. 用户设置文件（`~/.qwen/settings.json`）
4. 项目设置文件（`.qwen/settings.json`）
5. 默认值（最低优先级）

### 4.2 常用参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--prompt, -p` | 直接输入提示词 | `-p "解释这段代码"` |
| `--resume [id]` | 恢复会话 | `--resume` 或 `--resume <session-id>` |
| `--yolo` | 自动批准所有操作 | `--yolo` |
| `--debug` | 启用调试模式 | `--debug` |
| `--ide-mode` | 启用 IDE 集成模式 | `--ide-mode` |
| `--model` | 指定模型 | `--model gemini-2.5-flash` |
| `--no-sandbox` | 禁用沙盒 | `--no-sandbox` |

---

## 5. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| 启动崩溃无提示 | 检查全局异常处理 | `index.ts:14` |
| 认证失败 | 检查 performInitialAuth | `initializer.ts:47` |
| 主题加载失败 | 检查 validateTheme | `initializer.ts:57` |
| UI 不渲染 | 检查 Ink render 调用 | `gemini.tsx:179` |
| 沙盒启动失败 | 检查 loadSandboxConfig | `gemini.tsx:248` |
| 内存不足 | 检查 getNodeMemoryArgs | `gemini.tsx:83` |
| 会话恢复失败 | 检查 resume 参数处理 | `gemini.tsx:327` |

---

## 6. 架构特点

### 6.1 双进程架构

```
父进程 (index.ts)
    │
    ├── 沙盒模式 ──► Docker 容器 ──► 子进程
    │
    └── 普通模式 ──► relaunchAppInChildProcess() ──► 子进程
```

**目的**：
- 沙盒模式：文件系统隔离，安全执行
- 普通模式：内存优化（--max-old-space-size）

### 6.2 React Context 层级

```
SettingsContext      # 设置共享
    │
    ├── KeypressProvider      # 键盘事件处理
    │       └── SessionStatsProvider  # 会话统计
    │               └── VimModeProvider  # Vim 模式
    │                       └── AppContainer  # 主应用容器
```

### 6.3 清理机制

```typescript
// 注册清理钩子
registerCleanup(() => config.shutdown());      // MCP 客户端关闭
registerCleanup(() => instance.unmount());     // React 卸载

// 进程退出时执行
process.on('exit', runExitCleanup);
process.on('SIGINT', runExitCleanup);
```

---

## 7. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 入口结构 | index.ts + gemini.ts | ✅ 相同 |
| 沙盒支持 | ✅ 支持 | ✅ 继承 |
| 内存优化 | ✅ 支持 | ✅ 继承 |
| i18n 支持 | 有限 | ✅ 增强 |
| IDE 集成 | 实验性 | ✅ 原生支持 |

---

## 8. 总结

Qwen Code 的 CLI 入口设计特点：

1. **全局异常处理** - FatalError 分类处理，确保优雅退出
2. **双进程架构** - 支持沙盒隔离和内存优化
3. **模式分发** - 交互式/非交互式/ACP 模式灵活切换
4. **Context 层级** - React Context 提供依赖注入
5. **清理机制** - 完善的资源释放和进程管理
