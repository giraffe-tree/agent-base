# Skill 执行超时机制跨项目对比

## 结论

五个项目的超时机制呈现**分层演进**特征：Codex/Gemini CLI 侧重**基础设施级**的异步取消和状态机管理；Kimi CLI 强调**业务级**的保守超时与状态回滚；OpenCode 创新性地引入**动态超时重置**；SWE-agent 则面向**学术研究**设计了可重采样错误恢复。核心差异在于超时与系统其他模块的耦合深度。

---

## 对比维度总览

| 维度 | Codex | Gemini CLI | Kimi CLI | OpenCode | SWE-agent |
|-----|-------|-----------|----------|----------|-----------|
| **超时粒度** | 两层（启动+执行） | 一层（工具级） | 一层（工具级） | 两层（Bash+MCP） | 两层（单命令+总时长） |
| **默认超时** | 30s / 10min | 10min | 60s | 5min / 10min | 30s / 1h |
| **单位** | 秒（Duration） | 毫秒 | 秒 | 毫秒 | 秒 |
| **取消机制** | CancellationToken | AbortController + cancelAll | asyncio.CancelledError | AbortController | subprocess.kill |
| **超时后行为** | EventMsg::Error | Scheduler→Error | ToolError + 回滚 | Zod Error | 重试/兜底提交 |
| **特殊机制** | 原生安全沙箱 | 状态机驱动 | Checkpoint 联动 | resetTimeoutOnProgress | 可重采样错误 |
| **适合场景** | 企业级生产 | 交互式 CLI | 状态敏感任务 | 长任务处理 | 自动化评测 |

---

## 流程图：跨项目超时处理总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Skill 执行超时机制 - 跨项目对比                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   用户调用工具                                                            │
│        │                                                                │
│        ▼                                                                │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                     超时配置读取                                  │  │
│   │  ┌─────────┬─────────┬─────────┬─────────┬─────────┐           │  │
│   │  │  Codex  │ Gemini  │  Kimi   │OpenCode │SWE-agent│           │  │
│   │  │ 30s/10m │  10min  │   60s   │  5min   │  30s/1h │           │  │
│   │  └─────────┴─────────┴─────────┴─────────┴─────────┘           │  │
│   └─────────────────────────────────────────────────────────────────┘  │
│        │                                                                │
│        ▼                                                                │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                     执行与超时控制                                │  │
│   │  ┌─────────┬─────────┬─────────┬─────────┬─────────┐           │  │
│   │  │ tokio:: │Promise. │asyncio. │ execa/  │subpro-  │           │  │
│   │  │ timeout │  race   │wait_for │ mcp sdk │ cess    │           │  │
│   │  │CancellationToken│AbortController│CancelledError│AbortController│kill │  │
│   │  └─────────┴─────────┴─────────┴─────────┴─────────┘           │  │
│   └─────────────────────────────────────────────────────────────────┘  │
│        │                                                                │
│        ├─────────────────────────────────────────────────────────┐     │
│        │                          超时触发                        │     │
│        ▼                                                          ▼     │
│   ┌─────────────┐                                            ┌────────┐│
│   │   正常完成   │                                            │  超时   ││
│   └──────┬──────┘                                            └───┬────┘│
│          │                                                      │     │
│          ▼                                                      ▼     │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                     超时后处理                                    │  │
│   │  ┌─────────┬─────────┬─────────┬─────────┬─────────┐           │  │
│   │  │ EventMsg│Scheduler│ ToolErr │Zod Error│forward_ │           │  │
│   │  │::Error  │  Error  │+Rollback│         │handling │           │  │
│   │  │         │  state  │         │         │         │           │  │
│   │  │会话继续  │状态更新  │状态恢复  │LLM纠正  │重试/兜底 │           │  │
│   │  └─────────┴─────────┴─────────┴─────────┴─────────┘           │  │
│   └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 数据流转对比图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        数据流转对比                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   Codex                    Gemini CLI               Kimi CLI            │
│   ───────                  ───────────              ─────────           │
│                                                                         │
│   Config ──▶ Duration      Config ──▶ number        Config ──▶ int(秒)  │
│      │                        │                        │                │
│      ▼                        ▼                        ▼                │
│   tokio::time::          ToolScheduler             asyncio.wait_for     │
│   timeout()                  │                           │              │
│      │                       ▼                           ▼              │
│      ▼                  Promise.race()              Checkpoint          │
│   CancelToken                │                     create/rollback      │
│   cancel()         ┌─────────┴─────────┐               │                │
│      │             ▼                   ▼               ▼                │
│      ▼        Success              Error          ToolError             │
│   EventMsg    emitResult          emitError      (recoverable)          │
│   ::Error                                                               │
│                                                                         │
│   ─────────────────────────────────────────────────────────────────     │
│                                                                         │
│   OpenCode                 SWE-agent                                    │
│   ────────                 ─────────                                    │
│                                                                         │
│   Config ──▶ number(ms)    Config ──▶ int(秒)                           │
│      │                        │                                         │
│      ▼                        ▼                                         │
│   execa()/mcp              subprocess.run                               │
│      │                        │                                         │
│      ▼                        ▼                                         │
│   Zod Error             TimeoutExpired                                  │
│   validation            ┌──────────────┐                                │
│      │                  ▼              ▼                                │
│      ▼              Execution     forward_with_                         │
│   ToolResult        Result        handling()                            │
│   (success:false)   (timed_out)       │                                 │
│                                     重试?                               │
│                                    ┌────┴────┐                          │
│                                   是         否                          │
│                                    │         │                          │
│                                    ▼         ▼                          │
│                               临时history  attempt_                     │
│                               +递归        autosubmission()             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 配置体系对比

### 配置结构

| 项目 | 配置位置 | 关键字段 | 默认值 |
|-----|---------|---------|--------|
| **Codex** | `mcp-server.toml` | `startup_timeout_sec`, `tool_timeout_sec` | 30s / 10min |
| **Gemini CLI** | `.gemini/config.json` | `mcpServers[].timeout` | 10min |
| **Kimi CLI** | `~/.kimi/config.yaml` | `tool_defaults.shell.timeout` | 60s |
| **OpenCode** | `opencoder.json` | `tools.bash.defaultTimeout` | 5min |
| **SWE-agent** | `sweagent.yml` | `agent.execution_timeout` | 30s |

### 配置示例对比

```toml
# Codex: 分层配置（启动 vs 执行）
[[mcp_servers]]
name = "filesystem"
startup_timeout_sec = 30      # 服务启动
tool_timeout_sec = 600        # 工具执行
```

```json
// Gemini CLI: 服务器级统一配置
{
  "mcpServers": [{
    "id": "filesystem",
    "timeout": 600000  // 仅工具执行超时
  }]
}
```

```yaml
# Kimi CLI: 工具类型级默认
tool_defaults:
  shell:
    timeout: 60  # 保守默认值
```

```typescript
// OpenCode: 工具参数级（Zod Schema）
{
  command: z.string(),
  timeout: z.number().optional()  // 每次调用可指定
}
```

```yaml
# SWE-agent: 双层保护
agent:
  execution_timeout: 30          # 单命令
  total_execution_timeout: 3600  # 总会话
```

---

## 执行流程对比

### Codex: CancellationToken 异步取消

```
McpServerConfig
    │
    ├── startup_timeout_sec: 30s
    └── tool_timeout_sec: 600s
         │
         ▼
    tokio::time::timeout(Duration, future)
         │
         ├── 完成 ──▶ Ok(result)
         │
         └── 超时 ──▶ Err(Elapsed)
              │
              ▼
         cancel_token.cancel()
              │
              ▼
         abort_all_tasks()
              │
              ▼
         EventMsg::Error { code: ExecutionTimeout }
```

### Gemini CLI: Scheduler 状态机

```
MCPServerConfig { timeout: 600000 }
    │
    ▼
ToolScheduler
    │
    ├── schedule() ──▶ state: Validating
    │
    ├── validatePermission() ──▶ state: Scheduled
    │
    ├── processQueue() ──▶ state: Executing
    │
    └── Promise.race([
            callTool(),
            timeoutPromise(600000)
        ])
            │
            ├── 完成 ──▶ state: Success
            │
            └── 超时 ──▶ state: Error
```

### Kimi CLI: Checkpoint 联动回滚

```
timeout: int = 60 (默认)
    │
    ▼
checkpoint_manager.create()  # 创建快照
    │
    ▼
asyncio.wait_for(process, timeout=60)
    │
    ├── 完成 ──▶ checkpoint.commit()
    │
    └── 超时 ──▶ process.kill()
                  │
                  ▼
           checkpoint.rollback()  # 状态恢复
                  │
                  ▼
           ToolError(error_type="timeout")
```

### OpenCode: 动态超时重置

```
timeout: number (ms)
    │
    ▼
mcpClient.callTool({ resetTimeoutOnProgress: true })
    │
    ├── 正常执行 ──▶ success
    │
    └── 收到 progress 通知
          │
          ▼
    clearTimeout() + setTimeout()  # 重置计时器
          │
          └── 循环直到完成
```

### SWE-agent: 可重采样错误恢复

```
execution_timeout: 30s
    │
    ▼
subprocess.run(timeout=30)
    │
    ├── 完成 ──▶ 继续
    │
    └── TimeoutExpired
          │
          ▼
    forward_with_handling()
          │
          ├── 构造临时 history
          │
          ├── retry_count < max_retries?
          │       ├── 是 ──▶ 递归重试
          │       └── 否 ──▶ attempt_autosubmission()
          │
          └── 返回结果
```

---

## 数据结构对比

### 超时配置结构

| 项目 | 结构 | 字段说明 |
|-----|------|---------|
| **Codex** | `McpServerConfig` | `startup_timeout_sec: Option<Duration>`, `tool_timeout_sec: Option<Duration>` |
| **Gemini CLI** | `MCPServerConfig` | `timeout?: number` |
| **Kimi CLI** | `tool.parameters` | `timeout: { type: "integer", default: 60 }` |
| **OpenCode** | `BashToolSchema` | `timeout: z.number().optional()` |
| **SWE-agent** | `AgentConfig` | `execution_timeout: int`, `total_execution_timeout: int` |

### 超时错误类型

| 项目 | 错误类型 | 关键字段 |
|-----|---------|---------|
| **Codex** | `EventMsg::Error` | `code: ErrorCode::ExecutionTimeout`, `message: String` |
| **Gemini CLI** | `ToolExecution` | `state: ToolExecutionState.Error`, `error: Error` |
| **Kimi CLI** | `ToolError` | `error_type: "timeout"`, `recoverable: bool` |
| **OpenCode** | `ToolResult.error` | `type: "timeout" \| "mcp_timeout"`, `timeout: number` |
| **SWE-agent** | `ExecutionResult` | `timed_out: bool`, `exit_code: -1` |

---

## 取消机制对比

| 项目 | 技术实现 | 触发方式 | 影响范围 |
|-----|---------|---------|---------|
| **Codex** | `tokio::sync::CancellationToken` | `cancel_token.cancel()` | 当前 turn 的所有任务 |
| **Gemini CLI** | `AbortController` + 状态机 | `cancelAll()` | 当前及排队中的工具 |
| **Kimi CLI** | `asyncio.CancelledError` | 超时或用户中断 | 当前工具调用 |
| **OpenCode** | `AbortController` | 超时或进度重置 | 当前执行 |
| **SWE-agent** | `subprocess.kill()` | 超时或总时长超限 | 当前命令 |

---

## 超时后行为对比

| 项目 | 超时后动作 | 会话状态 | 恢复机制 |
|-----|-----------|---------|---------|
| **Codex** | 发送 `EventMsg::Error` | 保持 | 用户继续对话 |
| **Gemini CLI** | `state = Error` | 保持 | Scheduler 调度下一任务 |
| **Kimi CLI** | `ToolError` + 回滚 | 保持 | Checkpoint 恢复状态 |
| **OpenCode** | 返回 error 给 LLM | 保持 | LLM 自我纠正 |
| **SWE-agent** | 构造反馈 history | 保持 | `forward_with_handling()` 重试 |

---

## 设计权衡分析

### 超时粒度选择

| 策略 | 代表项目 | 优点 | 缺点 |
|-----|---------|------|------|
| **启动+执行分离** | Codex | 慢启动服务不影响执行 | 配置复杂 |
| **单层工具级** | Gemini CLI, Kimi CLI | 简单直观 | 无法区分启动问题 |
| **单命令+总时长** | SWE-agent | 防止无限运行 | 可能过早终止 |
| **动态重置** | OpenCode | 适应长任务 | 依赖 progress 上报 |

### 默认值策略

| 项目 | 默认值 | 策略倾向 | 适用场景 |
|-----|--------|---------|---------|
| **Codex** | 10min | 宽松 | 通用企业场景 |
| **Gemini CLI** | 10min | 宽松 | 交互式使用 |
| **Kimi CLI** | 60s | 保守 | 快速响应，状态敏感 |
| **OpenCode** | 5min | 中等 | Web 开发 |
| **SWE-agent** | 30s | 保守 | 自动化评测 |

### 超时与系统耦合度

| 项目 | 耦合模块 | 设计意图 |
|-----|---------|---------|
| **Codex** | 安全沙箱 + 事件系统 | 企业级安全 |
| **Gemini CLI** | Scheduler 状态机 | 可控的用户体验 |
| **Kimi CLI** | Checkpoint 系统 | 状态一致性 |
| **OpenCode** | MCP 进度通知 | 长任务友好 |
| **SWE-agent** | History Processors | 学术研究可重复性 |

---

## 选型建议

### 按场景推荐

| 场景 | 推荐项目 | 理由 |
|-----|---------|------|
| **企业级生产** | Codex | Rust 安全 + CancellationToken 可靠 |
| **交互式 CLI** | Gemini CLI | 状态机可视化 + cancelAll 控制 |
| **状态敏感任务** | Kimi CLI | Checkpoint 回滚保证一致性 |
| **长任务处理** | OpenCode | resetTimeoutOnProgress 避免误判 |
| **自动化评测** | SWE-agent | 可重采样错误 + 兜底提交 |

### 设计迁移建议

**如果你在实现一个 Agent 系统，可以参考：**

1. **基础超时框架**：参考 Codex 的 `CancellationToken` + 分层配置
2. **用户可控性**：参考 Gemini CLI 的 `cancelAll()` + Scheduler 状态机
3. **状态安全**：参考 Kimi CLI 的 Checkpoint 联动回滚
4. **长任务支持**：参考 OpenCode 的 `resetTimeoutOnProgress`
5. **错误恢复**：参考 SWE-agent 的 `forward_with_handling()` 重试策略

---

## 总结

五个项目的超时机制设计反映了不同的产品定位和技术哲学：

1. **Codex**（Rust）：基础设施思维，强调安全性和可靠性
2. **Gemini CLI**（TypeScript）：用户体验优先，状态机提供可视性
3. **Kimi CLI**（Python）：状态一致性优先，Checkpoint 联动设计
4. **OpenCode**（TypeScript）：现代化 Web 开发，动态机制适应异步场景
5. **SWE-agent**（Python）：学术研究导向，可重复性和自动化评测支持

---

> **版本信息**：基于各项目 2026-02-08 版本源码
