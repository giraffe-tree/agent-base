# opencode 中 checkpoint 是如何实现的？

opencode 的 checkpoint 不是通过复制工作目录或数据库快照实现，而是通过一个**影子 Git 仓库（shadow git repo）**实现文件级可回滚能力，并与会话消息流绑定。

## 1) 核心思路

- 为每个项目创建独立的 snapshot git 目录：`<data>/snapshot/<project.id>`
- 该仓库与项目目录解耦：
  - `--git-dir` 指向 snapshot 目录
  - `--work-tree` 指向真实项目目录
- 因此可以在不污染用户项目 `.git` 的前提下，追踪项目当前文件状态

对应实现见 `opencode/packages/opencode/src/snapshot/index.ts` 的 `gitdir()` 与各个 git 命令调用。

## 2) 在 agent loop 里的接入点

checkpoint 直接嵌入在单轮处理器里（`SessionProcessor`）：

1. `start-step` 事件时调用 `Snapshot.track()`，记录当前 tree hash  
2. `finish-step` 事件时调用 `Snapshot.patch(snapshotHash)`，计算该 step 的变更文件  
3. 若有变更，写入一个 `patch` 类型的 message part（保存 `hash + files`）

这意味着：**每个推理 step 都可形成一个可回滚边界**。

## 3) Snapshot 子系统做了什么

### `Snapshot.track()`

- `git add .`
- `git write-tree`
- 返回 tree hash（作为 checkpoint 标识）

### `Snapshot.patch(hash)`

- 基于 `git diff --name-only <hash> -- .` 计算自 checkpoint 以来变更文件
- 返回 `{ hash, files[] }`

### `Snapshot.revert(patches)`

- 遍历 patch 中的文件，执行 `git checkout <hash> -- <file>`
- 若文件在该 hash 不存在，则删除文件（处理新增文件回滚）

### `Snapshot.restore(hash)`

- 用 `read-tree + checkout-index -a -f` 进行全量恢复
- 用于 unrevert 场景恢复到先前整体状态

## 4) 会话级回滚（Revert）流程

`SessionRevert.revert()` 的逻辑是：

1. 找到目标 `messageID` / `partID`
2. 收集目标之后所有 `patch` part
3. 执行 `Snapshot.revert(patches)` 回滚文件
4. 记录 `session.revert` 元数据（含 `snapshot`、`diff`）
5. 计算并发布 diff（用于 UI/状态展示）

随后在下一次 prompt 前，`SessionRevert.cleanup()` 会清理被回滚区间的消息/parts（真正完成会话历史收敛）。

`SessionRevert.unrevert()` 则调用 `Snapshot.restore(snapshot)` 做反向恢复。

## 5) 这个设计的优点

- 不依赖用户项目 git 历史，侵入性低
- 与 agent step 强绑定，回滚粒度自然
- 文件状态与消息状态可同步回滚/恢复
- 通过周期 `git gc --prune=7.days` 控制 snapshot 存储增长

## 6) 约束与前提

- 仅在 `project.vcs === "git"` 时启用
- 配置可通过 `cfg.snapshot === false` 关闭
- `acp` 客户端路径下会跳过 snapshot 逻辑

## 7) Shadow Git 实现流程（含是否依赖本地 git）

### 流程拆解

1. 项目初始化时，opencode 先向上查找 `.git` 目录，识别当前项目是否是 git 项目  
2. 如果是 git 项目，创建/复用 shadow git 目录：`<data>/snapshot/<project.id>`  
3. 每次 step 开始时执行 `track`：`git add .` + `git write-tree`，得到 checkpoint hash  
4. 每次 step 结束时执行 `patch`：`git diff --name-only <hash> -- .`，得到变更文件列表  
5. 用户触发 revert 时，按 patch 顺序执行 `git checkout <hash> -- <file>` 恢复文件  
6. 用户触发 unrevert 时，执行 `read-tree + checkout-index` 回到 revert 前整体状态  

### 是否需要本地 git 环境

- **需要。** Shadow Git 是通过调用本地 `git` 命令实现的（不是内置纯 TS 实现）。  
- 若本机没有可用 git，项目会降级为 `fake_vcs/global` 路径，snapshot/checkpoint/revert 相关能力不可用。  
- 即使项目目录里有 `.git`，若执行 `git` 命令失败，也无法完成 checkpoint 流程。

### 快速自检建议

- `git --version`（确认本机可执行 git）
- 在项目目录执行 `git rev-parse --show-toplevel`（确认仓库可识别）

---

一句话总结：**opencode 的 checkpoint 本质是“每个 step 的 tree hash + patch 记录 + 会话级 revert 协调”的 shadow-git 机制。**
