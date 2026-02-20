# Safety Control（kimi-cli）

结论先行：`kimi-cli` 的 safety-control 核心是“审批状态机驱动的执行门控”。它不是基于复杂命令语义打分，而是以工具动作分类（shell/file/mcp）触发 `approve / approve_for_session / reject`，并在 agent loop 中把拒绝结果显式收敛为停止当前步骤。

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

## 2. kimi-cli 项目级控制链路

```text
[工具请求 Tool Request]
          |
          v
[动作分类 Action Classify]
          |
          v
[审批请求 Approval Request]
          |
          v
    +---------------------------------------------+
    | approve / approve_for_session / reject      |
    | 批准 / 会话内批准 / 拒绝                    |
    +-----------+-------------------+-------------+
                |                   |
        +-------+-----+      +------+-------------------+
        | approve     |      | reject                  |
        v             |      v                         |
[工具执行 Tool Execute]  [拒绝错误 Tool Rejected Error]
        |             |      |                         |
        +-------------+------+-------------------------+
                      v
              [步骤结果 Step Result]
                      |
                      v
     [继续或停止 Agent Loop Continue or Stop]
```

---

## 3. 策略来源与运行时开关

- CLI/配置层：
  - `--yolo`、`default_yolo` 决定是否绕过审批。
  - `/yolo` slash command 支持会话内动态切换。
- Soul 层：
  - `Approval` 保存本会话 `auto_approve_actions`。
  - `Approval.share()` 可把审批态在子 agent 共享。

关键点：`kimi-cli` 的安全策略更偏“交互式审批策略”，不是集中 TOML 规则引擎。

---

## 4. 执行前检查与用户确认

- 所有关键写操作都会在工具执行前请求审批：
  - shell：`run command`
  - 文件写入/替换：`edit file` 或 `edit file outside of working directory`
  - MCP：`mcp:<tool_name>`
- 三态确认：
  - `approve`：仅本次执行
  - `approve_for_session`：本会话同动作自动放行
  - `reject`：拒绝并返回 `ToolRejectedError`
- 审批通道覆盖：
  - 终端 UI（交互确认）
  - Wire（协议请求/回包）
  - ACP（权限请求/映射）

---

## 5. 权限边界与异常处理

- 沙箱边界：默认并非系统级容器沙箱；主要依赖工具层审批与路径边界控制。
- 文件边界：工作目录外写入会被升级为 `outside` 动作并单独审批。
- 异常路径：
  - 拒绝后 `ToolRejectedError` 进入 `_step()`，以 `tool_rejected` 语义结束当前步骤。
  - Wire/ACP 失败场景默认走 reject 或错误结果，避免悬空等待。
  - turn 结束清理 stale pending request，避免审批状态死锁。

---

## 6. 证据索引（项目名 + 文件路径 + 关键职责）

- `kimi-cli` + `kimi-cli/src/kimi_cli/cli/__init__.py` + `--yolo/--wire/--acp` 等入口参数与运行模式。
- `kimi-cli` + `kimi-cli/src/kimi_cli/config.py` + `default_yolo` 配置面。
- `kimi-cli` + `kimi-cli/src/kimi_cli/soul/kimisoul.py` + 主循环与拒绝后的步骤收敛。
- `kimi-cli` + `kimi-cli/src/kimi_cli/soul/approval.py` + 审批状态机、会话级自动放行集合。
- `kimi-cli` + `kimi-cli/src/kimi_cli/soul/toolset.py` + tool call 分发与 MCP 审批接入。
- `kimi-cli` + `kimi-cli/src/kimi_cli/tools/shell/__init__.py` + shell 执行前审批与超时处理。
- `kimi-cli` + `kimi-cli/src/kimi_cli/tools/file/write.py` + 文件写入审批与 outside 工作区动作区分。
- `kimi-cli` + `kimi-cli/src/kimi_cli/tools/file/replace.py` + 文本替换审批与 diff 展示。
- `kimi-cli` + `kimi-cli/src/kimi_cli/ui/shell/visualize.py` + 终端确认交互（once/always/reject）。
- `kimi-cli` + `kimi-cli/src/kimi_cli/wire/server.py` + Wire 协议审批桥接与异常清理。
- `kimi-cli` + `kimi-cli/src/kimi_cli/acp/session.py` + ACP `request_permission` 到审批结果映射。

---

## 7. 适用边界

- 优势：审批交互清晰、可解释性高，适合人机协作场景。
- 局限：危险识别主要按动作类型，不是命令语义级风险模型。

