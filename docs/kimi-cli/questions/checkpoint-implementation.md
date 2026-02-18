# Kimi CLI：checkpoint 如何实现

本文聚焦 `Agent loop` 里的 checkpoint 机制：它解决什么问题、主流程如何跑、底层如何保证上下文一致性。

---

## 1) 先看整体流程（主线）

在一个 turn（一次用户输入）里，checkpoint 的主流程可以概括为：

1. `run(user_input)` 进入 `_turn()`
2. `_turn()` 先创建 checkpoint（首次通常是 `checkpoint 0`）
3. append 用户消息后进入 `_agent_loop()`
4. `_agent_loop()` 循环执行 `_step()`
5. 过程中有两类会影响 checkpoint 的分支：
   - **上下文压缩（compaction）**：清空上下文并重建 checkpoint，再写入压缩后的消息
   - **回到未来（BackToTheFuture / D-Mail）**：回滚到指定 checkpoint，随后新建 checkpoint 并注入消息
6. 当模型不再发 tool call（或工具被拒绝）时结束 turn

一句话理解：  
checkpoint 是 turn 内上下文的“可回退锚点”，让 agent 能在异常分支或策略改道时恢复到安全状态继续推进。

---

## 2) checkpoint 在什么时候被创建/重建

### A. turn 开始时创建

- `_turn()` 在 append 用户消息前后（实现通常是前置）会新建 checkpoint。
- 第一次进入通常形成 `checkpoint 0`，作为本轮最早的回退点。

### B. context compaction 后重建

当 token 预算逼近模型上下文上限时，`_agent_loop()` 会触发 `compact_context()`：

1. 发 `CompactionBegin`
2. 生成压缩消息（summary/retained messages）
3. 清空旧 context
4. **新建 checkpoint**
5. append 压缩消息
6. 发 `CompactionEnd`

这一步的核心是：压缩后上下文结构变化很大，旧 checkpoint 不再可靠，所以需要重建新的“基线锚点”。

### C. BackToTheFuture 回滚后重建

当 `_step()` 里检测到 `DenwaRenji` 有 pending D-Mail，会抛 `BackToTheFuture(checkpoint_id, messages)`，`_agent_loop()` 捕获后：

1. `context.revert_to(checkpoint_id)`
2. **新建 checkpoint**
3. append 注入消息（系统提示 + D-Mail 内容）
4. 继续后续 step

这是“回滚 + 改道”的关键闭环：先恢复历史状态，再以新 checkpoint 作为后续分支起点。

---

## 3) 细节：内部数据与操作语义

> 下述是按当前文档与调用语义可还原的实现模型。

### 3.1 context 至少维护两类状态

- **消息历史**：`history`（用户/assistant/tool 消息序列）
- **checkpoint 索引**：checkpoint_id 到历史位置（通常是消息下标或快照指针）的映射

可以把它看成：

- `history`: append-only（正常路径）
- `checkpoint`: 记录“当时 history 到了哪里”

### 3.2 create_checkpoint 的语义

`create_checkpoint()` 的返回值是一个 checkpoint_id，它对应“当前历史边界”。

伪代码：

```python
def create_checkpoint():
    cid = next_checkpoint_id()
    checkpoints[cid] = len(history)   # 锚定到当前消息边界
    return cid
```

### 3.3 revert_to 的语义

`revert_to(checkpoint_id)` 会把 `history` 截断回该 checkpoint 对应位置，并丢弃其后的分支消息。

伪代码：

```python
def revert_to(cid):
    pos = checkpoints[cid]
    history = history[:pos]
    prune_checkpoints_after(pos)
```

这样可以保证：回滚后的上下文与“当时创建 checkpoint 时看到的世界”一致。

---

## 4) 细节：为什么 loop 里不容易把上下文弄乱

### 4.1 `_grow_context()` 用 `asyncio.shield`

step 完成后，assistant 消息和 tool results 会被写回 context。  
这段增长逻辑被 `shield` 保护，意义是：

- 即使外层任务取消/中断，也尽量完成关键写入
- 降低“模型已经输出了，但上下文没落盘”的不一致风险

### 4.2 异常分支显式收口

- `_step()` 抛错时：发送 `StepInterrupted` 并中断 turn
- `BackToTheFuture`：专门 catch，执行 `revert_to + create_checkpoint + append injected messages`

也就是说，所有非主路径都被显式编码，不依赖隐式状态恢复。

### 4.3 审批管道与 checkpoint 解耦

tool approval 通过独立协程在 wire 和 Approval 系统之间转发。  
它影响“工具是否执行”，但不直接改 checkpoint；checkpoint 仍由 `_turn / compact_context / BackToTheFuture` 三个位置统一管理。

---

## 5) 一个最小时序（含回滚）

1. 创建 `checkpoint 0`
2. 用户消息入 context
3. step#1：模型调用工具，结果写回
4. step#2：触发 D-Mail，抛 `BackToTheFuture(checkpoint 0, injected_messages)`
5. loop 捕获后回滚到 `checkpoint 0`
6. 新建 `checkpoint 1`
7. 注入消息，继续 step#3...

效果：  
step#1 之后产生的“旧分支历史”被安全裁掉，后续从新分支继续。

---

## 6) 设计价值与边界

### 价值

- **可恢复**：策略错误或外部信号到来时可回退重试
- **可控**：把“上下文状态机”从隐式变成显式
- **可扩展**：后续可叠加更复杂的 flow/branching 逻辑

### 边界

- checkpoint 只保证“上下文一致性”，不自动回滚外部副作用（例如某些工具已写文件/发请求）
- 因此高风险工具仍需要审批与幂等策略配合

---

## 7) 一句话总结

Kimi CLI 的 checkpoint 本质是：  
**在 turn 内给 context 打可回退锚点，并在 compaction 与 BackToTheFuture 分支中“回滚/重建”，从而保证 agent 在复杂循环里仍能稳定演进。**
