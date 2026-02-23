# Kimi CLI 开发者入门（面向第一次做 Code Agent）

> 目标读者：有工程开发经验，但没有 code agent 开发经验。
>
> 阅读目标：30-60 分钟建立整体认知，并能在本地跑通、改动、验证一个最小功能。

---

## 0. 推荐阅读顺序（新手路径）

1. `00-kimi-cli-onboarding.md`：先建立全局心智模型。
2. `01-kimi-cli-overview.md`：看完整架构分层与模块边界。
3. `03-kimi-cli-session-runtime.md` + `04-kimi-cli-agent-loop.md`：理解一次 turn 如何被执行与持久化。
4. `05/06/07`：工具系统、MCP、上下文策略。
5. `08/09`：UI 协议层与 Web 多进程执行层。
6. `10/11/12`：安全、prompt、日志（偏工程治理）。

---

## 1. 先跑起来（10 分钟）

### 1.1 环境前提

- Python 版本要求：`>=3.12`（证据：`kimi-cli/pyproject.toml:6`）
- CLI 入口脚本：`kimi` / `kimi-cli`（证据：`kimi-cli/pyproject.toml:72`）

### 1.2 推荐命令

```bash
cd kimi-cli
make prepare
uv run kimi --help
uv run kimi
```

命令依据：
- `make prepare`：同步依赖并安装 hooks（`kimi-cli/Makefile:16`）
- `check/test/build` 常用目标（`kimi-cli/Makefile:59`, `kimi-cli/Makefile:93`, `kimi-cli/Makefile:108`）

### 1.3 你会得到什么

- 看到 CLI 参数和运行模式（shell / print / acp / wire）。
- 能发一条 prompt，进入一次完整 agent turn。

---

## 2. 全局架构（先有地图，再看街道）

```text
┌─────────────────────────────────────────────────────────┐
│ CLI 入口 (Typer)                                       │
│ src/kimi_cli/cli/__init__.py                           │
└──────────────────────┬──────────────────────────────────┘
                       │ 选择模式 + 组装配置/Session
                       ▼
┌─────────────────────────────────────────────────────────┐
│ KimiCLI.create()                                       │
│ src/kimi_cli/app.py                                    │
│ - 读配置、建 LLM、建 Runtime、加载 Agent/Tool          │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│ KimiSoul (核心循环)                                    │
│ src/kimi_cli/soul/kimisoul.py                          │
│ - _turn / _agent_loop / _step                          │
└──────────────────────┬──────────────────────────────────┘
                       │ 调 LLM + 调工具 + 审批 + 上下文
                       ▼
┌─────────────────────────────────────────────────────────┐
│ Context + Session + Wire                               │
│ src/kimi_cli/soul/context.py                           │
│ src/kimi_cli/session.py                                │
│ src/kimi_cli/wire/*                                    │
└─────────────────────────────────────────────────────────┘
```

关键代码锚点：
- CLI callback：`kimi-cli/src/kimi_cli/cli/__init__.py:54`
- CLI 执行入口 `_run()`：`kimi-cli/src/kimi_cli/cli/__init__.py:457`
- `KimiCLI.create()`：`kimi-cli/src/kimi_cli/app.py:55`
- `KimiSoul.run()`：`kimi-cli/src/kimi_cli/soul/kimisoul.py:182`
- `Context.checkpoint()`：`kimi-cli/src/kimi_cli/soul/context.py:68`

---

## 3. 一次请求是怎么跑完的（核心主链路）

### 3.1 运行时初始化链路

```text
CLI 参数解析
  -> 解析运行模式 + 参数冲突检查
  -> 创建/恢复 Session
  -> KimiCLI.create(session,...)
     -> load_config
     -> Runtime.create
     -> load_agent (system prompt + tools + MCP)
     -> Context.restore
  -> run_shell / run_print / run_acp / run_wire_stdio
```

代码依据：
- 参数冲突检查：`kimi-cli/src/kimi_cli/cli/__init__.py:357`
- Session 创建/恢复：`kimi-cli/src/kimi_cli/cli/__init__.py:464`
- UI 模式分发：`kimi-cli/src/kimi_cli/cli/__init__.py:501`
- Runtime 创建：`kimi-cli/src/kimi_cli/soul/agent.py:79`
- Agent 加载：`kimi-cli/src/kimi_cli/soul/agent.py:189`

### 3.2 单次 turn 执行链路

```text
KimiSoul.run(user_input)
  -> TurnBegin
  -> slash? (命令) / normal? (进入 _turn)
  -> _turn:
     - checkpoint
     - append user message
     - _agent_loop
  -> _agent_loop:
     - step 计数上限
     - 必要时 context compaction
     - _step (LLM + 工具执行)
     - 写回上下文
  -> TurnEnd
```

代码依据：
- `run`：`kimi-cli/src/kimi_cli/soul/kimisoul.py:182`
- `_turn`：`kimi-cli/src/kimi_cli/soul/kimisoul.py:210`
- `_agent_loop`：`kimi-cli/src/kimi_cli/soul/kimisoul.py:302`
- `_step`：`kimi-cli/src/kimi_cli/soul/kimisoul.py:382`

---

## 4. 数据怎么流（你需要知道哪些文件会变）

```text
用户输入
  -> Wire 事件流 (TurnBegin/StepBegin/ToolResult/...)
  -> context.jsonl (对话历史 + _checkpoint + _usage)
  -> wire.jsonl (可回放事件流)
  -> metadata(kimi.json) 更新 last_session_id
```

关键事实：
- `Context` 把消息、checkpoint、token usage 写入 `context.jsonl`。
- `Wire` 可把合并后的消息写入 `wire.jsonl`。
- 会话目录由 work_dir hash 决定。

代码依据：
- Context restore/checkpoint/revert：`kimi-cli/src/kimi_cli/soul/context.py:24`, `kimi-cli/src/kimi_cli/soul/context.py:68`, `kimi-cli/src/kimi_cli/soul/context.py:80`
- Wire 记录：`kimi-cli/src/kimi_cli/wire/__init__.py:29`, `kimi-cli/src/kimi_cli/wire/__init__.py:130`
- Session 文件布局：`kimi-cli/src/kimi_cli/session.py:106`, `kimi-cli/src/kimi_cli/session.py:131`

---

## 5. 设计意图与工程 trade-off（最重要）

### 5.1 为什么用 checkpoint，而不是全量事务回滚

设计意图：先保证“对话状态可回退”，而不是“外部世界可回退”。

trade-off：
- 优点：实现简单，性能与复杂度可控。
- 代价：文件系统副作用不自动回滚，需工具/策略层自行处理。

依据：
- context 回滚仅处理消息历史：`kimi-cli/src/kimi_cli/soul/context.py:80`

### 5.2 为什么审批放在工具层

设计意图：把风险控制绑定到“具体动作”（如 run command / edit file）。

trade-off：
- 优点：可解释、可交互、可会话级自动放行。
- 代价：需要维护动作命名规范；策略粒度偏动作级，不是语义级静态分析。

依据：
- 审批状态机：`kimi-cli/src/kimi_cli/soul/approval.py:34`
- Shell 调用审批：`kimi-cli/src/kimi_cli/tools/shell/__init__.py:56`
- MCP 工具审批：`kimi-cli/src/kimi_cli/soul/toolset.py:382`

### 5.3 为什么引入 Wire 作为中间层

设计意图：把“Agent 核心执行”与“UI 展示/协议交互”解耦。

trade-off：
- 优点：shell/print/wire/web 可复用同一 Soul。
- 代价：消息模型与桥接逻辑更复杂，排障要看 wire 层。

依据：
- `run_soul` 连接 Soul 与 UI：`kimi-cli/src/kimi_cli/soul/__init__.py:121`
- Wire 双队列与 merge：`kimi-cli/src/kimi_cli/wire/__init__.py:23`, `kimi-cli/src/kimi_cli/wire/__init__.py:87`

### 5.4 为什么 Web 模式用 runner + worker 子进程

设计意图：隔离会话执行，支持多会话管理与重放。

trade-off：
- 优点：生命周期控制清晰，Web API 与执行引擎隔离。
- 代价：进程间通信与状态同步更复杂。

依据：
- Web app 生命周期启动 runner：`kimi-cli/src/kimi_cli/web/app.py:168`, `kimi-cli/src/kimi_cli/web/app.py:179`
- WebSocket 会话流：`kimi-cli/src/kimi_cli/web/api/sessions.py:1044`
- worker 运行 `run_wire_stdio`：`kimi-cli/src/kimi_cli/web/runner/worker.py:57`

---

## 6. 你可以先改哪三类东西（最小可落地路径）

### 6.1 增加一个内置工具

最小路径：
1. 在 `src/kimi_cli/tools/<domain>/` 写 `CallableTool2` 工具类。
2. 在 agent spec (`agents/default/agent.yaml`) 把工具路径加入 `tools`。
3. 运行 `uv run kimi` 验证 tool call。

参考点：
- 工具加载与依赖注入：`kimi-cli/src/kimi_cli/soul/toolset.py:152`
- 工具参数摘要提取（UI 展示）：`kimi-cli/src/kimi_cli/tools/__init__.py:17`

### 6.2 调整循环策略

- `max_steps_per_turn`
- `max_retries_per_step`
- `reserved_context_size`

参考：`kimi-cli/src/kimi_cli/config.py:68`

### 6.3 增加一个技能（skill）

思路：先放 `SKILL.md`，再通过 `/skill:<name>` 调用。

参考：
- slash 技能命令生成：`kimi-cli/src/kimi_cli/soul/kimisoul.py:222`
- 读取技能并转为 user turn：`kimi-cli/src/kimi_cli/soul/kimisoul.py:286`

---

## 7. 调试与排障（工程视角）

- 日志文件：`~/.kimi/logs/kimi.log`（见 `kimi-cli/src/kimi_cli/app.py:44`）
- 常用检查：
  - `make check`（静态检查）
  - `make test`（测试）
  - `uv run kimi --debug ...`（增强日志）

常见症状与定位：
- 工具没注册：看 `load_tools` 与 agent spec 路径（`kimi-cli/src/kimi_cli/soul/toolset.py:152`）。
- 一直要求审批：看 `Approval` state 与 `/yolo` 状态（`kimi-cli/src/kimi_cli/soul/approval.py:44`）。
- 上下文过长：看 compaction 触发条件（`kimi-cli/src/kimi_cli/soul/kimisoul.py:341`）。
- Web 连接问题：看 session stream 鉴权与 origin 检查（`kimi-cli/src/kimi_cli/web/api/sessions.py:1061`）。

### 7.1 流程图完整性检查（本轮校验）

已补齐新手最关键的 8 条流程图主链路：

1. 启动与初始化链路：`00-kimi-cli-onboarding.md` 第 2、3 节。
2. 单 turn 执行链路：`00-kimi-cli-onboarding.md` 第 3.2 节 + `04-kimi-cli-agent-loop.md`。
3. Session 生命周期与收尾分支：`03-kimi-cli-session-runtime.md` 第 1 节。
4. UI-Wire-Soul 交互与取消链路：`08-kimi-cli-ui-interaction.md` 第 1、2 节。
5. WebSocket 历史回放 + 实时转发链路：`09-kimi-cli-web-server.md` 第 2 节。
6. Web runner/worker 子进程链路：`09-kimi-cli-web-server.md` 第 3 节。
7. Prompt 继承 + 渲染链路：`11-kimi-cli-prompt-organization.md` 第 1、3 节。
8. Logging 启用 + stderr 重定向链路：`12-kimi-cli-logging.md` 第 1、3 节。

---

## 8. 快速心智模型（给新手的一句话）

Kimi CLI 本质是：

**“一个带状态（Session/Context）的 LLM 循环器 + 一个工具执行与审批系统 + 一个可替换的 UI/Wire 外壳。”**

你只要先掌握这三块，后续无论扩展工具、改策略还是接 Web/IDE，都会有稳定抓手。
