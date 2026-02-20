# opencode：使用 revert 回滚时，用户已编辑与源文件冲突怎么处理？

本文基于现有文档与实现描述，说明 **opencode 当前**在 revert 回滚时若目标文件已被用户编辑，是否有冲突检测与专门处理。只讲现状，不讨论未来可如何实现。

---

## 1) 结论

**当前 opencode 不做“用户已编辑”的冲突检测，revert 时直接按 checkpoint 的 tree hash 覆盖工作区文件。**  
若用户在 revert 前已手动改过某文件，该文件的修改会被 `git checkout <hash> -- <file>` 覆盖，不会弹窗或提示冲突。

---

## 2) 依据：revert 的当前实现

- **会话级回滚**：`SessionRevert.revert()` 会收集目标 message/part 之后的所有 `patch` part，然后调用 `Snapshot.revert(patches)` 回滚文件，并写 `session.revert` 元数据、做消息区间清理。  
  详见 `docs/opencode/questions/opencode-checkpoint-implementation.md` 第 4 节。

- **文件级回滚**：`Snapshot.revert(patches)` 对每个 patch 中的文件执行：
  - `git checkout <hash> -- <file>` 将工作区恢复为 snapshot 中该 hash 对应的版本；
  - 若文件在该 hash 中不存在，则删除该文件（用于回滚“新增文件”）。

- **实现位置**：`opencode/packages/opencode/src/snapshot/index.ts`（文档引用）；revert 流程中未描述任何“检查工作区是否被用户修改”或“冲突协商”的逻辑。

因此，**当前行为是“强制覆盖”**：不比较当前工作区与 checkpoint 的差异，不区分“仅 agent 改过”与“用户也改过”，一律用 snapshot 版本覆盖。

---

## 3) 与“冲突”相关的现状小结

| 项目         | 当前是否具备                         |
|--------------|--------------------------------------|
| 冲突检测     | 无：不检测用户是否在 revert 前编辑过 |
| 冲突提示/弹窗| 无                                   |
| 三路合并/协商| 无                                   |
| 回滚失败处理 | 未在文档中描述（如 git 命令失败时的表现） |

---

## 4) 使用上的含义

- 若用户在 revert 前手动编辑了会被回滚到的文件，**这些编辑会在 revert 后丢失**。
- 若希望保留用户编辑，需要在 revert 前自行备份或提交（例如用项目自身的 git 或其它方式），当前 opencode revert 流程不会代为保留或合并。

---

**一句话**：opencode 的 revert 当前是“按 checkpoint 强制覆盖工作区”，不处理“用户已编辑与源文件冲突”的检测与协商，用户需自行在 revert 前做好备份或版本管理。
