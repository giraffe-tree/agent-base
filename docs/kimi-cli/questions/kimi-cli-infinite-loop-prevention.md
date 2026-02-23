# Kimi CLI 如何避免工具无限循环（校正版）

本文基于当前源码：
- `src/kimi_cli/config.py`
- `src/kimi_cli/soul/kimisoul.py`
- `src/kimi_cli/soul/denwarenji.py`

---

## 1. 结论

Kimi CLI 的主防线是“硬上限 + 有限重试”，不是自动循环检测器。

- 硬上限：`max_steps_per_turn`（默认 100）
- 有限重试：`max_retries_per_step`（默认 3，针对可重试的 LLM API 错误）
- D-Mail：是可选的“显式回到旧 checkpoint”机制，不会自动替你识别所有循环。

---

## 2. 硬上限机制

在 `_agent_loop()` 中，step 计数超过 `max_steps_per_turn` 会抛出 `MaxStepsReached` 并终止当前 turn。

这保证了即使模型持续产出 tool calls，也不会无限运行。

---

## 3. 重试机制边界

`_step()` 使用 tenacity 重试，但只针对 `_is_retryable_error()` 判定的 API 错误：
- `APIConnectionError`
- `APITimeoutError`
- `APIEmptyResponseError`
- `APIStatusError` 且状态码在 `429/500/502/503`

工具业务错误（例如参数错、命令失败）不会走这套自动重试。

---

## 4. D-Mail 在防循环中的真实角色

D-Mail 机制存在，但触发条件是：
- 有工具调用 `SendDMail` 显式发送到某个 checkpoint。

随后 `_step()` 检测到 pending D-Mail 才会抛 `BackToTheFuture`，主循环再执行：
- `context.revert_to(checkpoint_id)`
- 新建 checkpoint
- 注入来自“未来”的系统消息

这不是“自动循环检测”，而是“工具驱动的显式回退”。

---

## 5. 配置项

`src/kimi_cli/config.py`：
- `loop_control.max_steps_per_turn = 100`
- `loop_control.max_retries_per_step = 3`
- `loop_control.max_ralph_iterations = 0`（Ralph 自动迭代默认关闭）

---

## 6. 关键纠正

以下旧说法需要修正：
- “通过 D-Mail 机制防止无限循环”过于绝对。
- 更准确：D-Mail 是附加回退机制；真正的兜底是 step 上限和有限重试。

