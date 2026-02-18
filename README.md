# Agent Base Research

这个仓库用于对多个 Coding Agent 项目进行源码级拆解，重点关注：

- Agent Loop 如何组织（输入、推理、工具执行、续跑与收敛）
- 工具系统与 MCP 集成方式
- 上下文管理、压缩、回滚与安全控制策略
- CLI/UI 交互链路与 Web Server 扩展能力

> 当前研究基线：所有上游仓库均来自 2026-02-08 从 GitHub clone 的当时最新分支。

## 初始化(可选)

```bash
# export ALL_PROXY=http://localhost:7890
git clone https://github.com/openai/codex.git
git clone https://github.com/anomalyco/opencode.git
git clone https://github.com/google-gemini/gemini-cli.git
git clone https://github.com/MoonshotAI/kimi-cli.git
git clone https://github.com/SWE-agent/SWE-agent.git
```

## 文档结构

`docs/` 目录按“产品维度 + 主题维度”组织：

- `docs/codex/`：Codex CLI 源码分析
- `docs/opencode/`：opencode 源码分析
- `docs/gemini-cli/`：Gemini CLI 源码分析
- `docs/kimi-cli/`：Kimi CLI 源码分析
- `docs/swe-agent/`：SWE-agent 源码分析
- `docs/comm/`：跨项目共性抽象
- `docs/cursor/questions/`：Cursor 相关专项问题记录

各目录下文档编号语义（统一约定）：

- `02`：CLI 入口与启动链路
- `03`：Session Runtime（会话运行时）
- `04`：Agent Loop（核心）
- `05`：Tools System
- `06`：MCP Integration
- `07`：Memory Context
- `08`：UI Interaction
- `09`：Web Server
- `10`：Safety Control
- `11+`：专题问题（如 checkpoint/revert 等）

## 当前覆盖进度

- `codex`：`02~10` 已覆盖
- `opencode`：`02~10` 已覆盖，含 `questions/11`
- `gemini-cli`：`02~10` 已覆盖
- `kimi-cli`：`02~10` 已覆盖，含 `questions/11`、`questions/12`
- `comm`：`02~10` 已覆盖（跨项目共性层）
- `swe-agent`：已完成 `02`、`04`，其余主题待补充
- `cursor`：已开始 questions 方向研究

## 推荐阅读路径

如果你是第一次阅读，建议按以下顺序：

1. 先读各项目的 `04-*agent-loop.md`，建立主执行链路心智模型；
2. 再读 `05~07`，理解工具、MCP、上下文管理；
3. 最后读 `08~10`，补齐交互层和安全控制；
4. 对特定机制（如 checkpoint/revert）再进入 `questions/` 深挖。
