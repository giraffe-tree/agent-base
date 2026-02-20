# Safety Control（opencode）

结论先行：`opencode` 的 safety-control 是“规则评估 + 执行前拦截 + 事件审批 + 路径边界”四段式控制。核心控制器是 `PermissionNext`，每次工具执行前都由 `ctx.ask()` 统一进入 `allow/ask/deny` 决策。

---

## 1. 跨项目统一安全控制流程图

```text
+--------------------------------------------+
| 用户请求 User Request                      |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 策略来源 Policy Source                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 执行前检查 Pre-Execution Check             |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 审批闸门 Approval Gate                     |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 工具执行 Tool Execution                    |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 边界/沙箱 Boundary and Sandbox Guard       |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 结果/错误 Result or Error                  |
+------------------------+-------------------+
                         |
                         v
+--------------------------------------------+
| 重试/中止/降级 Retry, Abort, or Fallback   |
+--------------------------------------------+
```

---

## 2. opencode 项目级控制链路

```text
[工具调用 Tool Call]
         |
         v
[统一询问 ctx.ask]
         |
         v
[规则评估 PermissionNext.evaluate]
         |
         v
    +-----------------------------------+
    | allow / ask / deny                |
    | 允许 / 询问 / 拒绝                |
    +--------+--------------+-----------+
             |              |
      +------+---+      +---+----------------------+
      | allow    |      | deny                    |
      v          |      v                         |
[执行工具 Execute]  [拒绝错误 DeniedError]
      ^          |                                |
      |          +-------------+------------------+
      |                        |
      |         [事件 permission.asked]
      |                        |
      |      [once/always/reject 审批回复]
      |                        |
      +------[permission.reply]+
               |
               v
       [结果处理 Session Processor]
```

---

## 3. 策略来源与优先级

- 规则模型：`{ permission, pattern, action }`，动作为 `allow | ask | deny`。
- 规则来源：
  - 默认 agent 规则
  - 用户配置 `permission`
  - agent frontmatter `permission`
  - session 级覆盖
  - 环境变量覆盖（如 `OPENCODE_PERMISSION`）
- 命中语义：按合并后的规则序列匹配，采用后匹配优先；无匹配默认 `ask`。

---

## 4. 执行前拦截与审批闭环

- 拦截入口：工具实现统一在执行前调用 `ctx.ask()`。
- bash 双重校验：
  - 命令级 `permission: bash`
  - 越界路径级 `permission: external_directory`
- 审批交互：
  - `ask` 决策会发布 `permission.asked` 事件并挂起调用。
  - TUI/API 回复 `once / always / reject` 后恢复或拒绝。
- `always` 语义：把当前模式写入运行时已批准集合，当前会话后续匹配可自动放行。

---

## 5. 权限边界与失败处理

- 路径边界：
  - `Project.fromDirectory()` 识别 `directory/worktree`。
  - `Instance.containsPath()` 判断路径是否越界。
  - 越界操作统一走 `external_directory` 审批。
- 失败语义：
  - `DeniedError`：策略拒绝，不执行。
  - `RejectedError`：用户拒绝。
  - `CorrectedError`：审批后修正执行路径。
- session processor 负责把权限错误写回消息并按配置决定是否继续循环。

---

## 6. 证据索引（项目名 + 文件路径 + 关键职责）

- `opencode` + `opencode/packages/opencode/src/permission/next.ts` + 权限规则评估与 ask/reply 状态机。
- `opencode` + `opencode/packages/opencode/src/session/prompt.ts` + `ctx.ask()` 注入工具执行上下文。
- `opencode` + `opencode/packages/opencode/src/tool/bash.ts` + bash AST 提取、命令与外部目录双重鉴权。
- `opencode` + `opencode/packages/opencode/src/tool/external-directory.ts` + 外部目录访问统一校验入口。
- `opencode` + `opencode/packages/opencode/src/project/project.ts` + 项目目录与 worktree 边界初始化。
- `opencode` + `opencode/packages/opencode/src/project/instance.ts` + 路径是否在实例边界内判定。
- `opencode` + `opencode/packages/opencode/src/server/routes/permission.ts` + 权限请求查询与回复 API。
- `opencode` + `opencode/packages/opencode/src/cli/cmd/tui/routes/session/permission.tsx` + TUI 审批交互实现。
- `opencode` + `opencode/packages/opencode/src/session/processor.ts` + 权限错误在 session loop 的处理策略。
- `opencode` + `opencode/packages/opencode/src/config/config.ts` + 权限配置 schema 与来源合并。

---

## 7. 适用边界

- 优势：审批闭环可追踪、路径边界明确、工具侧易扩展。
- 局限：路径边界不等同于系统级容器隔离；是否启用更强隔离取决于部署层与运行环境策略。

