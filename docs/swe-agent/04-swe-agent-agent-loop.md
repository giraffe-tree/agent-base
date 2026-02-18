# Agent Loop（SWE-agent）

本文基于 `./SWE-agent`（重点 `sweagent/agent/agents.py` 与 `sweagent/run/run_single.py`）源码，解释 SWE-agent 如何把「问题输入 -> 模型决策 -> 环境执行 -> 结果回注 -> 直到提交」组织成可控的 Agent Loop。  
为适配“先看全貌再看细节”的阅读习惯，先给流程图和阅读路径，再展开实现细节。

---

## 1. 先看全局（流程图）

### 1.1 主路径流程图（DefaultAgent）

```text
+--------------------------+
| RunSingle.run            |
| - env.start()            |
| - agent.run(...)         |
+------------+-------------+
             |
             v
+--------------------------+
| DefaultAgent.run         |
| - setup(...)             |
| - on_run_start()         |
+------------+-------------+
             |
             v
      +------+------+
      | while !done |
      +------+------+
             |
             v
+-------------------------------+
| step()                        |
| - forward_with_handling(...)  |
| - add_step_to_history         |
| - add_step_to_trajectory      |
| - save_trajectory             |
+--------------+----------------+
               |
               v
+-------------------------------+
| forward_with_handling         |
| - forward(query model)        |
| - parse thought/action        |
| - handle_action(env exec)     |
| - errors: requery or autosub  |
+--------------+----------------+
               |
               v
      +--------+---------+
      | step_output.done |
      +--------+---------+
               |Yes                   |No
               v                      v
+--------------------------+   +------------------------+
| on_run_done + return     |   | continue next step     |
| AgentRunResult           |   | (history + trajectory) |
+--------------------------+   +------------------------+
```

### 1.2 关键分支流程图（重试、提交、异常）

```text
[A] 单步里的重采样分支（forward_with_handling）

forward()
  |
  +-- FormatError / BlockedAction / BashSyntax
  |      -> 组装错误模板
  |      -> requery 模型（最多 max_requeries）
  |
  +-- _RetryWithOutput / _RetryWithoutOutput
  |      -> 继续重采样（可带/不带上一步输出）
  |
  +-- 其他致命错误（context/cost/runtime/env/...）
         -> attempt_autosubmission_after_error()
         -> done = true


[B] 提交分支（handle_submission）

observation 或 force_submission
  |
  +-- 命中 submit 信号
  |      -> 读取 /root/model.patch
  |      -> step.submission = patch
  |      -> step.done = true
  |      -> exit_status = submitted(...)
  |
  +-- 未命中
         -> 正常继续下一步


[C] RetryAgent 外层循环

RetryAgent.run
  |
  +-- setup 第 0 次 attempt
  |
  +-- while !done:
        step()
        save_trajectory(choose=false)
        if done:
          rloop.on_submit(...)
          rloop.retry() ?
            Yes -> env.hard_reset + next attempt + done=false
            No  -> choose best attempt, 结束
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道 loop 在哪一层、何时结束）。
- **3 分钟版**：看 `1.1` + `1.2` + `4~6`（知道重采样、执行、提交、异常）。
- **10 分钟版**：通读 `3~9`（能定位大多数“为什么停/为什么没提交”问题）。

### 2.1 一句话定义

SWE-agent 的 Agent Loop 是“**step 级动作-观察循环 +（可选）attempt 级重试循环**”：  
每个 step 都是“模型输出动作 -> 环境执行 -> 观察回注”；当出现 submission 或退出信号时结束当前 attempt；若启用 `RetryAgent`，再由 reviewer/chooser 决定是否开新 attempt。

---

## 3. 入口与分层

核心分层如下：

- **`RunSingle.run()`**（`sweagent/run/run_single.py`）：运行器入口，负责环境生命周期与 hooks。
- **`DefaultAgent.run()`**（`sweagent/agent/agents.py`）：单 attempt 主循环（`while not done`）。
- **`DefaultAgent.step()`**：单步编排，负责调用模型、执行动作、落历史和轨迹。
- **`forward_with_handling()` / `forward()`**：模型采样、动作解析、错误重采样。
- **`handle_action()`**：把 action 送进 `SWEEnv` 执行，拿 observation 并检查 submission。
- **`RetryAgent.run()`**：attempt 外层循环（多次尝试 + review/choose 最优）。

### 3.1 从 run 到 loop

默认单实例路径是：

1. `RunSingle.run()` 启动环境 `env.start()`；
2. 调 `agent.run(problem_statement, env, output_dir)`；
3. `DefaultAgent.setup()` 初始化 system/demo/instance 消息；
4. 进入 `while not step_output.done` 的 step 循环；
5. 每步后持久化 `.traj`，最终返回 `AgentRunResult`。

---

## 4. `DefaultAgent.run()`：step 级主循环

`DefaultAgent.run()` 本身很薄，主职责是把生命周期围起来：

1. `setup(...)`：安装工具、写 system/demo/instance 初始历史；
2. `on_run_start` hook；
3. `while not done` 重复 `step()`；
4. 每步调用 `save_trajectory()`；
5. `on_run_done` hook，返回最终 `info + trajectory`。

`done=true` 的典型来源：

- action 为 `exit`；
- 观测中检测到 submission（或强制 submission）；
- 致命错误后走 autosubmission；
- 达到某些退出条件（如总执行时长、连续超时等）并转换为退出。

---

## 5. 单步循环：`step()` -> `forward_with_handling()` -> `handle_action()`

### 5.1 `step()`（编排层）

`step()` 负责“串起来并落账”：

- 调 `forward_with_handling(self.messages)` 得到 `StepOutput`；
- 把 thought/action/observation 追加到 history；
- 更新 `info`（`submission`、`exit_status`、`model_stats`、edited files）；
- 追加 trajectory step。

也就是说，`step()` 是每轮 loop 的状态提交点。

### 5.2 `forward()`（采样与动作解析）

`forward()` 做单次“纯前向”：

1. query 模型（或 action sampler）；
2. `tools.parse_actions(output)` 解析出 `thought + action`；
3. 调 `handle_action(step)` 在环境中执行；
4. 成功返回完整 `StepOutput`，失败把当前 `step` 挂到异常上抛。

### 5.3 `handle_action()`（执行与观测）

执行阶段关键点：

- blocklist 命中直接抛 `_BlockedActionError`；
- `action == "exit"` 直接置 `done=true`；
- 否则 `env.communicate(...)` 执行命令，处理 timeout/interrupt；
- 拉取最新 `state` 并检测特殊 token（重试/forfeit）；
- 最后调用 `handle_submission()` 判定是否提交并结束。

---

## 6. 错误处理与重采样：`forward_with_handling()`

这层是 SWE-agent loop 的“韧性核心”。它在 `max_requeries` 范围内做恢复：

- **可重采样错误**：`FormatError`、`_BlockedActionError`、`BashIncorrectSyntaxError`、`ContentPolicyViolationError` 等；
- **重采样方式**：用错误模板构造临时 history，再次 query；
- **退出类错误**：context/cost/retry/runtime/environment 等，直接走 `attempt_autosubmission_after_error()`；
- **最终兜底**：连续重采样失败后，以 `exit_format` 退出并尝试 autosubmit。

关键语义：  
SWE-agent 不会因为“单次格式错”立刻中断，而是优先自修复；但命中硬性上限或致命错误时，会尽量提取 patch 后再结束。

---

## 7. 提交与收敛：`handle_submission()` / autosubmission

SWE-agent 的“结束”通常围绕 patch 提交：

1. 先检测是否出现 submit 信号（或强制提交）；
2. 从 `/root/model.patch` 读取补丁内容；
3. 写入 `step.submission`，并置 `done=true`；
4. 更新 `exit_status`（如 `submitted` / `submitted (exit_*)`）。

若运行时异常导致无法正常提交流程，`attempt_autosubmission_after_error()` 会尝试：

- 环境仍存活：执行 `git add -A && git diff --cached > /root/model.patch` 后再走提交；
- 环境已死：尝试从上一条 trajectory 的 `state["diff"]` 抢救 patch。

---

## 8. 外层重试循环：`RetryAgent.run()`

`RetryAgent` 在 DefaultAgent 外再套一层 attempt loop：

1. 初始化 retry loop（`ScoreRetryLoop` 或 `ChooserRetryLoop`）；
2. 跑一次 sub-agent（本质是 `DefaultAgent`）直到 done；
3. `on_submit(...)` 把本次 submission 交给 reviewer/chooser；
4. 若 `rloop.retry()` 为真：`env.hard_reset()` 后开下一次 attempt；
5. 最后 `choose=True` 汇总最佳 attempt 作为全局结果。

这让 SWE-agent 同时具备：

- **局部自修复**（step 内重采样）；
- **全局重试优化**（attempt 间比较与选优）。

---

## 9. 中断、上限与保护机制

Loop 运行过程中主要保护阈值：

- **重采样上限**：`max_requeries`，避免无限“格式修复循环”；
- **命令执行超时**：单命令 timeout + 连续超时上限；
- **总执行时长上限**：`total_execution_timeout`；
- **预算上限**：模型单次/总成本限制（触发 `exit_cost` 或 total cost 异常）；
- **Retry budget 与次数**：`max_attempts`、`cost_limit`、`min_budget_for_new_attempt`。

这些条件最终都会落到 `StepOutput.done` 或 retry loop 的 `retry()` 决策上，实现可预测收敛。

---

## 10. 排障速查

- **为什么没继续下一步**：看 `step_output.done` 是谁置为 `true`（exit/submission/错误退出）。
- **为什么模型一直重试**：看 `forward_with_handling()` 是否持续命中可重采样错误。
- **为什么没拿到 patch**：看 `/root/model.patch` 是否生成，及 autosubmission 分支是否触发。
- **为什么 attempt 没继续**：看 `rloop.retry()` 的预算/次数/accept 阈值判定。
- **为什么结果不是最后一次**：`RetryAgent` 会在结束时选择“最佳 attempt”返回。

---

## 11. 架构特点总结

- **双层循环**：内层 step loop 解决“当前尝试如何推进”，外层 retry loop 解决“多次尝试如何选优”。
- **执行中心化**：所有 action 最终走 `handle_action -> SWEEnv`，行为可统一管控。
- **错误韧性强**：先重采样修复，再 autosubmit 兜底，尽量减少“空跑退出”。
- **轨迹可复盘**：每步落 `history + trajectory + info`，并持续写 `.traj`。
- **工程化收敛**：通过 timeout/cost/requery/retry 多维阈值避免无界循环。
