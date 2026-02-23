# UI 交互（Qwen Code）

本文分析 Qwen Code 的 UI 交互系统，基于 React 和 Ink 的终端 UI 实现。

---

## 1. 先看全局（流程图）

### 1.1 UI 组件层级

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        Ink Renderer                                  │
│                     (packages/cli/src/gemini.tsx:179)               │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     AppWrapper                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ SettingsContext.Provider                                     │   │
│  │  └─────────────────────────────────────────────────────────┐ │   │
│  │     KeypressProvider                                       │ │   │
│  │      └───────────────────────────────────────────────────┐ │ │   │
│  │         SessionStatsProvider                             │ │ │   │
│  │          └─────────────────────────────────────────────┐ │ │ │   │
│  │             VimModeProvider                            │ │ │ │   │
│  │              └───────────────────────────────────────┐ │ │ │ │   │
│  │                 AppContainer                         │ │ │ │ │   │
│  │                  (packages/cli/src/ui/AppContainer.tsx)│ │ │ │   │
│  │                                                      │ │ │ │ │   │
│  │  ┌─────────────────────────────────────────────────┐ │ │ │ │ │   │
│  │  │ MainLayout                                      │ │ │ │ │ │   │
│  │  │  ├── Header (版本、状态)                         │ │ │ │ │ │   │
│  │  │  ├── ChatArea (对话历史)                         │ │ │ │ │ │   │
│  │  │  ├── InputBox (输入框)                           │ │ │ │ │ │   │
│  │  │  └── StatusBar (状态栏)                          │ │ │ │ │ │   │
│  │  └─────────────────────────────────────────────────┘ │ │ │ │ │   │
│  │                                                      │ │ │ │ │   │
│  └────────────────────────────────────────────────────────┘ │ │ │   │
└─────────────────────────────────────────────────────────────────────┘

图例: Context Provider 用于依赖注入，Props 层层传递
```

### 1.2 流式输出渲染流程

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     流式消息渲染时序                                  │
└──────────────────────────────────────────────────────────────────────┘

User Input
    │
    ▼
┌─────────────────┐
│ useGeminiStream │ ◄── UI Hook
│ .submitQuery()  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ geminiClient.   │ ◄── Core 层
│ sendMessageStream│
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│ 流式事件生成     │────►│ for await       │
│ Content         │     │ UI 逐字符渲染    │
│ Thought         │────►│ 思考内容折叠    │
│ ToolCallRequest │────►│ 显示确认对话框  │
│ ToolResult      │────►│ 显示执行结果    │
│ Finished        │────►│ 结束本轮        │
└─────────────────┘     └─────────────────┘
```

---

## 2. 阅读路径

- **30 秒版**：只看 `1.1` 流程图，知道 UI 基于 Ink + React，AppContainer 是根组件。
- **3 分钟版**：看 `1.1` + `1.2` 节，了解组件层级和流式渲染。
- **10 分钟版**：通读全文，掌握 Hook、Context 和组件实现。

### 2.1 一句话定义

Qwen Code 的 UI 是「**Ink + React 的终端渲染系统**」：使用 React 组件模型构建终端 UI，通过 Context Provider 管理全局状态，流式事件驱动逐字符渲染。

---

## 3. 核心组件

### 3.1 AppContainer

✅ **Verified**: `qwen-code/packages/cli/src/ui/AppContainer.tsx`

```typescript
export function AppContainer({
  config,
  settings,
  startupWarnings,
  version,
  initializationResult,
}: AppContainerProps) {
  // 主应用容器，管理全局状态
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  return (
    <Box flexDirection="column" height={"100%"}>
      {/* 头部信息 */}
      <Header version={version} />

      {/* 启动警告 */}
      {startupWarnings.map((warning) => (
        <WarningBanner key={warning} message={warning} />
      ))}

      {/* 主聊天区域 */}
      <ChatArea
        messages={messages}
        isLoading={isLoading}
      />

      {/* 输入框 */}
      <InputBox
        onSubmit={handleSubmit}
        isLoading={isLoading}
      />

      {/* 状态栏 */}
      <StatusBar
        tokenCount={tokenCount}
        sessionId={config.getSessionId()}
      />
    </Box>
  );
}
```

### 3.2 useGeminiStream Hook

```typescript
export function useGeminiStream(config: Config) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const submitQuery = useCallback(async (
    input: string,
    options?: { isContinuation?: boolean },
  ) => {
    setIsStreaming(true);

    const stream = geminiClient.sendMessageStream(
      [{ text: input }],
      abortController.signal,
      promptId,
      options,
    );

    for await (const event of stream) {
      switch (event.type) {
        case GeminiEventType.Content:
          // 追加文本到当前消息
          appendToCurrentMessage(event.value);
          break;
        case GeminiEventType.Thought:
          // 显示思考内容（可折叠）
          addThought(event.value);
          break;
        case GeminiEventType.ToolCallRequest:
          // 显示工具调用请求
          addToolCall(event.value);
          break;
        case GeminiEventType.ToolCallResponse:
          // 显示工具执行结果
          updateToolResult(event.value);
          break;
        case GeminiEventType.Finished:
          // 完成当前消息
          finalizeCurrentMessage();
          break;
      }
    }

    setIsStreaming(false);
  }, [config]);

  return { messages, isStreaming, submitQuery };
}
```

### 3.3 Context Providers

| Context | 文件路径 | 职责 |
|---------|----------|------|
| SettingsContext | `ui/contexts/SettingsContext.tsx` | 全局设置共享 |
| KeypressProvider | `ui/contexts/KeypressContext.tsx` | 键盘事件处理 |
| SessionStatsProvider | `ui/contexts/SessionContext.tsx` | 会话统计 |
| VimModeProvider | `ui/contexts/VimModeContext.tsx` | Vim 模式状态 |

---

## 4. 输入处理

### 4.1 键盘输入

```typescript
// KeypressProvider 处理特殊按键
function KeypressProvider({ children, config, ...props }) {
  useInput((input, key) => {
    // Ctrl+C 取消当前操作
    if (key.ctrl && input === 'c') {
      abortController.abort();
    }

    // Ctrl+D 退出
    if (key.ctrl && input === 'd') {
      process.exit(0);
    }

    // / 命令前缀
    if (input === '/' && !isTyping) {
      setShowCommandMenu(true);
    }
  });

  return <KeypressContext.Provider value={...}>{children}</KeypressContext.Provider>;
}
```

### 4.2 斜杠命令

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清空对话 |
| `/model` | 切换模型 |
| `/undo` | 撤销上一步 |
| `/checkpoint` | 管理检查点 |

---

## 5. 排障速查

| 问题 | 检查点 | 文件/代码 |
|------|--------|-----------|
| UI 不渲染 | 检查 Ink render | `gemini.tsx:179` |
| 流式输出卡顿 | 检查 useGeminiStream | `useGeminiStream.ts` |
| 键盘无响应 | 检查 KeypressProvider | `KeypressContext.tsx` |
| 主题不生效 | 检查 themeManager | `theme-manager.ts` |
| 状态不更新 | 检查 Context Provider | 各 Context 文件 |

---

## 6. 对比 Gemini CLI

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| 框架 | Ink + React | ✅ 相同 |
| Context 层级 | 多层 Provider | ✅ 继承 |
| Vim 模式 | 支持 | ✅ 继承 |
| 自定义主题 | 支持 | ✅ 继承 |
| Kitty 协议 | 支持 | ✅ 继承 |

---

## 7. 总结

Qwen Code 的 UI 系统特点：

1. **React 组件模型** - 熟悉的开发体验
2. **Context 状态管理** - 依赖注入解耦
3. **流式渲染** - 事件驱动逐字符更新
4. **Vim 模式** - 支持 Vim 键位绑定
5. **可扩展主题** - 自定义配色方案
