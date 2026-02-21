# OpenCode 如何避免 Tool 无限循环调用

**结论先行**: OpenCode 通过 **Doom loop 检测** + **PermissionNext 权限系统** + **resetTimeoutOnProgress** 防止 tool 无限循环。核心设计是"行为模式检测"，通过配置化的权限规则检测重复工具调用模式并触发人工介入。

---

## 1. Doom Loop 检测机制

位于 `opencode/packages/opencode/src/agent/agent.ts`：

### 1.1 权限规则配置

```typescript
const defaults = PermissionNext.fromConfig({
  "*": "allow",
  doom_loop: "ask",  // ← 关键：doom loop 触发时询问用户
  external_directory: {
    "*": "ask",
    ...Object.fromEntries(whitelistedDirs.map((dir) => [dir, "allow"])),
  },
  question: "deny",
  plan_enter: "deny",
  plan_exit: "deny",
})
```

**核心设计**: `doom_loop: "ask"` 表示当检测到 doom loop 时，暂停执行并询问用户。

### 1.2 检测逻辑

```typescript
// 伪代码：基于最近工具调用历史检测
class DoomLoopDetector {
  private recentToolCalls: ToolCallRecord[] = []
  private readonly DETECTION_WINDOW = 3  // 检测最近 3 次调用

  detectDoomLoop(): boolean {
    const recent = this.recentToolCalls.slice(-this.DETECTION_WINDOW)

    // 检查是否重复调用相同工具且参数相似
    if (recent.length < this.DETECTION_WINDOW) return false

    const first = recent[0]
    return recent.every(call =>
      call.toolName === first.toolName &&
      this.areParamsSimilar(call.params, first.params)
    )
  }

  private areParamsSimilar(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
    // 比较参数相似度
    const aKeys = Object.keys(a).sort()
    const bKeys = Object.keys(b).sort()
    if (aKeys.join(',') !== bKeys.join(',')) return false

    // 允许值有轻微差异（如行号不同）
    for (const key of aKeys) {
      if (typeof a[key] === 'string' && typeof b[key] === 'string') {
        // 字符串相似度阈值
        if (similarity(a[key], b[key]) < 0.8) return false
      }
    }
    return true
  }
}
```

**检测策略**:
- 检测最近 3 次工具调用
- 检查是否为相同工具 + 相似参数
- 字符串参数使用相似度算法（如 Levenshtein 距离）

### 1.3 触发后的行为

```typescript
export const ask = fn(
  Request.partial({ id: true }).extend({ ruleset: Ruleset }),
  async (input) => {
    for (const pattern of request.patterns ?? []) {
      const rule = evaluate(request.permission, pattern, ruleset, s.approved)

      if (rule.action === "ask") {
        // 暂停执行，等待用户确认
        return new Promise<void>((resolve, reject) => {
          s.pending[id] = { info, resolve, reject }
          Bus.publish(Event.Asked, info)  // 通知 UI 层
        })
      }
    }
  }
)
```

**用户选项**:
- `once`: 仅本次允许继续
- `always`: 始终允许（持久化到数据库）
- `reject`: 拒绝并终止当前会话

---

## 2. PermissionNext 权限系统

位于 `opencode/packages/opencode/src/permission/next.ts`：

### 2.1 三种 Action 类型

```typescript
export const Action = z.enum(["allow", "deny", "ask"])

export const Rule = z.object({
  permission: z.string(),  // 权限名称（如 "doom_loop", "bash", "edit"）
  pattern: z.string(),     // 匹配模式
  action: Action,          // allow / deny / ask
})
```

### 2.2 权限评估逻辑

```typescript
export function evaluate(
  permission: string,
  pattern: string,
  ruleset: Ruleset,
  approved: Ruleset
): Rule {
  // 1. 检查已批准的规则
  for (const rule of approved) {
    if (Wildcard.match(permission, rule.permission) &&
        Wildcard.match(pattern, rule.pattern)) {
      return { ...rule, action: "allow" }
    }
  }

  // 2. 按规则优先级评估
  for (const rule of ruleset) {
    if (Wildcard.match(permission, rule.permission) &&
        Wildcard.match(pattern, rule.pattern)) {
      return rule
    }
  }

  // 3. 默认 deny
  return { permission, pattern, action: "deny" }
}
```

---

## 3. resetTimeoutOnProgress 长任务支持

位于 `opencode/packages/opencode/src/session/retry.ts`：

### 3.1 机制说明

```typescript
class TimeoutManager {
  private lastProgressTime: number
  private timeoutMs: number

  startTimeout(timeoutMs: number, onTimeout: () => void) {
    this.timeoutMs = timeoutMs
    this.lastProgressTime = Date.now()

    const check = () => {
      if (Date.now() - this.lastProgressTime > timeoutMs) {
        onTimeout()
      } else {
        setTimeout(check, 1000)
      }
    }
    setTimeout(check, 1000)
  }

  onProgress() {
    // 有进度时重置计时器
    this.lastProgressTime = Date.now()
  }
}
```

**应用场景**:
- 长时间运行的 bash 命令（如编译、测试）
- 大文件下载/上传
- 数据库迁移等批处理任务

---

## 4. 防循环流程图

```
┌─────────────────────────────────────────────────────────────────┐
│               OpenCode Tool 调用防循环流程                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   工具调用请求                                                   │
│        │                                                        │
│        ▼                                                        │
│   ┌───────────────────┐                                        │
│   │ PermissionNext    │                                        │
│   │ 权限评估           │                                        │
│   └─────────┬─────────┘                                        │
│             │                                                   │
│     ┌───────┼───────┬──────────┐                               │
│     ▼       ▼       ▼          ▼                               │
│   allow   deny      ask      doom_loop                         │
│     │       │        │          │                              │
│     ▼       ▼        ▼          ▼                              │
│   直接    拒绝      暂停      检测到循环                        │
│   执行    执行      等待      触发 ask                         │
│                     用户确认     │                              │
│                        │         │                              │
│                        ▼         ▼                              │
│                    ┌─────────────────┐                          │
│                    │ 用户选择        │                          │
│                    │ • once: 继续    │                          │
│                    │ • always: 始终  │                          │
│                    │ • reject: 终止  │                          │
│                    └─────────────────┘                          │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                    长任务执行阶段                        │   │
│   │                                                         │   │
│   │   bash 命令开始                                         │   │
│   │        │                                                │   │
│   │        ▼                                                │   │
│   │   ┌───────────────────┐                                │   │
│   │   │ 有输出?           │────是────▶ onProgress()        │   │
│   │   │ (进度)            │              重置计时器         │   │
│   │   └─────────┬─────────┘                                │   │
│   │             │否                                        │   │
│   │             ▼                                          │   │
│   │   ┌───────────────────┐                                │   │
│   │   │ 超时? (默认30s)   │────是────▶ 终止执行            │   │
│   │   └───────────────────┘                                │   │
│   │             │否                                         │   │
│   │             └────────────────────▶ 继续等待            │   │
│   │                                                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 与其他 Agent 的对比

| 防护机制 | OpenCode | Gemini CLI | Kimi CLI | Codex | SWE-agent |
|---------|----------|------------|----------|-------|-----------|
| **Doom loop 检测** | ✅ 3次触发 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **权限系统** | ✅ 可配置 | ✅ 策略驱动 | ✅ 危险命令 | ✅ 三档审批 | ❌ 无 |
| **人工介入** | ✅ ask/reject | ✅ 确认循环 | ✅ 审批 | ✅ 审批 | ❌ 无 |
| **进度超时** | ✅ resetTimeout | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **状态回滚** | ❌ 无 | ❌ 无 | ✅ Checkpoint | ❌ 无 | ❌ 无 |

---

## 6. 总结

OpenCode 的防循环设计哲学是**"模式检测 + 权限管控"**：

1. **Doom Loop 检测**: 基于最近 3 次工具调用检测重复模式
2. **权限介入**: `doom_loop: "ask"` 在关键点强制人工确认
3. **长任务支持**: `resetTimeoutOnProgress` 避免长时间任务被误杀
4. **持久化配置**: `always` 选项持久化用户选择到数据库

OpenCode 的独特之处在于将**循环检测与权限系统结合**，通过配置化规则灵活控制何时介入，既保证了防循环能力，又不过度干扰正常的长任务执行。

---

*文档版本: 2026-02-21*
*基于代码版本: opencode (baseline 2026-02-08)*
