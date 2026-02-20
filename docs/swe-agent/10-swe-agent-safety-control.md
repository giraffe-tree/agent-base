# Safety Control（SWE-agent）

结论先行：`SWE-agent` 的 safety-control 主要依赖“配置驱动命令过滤 + 容器化执行边界 + 失败恢复策略”。它的默认模式偏自动化，不是逐命令人工审批模型；人工控制更多体现在 human step-in/out。

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

## 2. SWE-agent 项目级控制链路

```text
[模型动作文本 Model Action Text]
              |
              v
[动作解析 Action Parser]
              |
              v
[过滤检查 Blocklist / Regex Guard]
              |
              v
      +----------------------------------+
      | 阻断或放行 Blocked or Allowed    |
      +-------------+--------------------+
                    |
        +-----------+-----------+
        |                       |
        v                       v
[阻断后重问 Requery]   [容器内执行 Runtime in Container]
                                |
                                v
                   +-----------------------------+
                   | 超时/错误? Timeout or Error |
                   +-------------+---------------+
                                 |
                     +-----------+-----------+
                     |                       |
                     v                       v
             [步骤完成 Step Done]   [自动提交或中止 Autosubmit/Abort]
                     |                       |
                     +-----------+-----------+
                                 v
                       [进入下一步 Next Step]
```

---

## 3. 策略来源与覆盖关系

- 入口配置来自：
  - `config/default.yaml`
  - 额外 `--config`
  - CLI 参数覆盖（由 `BasicCLI` 合并）
- 工具安全相关默认策略在 `ToolFilterConfig/ToolConfig`：
  - `blocklist`
  - `blocklist_standalone`
  - `block_unless_regex`
  - 命令超时与连续超时限制

关键点：SWE-agent 的安全控制首先是一组“可配置过滤策略”，再由运行时执行边界兜底。

---

## 4. 执行前检查与限制

- Parser 层：先把模型输出解析为工具动作，确保格式满足工具调用约束。
- Guard 层：`should_block_action()` 在真正执行前拦截命令。
  - 前缀阻断（如交互编辑器/长驻命令等）
  - 独立词阻断（如裸 `python/bash/sh/su`）
  - 白名单 regex 例外（命中才允许）
- Multiline guard：规整多行输入，减少 heredoc/终止符错误带来的执行偏差。

---

## 5. 容器边界与失败处理

- 容器边界：
  - 默认 `DockerDeploymentConfig`，命令通过 runtime session 在容器内执行。
  - repo 通常复制/挂载到容器内路径进行操作，避免直接污染宿主工程目录。
- 失败恢复：
  - 动作被阻断、bash 语法错误、输出格式问题会触发 requery（有上限）。
  - timeout、环境异常、上下文超限、成本超限等走 autosubmission 或中止路径。
  - runtime 崩溃时尝试从最后 diff 提取可提交结果，降低全量失败损失。

---

## 6. 人工控制边界

- 默认是自动循环执行，不会对每条命令都弹窗确认。
- 支持 human 模式（step-in/out），用于人工接管关键步骤。
- 因此它更像“自动 agent + 人工可接管”，而非“强审批式 agent”。

---

## 7. 证据索引（项目名 + 文件路径 + 关键职责）

- `SWE-agent` + `SWE-agent/pyproject.toml` + CLI 入口脚本定义。
- `SWE-agent` + `SWE-agent/sweagent/run/run.py` + run/batch/replay/shell 命令分发。
- `SWE-agent` + `SWE-agent/sweagent/run/common.py` + 默认配置与 CLI 覆盖合并。
- `SWE-agent` + `SWE-agent/config/default.yaml` + 默认 agent/tools/model 配置基线。
- `SWE-agent` + `SWE-agent/sweagent/tools/tools.py` + blocklist/regex 过滤与工具执行约束。
- `SWE-agent` + `SWE-agent/sweagent/tools/parsing.py` + 动作解析与工具调用格式约束。
- `SWE-agent` + `SWE-agent/sweagent/agent/agents.py` + 主 loop、阻断后 requery、异常分流与退出语义。
- `SWE-agent` + `SWE-agent/sweagent/environment/swe_env.py` + runtime 会话执行与容器环境交互。
- `SWE-agent` + `SWE-agent/sweagent/environment/repo.py` + 仓库复制/reset 与本地脏仓保护。
- `SWE-agent` + `SWE-agent/sweagent/agent/extra/shell_agent.py` + human step-in/out 人工介入能力。

---

## 8. 适用边界

- 优势：自动化效率高，容器边界清晰，异常兜底路径完整。
- 局限：对高风险命令的确认机制偏“预设过滤+人工接管”，不是细粒度逐命令审批。
