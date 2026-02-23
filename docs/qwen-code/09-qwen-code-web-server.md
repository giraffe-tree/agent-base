# Web Server（Qwen Code）

本文分析 Qwen Code 的 Web Server 功能。

---

## 现状

⚠️ **Inferred**: 经检查 Qwen Code 源码，**未发现独立的 Web Server 实现**。

Qwen Code 与 Gemini CLI 架构类似，主要提供以下运行模式：

| 模式 | 说明 | 状态 |
|------|------|------|
| 交互式 CLI | 基于 Ink 的终端 UI | ✅ 完整支持 |
| 非交互式 CLI | 单次执行 | ✅ 完整支持 |
| ACP 模式 | Agent Communication Protocol | ✅ 支持 |
| Web Server | HTTP API 服务 | ❌ 未实现 |

---

## ACP 模式

Qwen Code 支持 ACP（Agent Communication Protocol）模式，通过 `runAcpAgent` 提供结构化通信：

```typescript
// packages/cli/src/gemini.tsx:401
if (config.getExperimentalZedIntegration()) {
  return runAcpAgent(config, settings, argv);
}
```

ACP 模式与 Web Server 不同，它基于 stdio 流而非 HTTP 协议。

---

## 与 Gemini CLI 对比

| 特性 | Gemini CLI | Qwen Code |
|------|------------|-----------|
| Web Server | ❌ 无 | ❌ 无 |
| ACP 协议 | ✅ 支持 | ✅ 支持 |
| Stream JSON | ✅ 支持 | ✅ 支持 |

---

## 结论

**Web Server 功能在 Qwen Code 中标记为 N/A**。

如需类似功能，可考虑：
1. 使用 ACP 模式进行进程间通信
2. 使用 `--input-format=stream-json` 进行结构化输入输出
3. 自行封装 HTTP 层调用 CLI
