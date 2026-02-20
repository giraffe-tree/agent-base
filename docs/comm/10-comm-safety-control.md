# 安全控制机制对比

## 1. 概念定义

**安全控制（Safety Control）** 是 Agent CLI 保护用户系统和数据安全的重要机制，用于限制 Agent 的操作范围，防止恶意或意外的破坏性行为。

### 核心要素

- **沙箱（Sandbox）**：隔离执行环境，限制资源访问
- **权限确认（Approval）**：危险操作前的用户确认
- **命令过滤（Command Filter）**：禁止或限制特定命令
- **网络隔离（Network Isolation）**：控制网络访问权限
- **文件系统保护（Filesystem Protection）**：限制文件读写范围

---

## 2. 各 Agent 实现

### 2.1 SWE-agent

**实现概述**

SWE-agent 使用 **Docker 沙箱 + 命令过滤** 的双重保护机制。通过容器隔离执行环境，同时提供命令级别的过滤能力。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Host System (宿主机)                                     │
│  ┌─────────────────────────────────────────────────────┐│
│  │ Docker Container (容器)                             ││
│  │ ├── Isolated Filesystem       隔离文件系统          ││
│  │ ├── Resource Limits             资源限制            ││
│  │ └── Network (可选)              网络控制            ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Command Filter (命令过滤层)                              │
│  ├── filter blocklist           阻止列表              │
│  ├── require explicit confirmation 需确认命令         │
│  └── logging                    审计日志              │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Docker Env | `sweagent/environment/swe_env.py` | 1 | 容器管理 |
| Command Filter | `sweagent/tools/tools.py` | 280 | 命令过滤 |
| Config | `sweagent/tools/config.py` | 50 | 安全配置 |

**配置示例**

```yaml
# 命令过滤配置
filter:
  blocklist:
    - "rm -rf /"
    - "mkfs.*"
  require_confirmation:
    - "rm .*"
    - "chmod .*"
```

### 2.2 Codex

**实现概述**

Codex 使用 **macOS Seatbelt/Landlock + 网络沙箱** 的多层安全架构。通过操作系统级沙箱限制资源访问，同时提供灵活的网络隔离策略。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Sandbox Layer (沙箱层)                                   │
│  ┌─────────────────────────────────────────────────────┐│
│  │ macOS Seatbelt / Linux Landlock                     ││
│  │ ├── Filesystem readonly         文件系统只读        ││
│  │ ├── Filesystem writeable        可写目录            ││
│  │ └── Process restrictions        进程限制            ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Network Sandbox (网络沙箱)                               │
│  ├── full                       完全网络访问          │
│  ├── host-only                  仅本地网络            │
│  └── none                       无网络访问            │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  tool_call_gate (工具调用门控)                            │
│  ├── is_mutating()              变异检测              │
│  └── 用户确认 (仅 when 需要)                          │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Sandbox | `codex-rs/core/src/sandbox/` | - | 沙箱实现 |
| Seatbelt | `codex-rs/core/src/sandbox/seatbelt.rs` | 1 | macOS 沙箱 |
| Landlock | `codex-rs/core/src/sandbox/landlock.rs` | 1 | Linux 沙箱 |
| Network | `codex-rs/core/src/sandbox/network.rs` | 50 | 网络策略 |
| Gate | `codex-rs/core/src/tools/execution.rs` | 100 | 工具门控 |

**沙箱配置**

```rust
// Sandbox 配置
pub struct SandboxConfig {
    pub sandbox: SandboxType,        // seatbelt / landlock / none
    pub network: NetworkMode,        // full / host-only / none
    pub cwd: PathBuf,                // 工作目录
    pub readonly: Vec<PathBuf>,      // 只读路径
    pub writeable: Vec<PathBuf>,     // 可写路径
}
```

### 2.3 Gemini CLI

**实现概述**

Gemini CLI 使用 **策略引擎 + 分层权限** 的安全模型。根据工具 Kind 分类进行权限控制，支持细粒度的用户确认策略。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Policy Engine (策略引擎)                                 │
│  ├── allowOnFirstUse            首次使用允许          │
│  ├── requireApproval            需要确认              │
│  └── deny                       拒绝                  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Tool Kind (工具分类)                                     │
│  ├── Read (0)                   读取操作              │
│  ├── Write (1)                  写入操作              │
│  ├── Mutate (2)                 变异操作              │
│  └── Execute (3)                执行操作              │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  User Confirmation (用户确认)                             │
│  ├── 命令行确认 (Y/n)                                   │
│  ├── IDE 弹窗确认                                       │
│  └── 可记住选择                                         │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Kind | `packages/core/src/tools/kind.ts` | 1 | 工具分类 |
| Policy | `packages/core/src/approval/` | - | 策略引擎 |
| Confirmation | `packages/core/src/approval/confirm.ts` | 50 | 确认逻辑 |

**策略配置**

```typescript
// 权限策略配置
const policy: Policy = {
  [Kind.Read]: 'allowOnFirstUse',
  [Kind.Write]: 'requireApproval',
  [Kind.Mutate]: 'requireApproval',
  [Kind.Execute]: 'requireApproval',
};
```

### 2.4 Kimi CLI

**实现概述**

Kimi CLI 使用 **简单确认机制 + D-Mail 回滚** 的安全策略。通过用户确认控制危险操作，支持通过检查点回滚到安全状态。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Tool Execution (工具执行)                                │
│  ├── 解析工具调用                                       │
│  ├── 检查是否需要确认                                   │
│  └── 执行并返回结果                                     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Simple Approval (简单确认)                               │
│  ├── 危险命令列表               预定义列表            │
│  ├── 用户确认 (Y/n)                                     │
│  └── 超时处理                                           │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Checkpoint (检查点)                                      │
│  ├── checkpoint()               创建检查点            │
│  └── revert_to(id)              回滚到检查点          │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Approval | `kimi-cli/src/kimi_cli/agent/kosong.py` | 200 | 确认逻辑 |
| Checkpoint | `kimi-cli/src/kimi_cli/checkpoint.py` | 1 | 检查点系统 |
| Dangerous | `kimi-cli/src/kimi_cli/tools/shell.py` | 50 | 危险命令检测 |

### 2.5 OpenCode

**实现概述**

OpenCode 使用 **PermissionNext 系统 + 模式匹配** 的细粒度权限控制。支持基于命令模式的动态权限管理，可配置自动允许、确认或拒绝策略。

**架构图**

```
┌─────────────────────────────────────────────────────────┐
│  Permission System (权限系统)                             │
│  ├── PermissionMode                                             │
│  │   ├── allow                 允许                     │
│  │   ├── deny                  拒绝                     │
│  │   └── ask                   询问                     │
│  └── PermissionNext                                       │
│      ├── mode                 当前模式                  │
│      ├── patterns             匹配模式                  │
│      └── remember()           记住选择                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Pattern Matching (模式匹配)                              │
│  ├── 命令模式匹配               bash:rm *               │
│  ├── 文件路径匹配               file:write /etc/*       │
│  └── 工具类型匹配               tool:bash               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ctx.ask() (运行时确认)                                   │
│  ├── 构建权限请求                                       │
│  ├── 等待用户响应                                       │
│  └── 缓存结果                                           │
└─────────────────────────────────────────────────────────┘
```

**关键代码位置**

| 组件 | 文件路径 | 行号 | 说明 |
|------|----------|------|------|
| Permission | `packages/opencode/src/permission/` | - | 权限系统 |
| PermissionNext | `packages/opencode/src/permission/types.ts` | 1 | 类型定义 |
| ctx.ask | `packages/opencode/src/tool/context.ts` | 100 | 运行时确认 |
| Config | `packages/opencode/src/config/permissions.ts` | 50 | 权限配置 |

**权限配置示例**

```typescript
// 权限配置
export const permissions: PermissionConfig = {
  bash: {
    mode: 'ask',
    patterns: [
      { pattern: 'rm -rf /', mode: 'deny' },
      { pattern: 'rm *', mode: 'ask' },
      { pattern: 'ls *', mode: 'allow' },
    ]
  },
  file: {
    mode: 'ask',
    patterns: [
      { pattern: '/etc/*', mode: 'ask' },
      { pattern: '~/.opencode/*', mode: 'allow' },
    ]
  }
};
```

---

## 3. 相同点总结

### 3.1 通用安全原则

| 原则 | 说明 |
|------|------|
| 最小权限 | 只授予必要的权限 |
| 用户确认 | 危险操作前需要确认 |
| 可审计 | 记录操作日志 |
| 可回滚 | 支持恢复到安全状态 |

### 3.2 通用保护能力

| 保护维度 | 说明 |
|----------|------|
| 文件系统 | 限制读写范围 |
| 网络访问 | 控制网络权限 |
| 命令执行 | 过滤危险命令 |
| 资源使用 | 限制 CPU/内存 |

### 3.3 用户确认流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Agent     │────▶│   检测风险   │────▶│   请求确认   │
│  执行工具   │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
       ▲                                       │
       │                                       ▼
       │                              ┌─────────────┐
       │                              │  用户确认   │
       │                              │  (Y/n)      │
       │                              └──────┬──────┘
       │                                     │
       └─────────────────────────────────────┘
                    执行/拒绝
```

---

## 4. 不同点对比

### 4.1 沙箱机制

| Agent | 沙箱类型 | 实现方式 | 平台支持 |
|-------|----------|----------|----------|
| SWE-agent | Docker 容器 | 完整系统隔离 | 跨平台 |
| Codex | Seatbelt/Landlock | 系统调用过滤 | macOS/Linux |
| Gemini CLI | 无内置 | 依赖操作系统 | - |
| Kimi CLI | 无内置 | 依赖操作系统 | - |
| OpenCode | 无内置 | 依赖操作系统 | - |

### 4.2 权限确认机制

| Agent | 确认粒度 | 确认方式 | 可配置性 |
|-------|----------|----------|----------|
| SWE-agent | 命令级 | 预定义规则 | 配置文件 |
| Codex | 操作级 | is_mutating | 启动参数 |
| Gemini CLI | 工具级 | Kind 分类 | 策略配置 |
| Kimi CLI | 命令级 | 简单列表 | 内置规则 |
| OpenCode | 模式级 | 正则匹配 | 灵活配置 |

### 4.3 网络隔离

| Agent | 网络控制 | 实现方式 |
|-------|----------|----------|
| SWE-agent | 可选 | Docker 网络模式 |
| Codex | 完整支持 | 自定义网络沙箱 |
| Gemini CLI | 无 | 依赖系统防火墙 |
| Kimi CLI | 无 | 依赖系统防火墙 |
| OpenCode | 无 | 依赖系统防火墙 |

### 4.4 文件系统保护

| Agent | 保护方式 | 粒度 |
|-------|----------|------|
| SWE-agent | Docker 挂载 | 目录级 |
| Codex | Seatbelt/Landlock | 路径级 |
| Gemini CLI | 无内置 | - |
| Kimi CLI | 无内置 | - |
| OpenCode | 无内置 | - |

### 4.5 回滚能力

| Agent | 回滚支持 | 实现方式 |
|-------|----------|----------|
| SWE-agent | 否 | - |
| Codex | 否 | - |
| Gemini CLI | 否 | - |
| Kimi CLI | 是 | Checkpoint 系统 |
| OpenCode | 否 | - |

---

## 5. 源码索引

### 5.1 沙箱实现

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/environment/swe_env.py` | 1 | Docker 容器管理 |
| Codex | `codex-rs/core/src/sandbox/` | - | 沙箱目录 |
| Codex | `codex-rs/core/src/sandbox/seatbelt.rs` | 1 | macOS Seatbelt |
| Codex | `codex-rs/core/src/sandbox/landlock.rs` | 1 | Linux Landlock |

### 5.2 权限确认

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/tools/tools.py` | 280 | 命令过滤 |
| Codex | `codex-rs/core/src/tools/execution.rs` | 100 | tool_call_gate |
| Gemini CLI | `packages/core/src/approval/` | - | 策略引擎 |
| Gemini CLI | `packages/core/src/tools/kind.ts` | 1 | 工具分类 |
| Kimi CLI | `kimi-cli/src/kimi_cli/agent/kosong.py` | 200 | 确认逻辑 |
| OpenCode | `packages/opencode/src/permission/` | - | 权限系统 |
| OpenCode | `packages/opencode/src/tool/context.ts` | 100 | ctx.ask |

### 5.3 配置管理

| Agent | 文件路径 | 行号 | 说明 |
|-------|----------|------|------|
| SWE-agent | `sweagent/tools/config.py` | 50 | 安全配置 |
| Codex | `codex-rs/core/src/config/` | - | 配置模块 |
| Gemini CLI | `packages/core/src/config/` | - | 配置管理 |
| OpenCode | `packages/opencode/src/config/permissions.ts` | 50 | 权限配置 |

---

## 6. 选择建议

| 场景 | 推荐 Agent | 理由 |
|------|-----------|------|
| 最高安全要求 | Codex | Seatbelt/Landlock + 网络沙箱 |
| 完全隔离环境 | SWE-agent | Docker 容器隔离 |
| 灵活权限策略 | OpenCode | PermissionNext 模式匹配 |
| IDE 集成安全 | Gemini CLI | Kind 分类 + 策略引擎 |
| 可回滚保护 | Kimi CLI | Checkpoint 系统 |

---

## 7. 补充：沙箱技术对比

### 7.1 沙箱技术概览

| 技术 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| Docker | 容器化隔离 | 完整系统隔离 | 性能开销 |
| Seatbelt | macOS 沙箱 | 系统原生 | macOS 专属 |
| Landlock | Linux LSM | 轻量级 | Linux 专属 |
| seccomp | 系统调用过滤 | 精细控制 | 配置复杂 |
| gVisor | 用户态内核 | 高安全性 | 性能损耗 |

### 7.2 安全建议

**高安全场景**

```
推荐组合：
- Codex (Seatbelt/Landlock + 网络沙箱)
- 配合 Docker 进行完全隔离
- 使用 readonly 文件系统
```

**便捷性优先**

```
推荐组合：
- OpenCode (灵活的 PermissionNext)
- Gemini CLI (IDE 集成确认)
- Kimi CLI (简单确认 + 回滚)
```

