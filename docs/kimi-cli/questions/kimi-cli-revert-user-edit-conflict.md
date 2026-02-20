# Kimi CLI：使用 revert 回滚时，用户已编辑与源文件冲突怎么处理？

本文基于现有文档说明 **Kimi CLI 当前**在 revert 场景下是否涉及“用户已编辑与源文件冲突”的处理。只讲现状，不讨论未来可如何实现。

---

## 1) 结论

**Kimi CLI 的 revert（`revert_to`）只做对话/推理上下文的回退，不做文件级回滚。**  
因此不存在“revert 时发现用户已经编辑过某文件、与源文件冲突”的**文件冲突**流程；与“用户编辑”冲突相关的问题在当前设计中不适用。

---

## 2) 依据

- **revert 的语义**：`context.revert_to(checkpoint_id)` 会把消息历史截断到该 checkpoint 对应的位置，并丢弃其后的分支消息；不读写工作区文件。  
  详见 `docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md` 中 3.3 revert_to 的语义。

- **设计边界**：checkpoint 被明确定义为“对话/推理状态”的回退机制，而非“外部世界状态（如文件）”的事务机制；默认不把文件回滚纳入能力。  
  详见 `docs/kimi-cli/questions/kimi-cli-checkpoint-no-file-rollback-tradeoffs.md`。

- **流程**：BackToTheFuture / D-Mail 触发时，会执行 `revert_to(checkpoint_id)` 并新建 checkpoint、注入消息，全程操作的是 context（history/checkpoint 索引），不涉及对磁盘文件的恢复或冲突检测。

因此，**当前没有“revert 时与用户编辑的文件冲突”的专门处理**，因为 revert 本身不触碰文件。

---

## 3) 小结

| 项目           | 当前情况                                   |
|----------------|--------------------------------------------|
| 文件级 revert  | 无                                         |
| 用户编辑 vs 源文件冲突 | 不适用（无文件回滚，故无此类冲突流程）     |
| 冲突检测/提示  | 不涉及                                     |

---

**一句话**：Kimi CLI 的 revert 仅回滚上下文，不做文件回滚，因此当前不存在“revert 时发现用户已编辑与源文件冲突”的处理逻辑。
