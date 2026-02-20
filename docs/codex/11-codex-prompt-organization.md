# Prompt Organization（codex）

结论先行：codex 采用"编译时静态包含 + 运行时动态渲染"的双层 prompt 组织策略，通过 Rust 的 `include_str!` 宏在编译期嵌入基础模板，再使用 Askama 模板引擎在运行时根据任务类型、模式配置进行动态组合。

---

## 1. Prompt 组织流程图

```text
+------------------------+
| Prompt 源文件           |
| (prompt.md, templates/) |
+-----------+------------+
            |
            v
+------------------------+
| 编译时包含              |
| (include_str! 宏)       |
+-----------+------------+
            |
            v
+------------------------+
| 基础模板层              |
| (Base Prompt)           |
+-----------+------------+
            |
            v
+------------------------+
| 模式选择器              |
| (Mode Selector)         |
+-----------+------------+
            |
            v
+------------------------+
| 任务模板层              |
| (Task Template)         |
+-----------+------------+
            |
            v
+------------------------+
| 工具描述注入            |
| (Tool Descriptions)     |
+-----------+------------+
            |
            v
+------------------------+
| Askama 渲染             |
| (动态变量替换)           |
+-----------+------------+
            |
            v
+------------------------+
| 最终 Prompt             |
| (发送至模型)            |
+------------------------+
```

---

## 2. 分层架构详解

```text
┌─────────────────────────────────────────────────────┐
│ Layer 4: Tool Layer                                  │
│  - 动态注入工具描述 (tool descriptions)               │
│  - 工具调用示例和格式说明                             │
├─────────────────────────────────────────────────────┤
│ Layer 3: Task Layer                                  │
│  - 任务特定指令 (coding, debugging, refactoring)      │
│  - 上下文文件引用                                     │
├─────────────────────────────────────────────────────┤
│ Layer 2: Mode Layer                                  │
│  - 模式配置 (agent mode, ask mode)                   │
│  - 行为约束和权限边界                                 │
├─────────────────────────────────────────────────────┤
│ Layer 1: Base Layer                                  │
│  - 系统身份定义                                      │
│  - 核心能力说明                                      │
│  - 全局安全策略                                      │
└─────────────────────────────────────────────────────┘
```

---

## 3. Prompt 文件位置

| 文件路径 | 职责 |
|---------|------|
| `codex/codex-rs/core/prompt.md` | 核心基础 prompt，定义系统身份和能力 |
| `codex/codex-rs/templates/` | Askama 模板目录，按任务类型组织 |
| `codex/protocol/src/prompts/` | 协议层 prompt 定义，工具描述生成 |
| `codex/codex-rs/core/src/prompt/` | Prompt 渲染模块，模板组合逻辑 |

---

## 4. 加载与管理机制

### 4.1 编译时加载

```rust
// 编译时嵌入基础 prompt
const BASE_PROMPT: &str = include_str!("../prompt.md");
```

特点：
- 基础 prompt 在编译期确定，运行时不可变
- 避免运行时文件 IO，提升启动速度
- 保证基础行为的一致性

### 4.2 运行时渲染

```rust
// Askama 模板结构
#[derive(Template)]
#[template(path = "task.txt")]
struct TaskPrompt {
    base: String,
    mode: ModeConfig,
    tools: Vec<ToolDescription>,
    context: TaskContext,
}
```

渲染流程：
1. 根据用户输入确定任务类型
2. 选择对应的模板文件
3. 注入工具描述（动态生成）
4. 替换模板变量
5. 生成最终 prompt

---

## 5. 模板与变量系统

### 5.1 Askama 模板语法

```text
{# 注释 #}
{{ variable }}           {# 变量插值 #}
{% if condition %}...{% endif %}   {# 条件渲染 #}
{% for item in items %}...{% endfor %}  {# 循环渲染 #}
```

### 5.2 变量类型

| 变量类别 | 说明 | 示例 |
|---------|------|------|
| `mode` | 运行模式配置 | `agent`, `ask` |
| `cwd` | 当前工作目录 | `/path/to/project` |
| `tools` | 可用工具列表 | `read`, `write`, `shell` |
| `files` | 上下文文件 | 用户引用的代码文件 |
| `history` | 对话历史摘要 | 前几轮关键信息 |

---

## 6. Prompt 工程方法

### 6.1 多层组合模式

```text
最终 Prompt = Base + ModeOverlay + TaskSpecific + ToolDescriptions
```

- **Base**: 不变的系统身份定义
- **ModeOverlay**: 根据模式叠加的行为约束
- **TaskSpecific**: 当前任务的特定指令
- **ToolDescriptions**: 动态生成的工具说明

### 6.2 工具描述动态生成

工具不是硬编码在 prompt 中，而是根据配置动态组装：
- 读取 `ToolsConfig` 确定可用工具
- 从 `protocol/src/prompts/` 加载工具描述模板
- 根据工具参数生成 JSON Schema 描述

### 6.3 上下文窗口管理

```text
┌────────────────────────────────────┐
│ System Prompt (固定)                │
├────────────────────────────────────┤
│ Tool Descriptions (动态生成)        │
├────────────────────────────────────┤
│ Context Files (按需加载)            │
├────────────────────────────────────┤
│ Conversation History (滑动窗口)     │
├────────────────────────────────────┤
│ Current User Message                │
└────────────────────────────────────┘
```

---

## 7. 证据索引

- `codex` + `codex/codex-rs/core/prompt.md` + 核心基础 prompt，定义系统身份和能力边界
- `codex` + `codex/codex-rs/templates/` + Askama 模板目录，任务特定 prompt 模板
- `codex` + `codex/protocol/src/prompts/` + 协议层工具描述生成逻辑
- `codex` + `codex/codex-rs/core/src/prompt/` + Prompt 渲染和组合模块
- `codex` + `docs/codex/04-codex-agent-loop.md` + TurnContext 中的 prompt 注入点

---

## 8. 边界与不确定性

- 本文基于仓库内研究文档整理，实际代码实现可能有所调整
- `codex-rs` 实码未完整收录，模板变量具体列表以实际源码为准
- Askama 模板的具体语法版本需要核对项目依赖

