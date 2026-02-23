# Prompt Organization（kimi-cli）

> 面向第一次做 code agent 的开发者：先把“Prompt 从哪里来、何时被使用、如何注入运行时信息”这三件事搞清楚。

本文基于：
- `kimi-cli/src/kimi_cli/agentspec.py`
- `kimi-cli/src/kimi_cli/soul/agent.py`
- `kimi-cli/src/kimi_cli/prompts/__init__.py`
- `kimi-cli/src/kimi_cli/soul/slash.py`
- `kimi-cli/src/kimi_cli/soul/compaction.py`

---

## 1. 全局图：Prompt 组装主链路

```text
agent.yaml / custom agent.yaml
  -> load_agent_spec() 递归解析 extend
  -> 解析 system_prompt_path + system_prompt_args
  -> _load_system_prompt()
     - 读 system.md
     - 注入 builtin args + spec args
     - Jinja2 渲染 (${...})
  -> 得到最终 system prompt
  -> 交给 KimiSoul 作为本轮系统约束
```

代码锚点：
- 默认 agent 文件：`kimi-cli/src/kimi_cli/agentspec.py:20`
- 入口 `load_agent_spec()`：`kimi-cli/src/kimi_cli/agentspec.py:70`
- 递归继承与字段合并：`kimi-cli/src/kimi_cli/agentspec.py:123`
- system prompt 渲染：`kimi-cli/src/kimi_cli/soul/agent.py:269`
- Jinja 变量分隔符 `${...}`：`kimi-cli/src/kimi_cli/soul/agent.py:279`

---

## 2. 配置层：agent spec 怎么“继承 + 覆盖”

```text
子 agent.yaml (extend: default)
  -> 先加载父层 default/agent.yaml
  -> 再按字段覆盖
     - name / system_prompt_path / tools / exclude_tools / subagents: 直接覆盖
     - system_prompt_args: 按 key 合并（不是整体替换）
```

设计意图：
- 让团队可以复用统一基线 prompt/toolset，再按项目局部覆盖。

工程 trade-off：
- 优点：复用强，迁移成本低。
- 代价：继承层级深时，定位“最终生效值”需要展开合并链路。

代码锚点：
- `AgentSpec` 字段定义：`kimi-cli/src/kimi_cli/agentspec.py:31`
- 解析并校验必填字段：`kimi-cli/src/kimi_cli/agentspec.py:78`
- `system_prompt_args` 合并：`kimi-cli/src/kimi_cli/agentspec.py:133`
- 相对路径转绝对路径：`kimi-cli/src/kimi_cli/agentspec.py:116`

---

## 3. 渲染层：运行时变量从哪里来

```text
Runtime.create()
  -> 收集 builtin args
     - KIMI_NOW
     - KIMI_WORK_DIR / KIMI_WORK_DIR_LS
     - KIMI_AGENTS_MD
     - KIMI_SKILLS
  -> _load_system_prompt(template, spec_args, builtin_args)
  -> template.render(asdict(builtin_args), **spec_args)
```

设计意图：
- 把“环境事实”（目录、技能、时间）在 runtime 注入，而不是写死在 prompt 文件里。

工程 trade-off：
- 优点：同一套 prompt 可以跨项目复用。
- 代价：模板变量写错会在运行期失败（`StrictUndefined`），需要测试覆盖。

代码锚点：
- builtin args 构造：`kimi-cli/src/kimi_cli/soul/agent.py:112`
- 读取 `AGENTS.md`：`kimi-cli/src/kimi_cli/soul/agent.py:50`
- 使用 `StrictUndefined`：`kimi-cli/src/kimi_cli/soul/agent.py:285`
- 变量缺失时报错：`kimi-cli/src/kimi_cli/soul/agent.py:290`

---

## 4. 流程模板层：`prompts/*.md` 的真实用途

很多新手会把 `prompts/*.md` 误解为“主 system prompt”。当前代码不是这样。

```text
prompts/init.md
  -> /init slash command 使用
  -> 临时 soul 执行一次 INIT 提示

prompts/compact.md
  -> context compaction 使用
  -> 把历史对话打包后附加 COMPACT 模板让模型摘要
```

代码锚点：
- 模板加载：`kimi-cli/src/kimi_cli/prompts/__init__.py:5`
- `/init` 使用 `prompts.INIT`：`kimi-cli/src/kimi_cli/soul/slash.py:41`
- compaction 使用 `prompts.COMPACT`：`kimi-cli/src/kimi_cli/soul/compaction.py:115`

---

## 5. 新手最容易踩的 3 个坑

1. 把 `prompts/init.md` 当主 system prompt。
2. 以为 `system_prompt_args` 会整体替换父层。
3. 模板里写了 `${VAR}`，但 runtime 没提供，启动才报错。

快速排查：
- 先看最终 agent spec 合并是否符合预期：`kimi-cli/src/kimi_cli/agentspec.py:123`
- 再看模板渲染入参：`kimi-cli/src/kimi_cli/soul/agent.py:289`

---

## 6. 关键结论

- 主 prompt 入口：`agents/*/agent.yaml + system.md`。
- `prompts/*.md` 是“流程模板”（`/init`、压缩），不是主 system prompt。
- 继承合并在 `agentspec.py`，模板渲染在 `soul/agent.py`。
