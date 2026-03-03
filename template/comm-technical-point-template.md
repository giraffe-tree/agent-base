# 单一技术点文档模板（适用于 Agent Loop、Checkpoint、Tool System 等）

存放位置：`docs/comm/comm-{技术点}-template.md`

使用方式：基于具体源码填写模板中的占位符，产出 `docs/{project}/{编号}-{project}-{技术点}.md`

---

> 📋 **阅读指南**
>
> | 属性 | 说明 |
> |-----|------|
> | 预计阅读 | 20-30 分钟 |
> | 前置文档 | `01-{project}-overview.md`、`03-{project}-session-runtime.md` |
> | 文档结构 | 速览 → 架构 → 机制 → 实现 → 对比 |
> | 代码呈现 | 关键代码直接展示，完整代码可折叠查看 |

---

```markdown
# {技术点名称}（如：Agent Loop / Checkpoint / Tool System）

## TL;DR（结论先行）

一句话定义该技术点：{一句话描述}

{Project} 的核心取舍：**{设计选择}**（对比其他项目的 {其他选择}）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 核心机制 | {一句话说明} | `{file}:{line}` |
| 状态管理 | {一句话说明} | `{file}:{line}` |
| 错误处理 | {一句话说明} | `{file}:{line}` |

---

## 1. 为什么需要这个机制？（解决什么问题）

### 1.1 问题场景

{描述一个具体场景，说明没有这个机制会怎样}

```
示例（Agent Loop）：
没有 Loop：用户问"修复这个 bug"→ LLM 一次回答→ 结束（可能根本没看文件）

有 Loop：
  → LLM: "先读文件" → 读文件 → 得到结果
  → LLM: "再跑测试" → 执行测试 → 得到结果
  → LLM: "修改第 42 行" → 写文件 → 成功
```

### 1.2 核心挑战

| 挑战 | 不解决的后果 |
|-----|-------------|
| {challenge1} | {consequence1} |
| {challenge2} | {consequence2} |
| {challenge3} | {consequence3} |

---

## 2. 整体架构（ASCII 图）

### 2.1 在系统中的位置

```text
┌─────────────────────────────────────────────────────────────┐
│ {上层模块}                                                   │
│ {上层文件路径}                                               │
└───────────────────────┬─────────────────────────────────────┘
                        │ 调用
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ {本技术点} ▓▓▓                                          │
│ {核心文件路径}                                               │
│ - {关键类/函数1}                                            │
│ - {关键类/函数2}                                            │
└───────────────────────┬─────────────────────────────────────┘
                        │ 依赖/调用
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ {下层模块1}          │ {下层模块2}          │ ...           │
│ {下层路径1}          │ {下层路径2}          │               │
└──────────────────────┴──────────────────────┴───────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 代码位置 |
|-----|------|---------|
| `{component1}` | {responsibility1} | `{file1}:{line1}` |
| `{component2}` | {responsibility2} | `{file2}:{line2}` |
| `{component3}` | {responsibility3} | `{file3}:{line3}` |

### 2.3 核心组件交互关系

展示组件间如何协作完成一次完整操作。

```mermaid
sequenceDiagram
    autonumber
    participant A as {组件A}
    participant B as {本技术点核心}
    participant C as {组件C}
    participant D as {组件D}

    A->>B: 1. 请求/触发
    Note over B: 内部处理阶段
    B->>B: 2. 状态变更/内部处理
    B->>C: 3. 调用下游服务
    C-->>B: 4. 返回结果
    B->>D: 5. 副作用/持久化
    B-->>A: 6. 最终结果
```

**关键交互说明**：

| 步骤 | 交互内容 | 设计意图 |
|-----|---------|---------|
| 1 | A 向核心组件发起请求 | 解耦触发与执行，支持多种触发源 |
| 2 | 核心组件内部状态流转 | 记录中间状态便于恢复和监控 |
| 3 | 调用下游组件处理 | 职责分离，核心组件协调而非包揽 |
| 4 | 同步/异步返回结果 | {选择同步或异步的原因} |
| 5 | 触发副作用 | 主流程与副作用分离，确保核心逻辑稳定 |
| 6 | 返回最终响应 | 统一输出格式，便于上层处理 |

---

## 3. 核心组件详细分析

对每个核心组件进行深入剖析，包括内部状态、算法逻辑和数据流。

### 3.1 {核心组件A} 内部结构

#### 职责定位

一句话说明该组件在本技术点中的核心职责。

#### 状态机图

```mermaid
stateDiagram-v2
    [*] --> Idle: 初始化
    Idle --> Processing: 收到请求
    Processing --> Waiting: 需要外部输入
    Waiting --> Processing: 收到输入
    Processing --> Completed: 处理成功
    Processing --> Failed: 处理失败
    Completed --> [*]
    Failed --> Idle: 重试
    Failed --> [*]: 超过重试次数
```

**状态说明**：

| 状态 | 说明 | 进入条件 | 退出条件 |
|-----|------|---------|---------|
| Idle | 空闲等待 | 初始化完成或处理结束 | 收到新请求 |
| Processing | 处理中 | 收到请求并开始处理 | 处理完成/失败/需要等待 |
| Waiting | 等待外部输入 | 需要额外数据才能继续 | 收到所需输入 |
| Completed | 完成 | 处理成功 | 自动返回 Idle |
| Failed | 失败 | 处理出错 | 重试或终止 |

#### 内部数据流

```text
┌────────────────────────────────────────────┐
│  输入层                                     │
│   原始输入 → 验证解析 → 结构化数据          │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  处理层                                     │
│   预处理 → 核心处理 → 后处理               │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  输出层                                     │
│   结果格式化 → 副作用执行 → 事件通知        │
└────────────────────────────────────────────┘
```

#### 关键接口

| 接口 | 输入 | 输出 | 说明 | 代码位置 |
|-----|------|------|------|---------|
| `init()` | 配置对象 | 初始化状态 | 组件初始化 | `{file}:{line}` |
| `process()` | 请求数据 | 处理结果 | 核心处理方法 | `{file}:{line}` |
| `cleanup()` | - | 清理状态 | 资源释放 | `{file}:{line}` |

---

### 3.2 {核心组件B} 内部结构

（同 3.1 结构，针对第二个核心组件进行分析）

---

### 3.3 组件间协作时序

展示多个组件如何协作完成一个复杂操作。

```mermaid
sequenceDiagram
    participant U as {调用方}
    participant A as {组件A}
    participant B as {组件B}
    participant C as {组件C}
    participant S as {存储/外部服务}

    U->>A: 发起操作
    activate A

    A->>A: 前置检查
    Note right of A: 验证输入合法性

    A->>B: 请求处理
    activate B

    B->>B: 内部处理
    B->>C: 调用服务
    activate C

    C->>S: 读取数据
    S-->>C: 返回数据
    C->>C: 处理数据
    C-->>B: 返回结果
    deactivate C

    B->>B: 后处理
    B-->>A: 返回结果
    deactivate B

    A->>A: 结果组装
    A-->>U: 返回最终结果
    deactivate A
```

**协作要点**：

1. **调用方与组件A**：{说明调用关系和接口契约}
2. **组件A与B**：{说明数据传递格式和处理边界}
3. **组件C与外部服务**：{说明异步/同步策略、超时处理}

---

### 3.4 关键数据路径

#### 主路径（正常流程）

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始输入] --> I2[解析验证]
        I2 --> I3[结构化数据]
    end

    subgraph Process["处理阶段"]
        P1[预处理] --> P2[核心处理]
        P2 --> P3[后处理]
    end

    subgraph Output["输出阶段"]
        O1[结果生成] --> O2[格式化]
        O2 --> O3[副作用执行]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#e1f5e1,stroke:#333
```

#### 异常路径（错误恢复）

```mermaid
flowchart TD
    E[发生错误] --> E1{错误类型}
    E1 -->|可恢复| R1[重试机制]
    E1 -->|不可恢复| R2[降级处理]
    E1 -->|严重错误| R3[终止并告警]

    R1 --> R1A[指数退避重试]
    R1A -->|成功| R1B[继续主路径]
    R1A -->|失败| R2

    R2 --> R2A[返回默认值/缓存]
    R3 --> R3A[记录错误日志]
    R3A --> R3B[通知监控]

    R1B --> End[结束]
    R2A --> End
    R3B --> End

    style R1 fill:#90EE90
    style R2 fill:#FFD700
    style R3 fill:#FF6B6B
```

#### 优化路径（缓存/短路）

```mermaid
flowchart TD
    Start[请求进入] --> Check{缓存检查}
    Check -->|命中| Hit[返回缓存]
    Check -->|未命中| Miss[执行正常流程]
    Miss --> Save[写入缓存]
    Save --> Result[返回结果]
    Hit --> Result

    Start --> Fast{快速路径?}
    Fast -->|是| FastPath[短路处理]
    Fast -->|否| Check
    FastPath --> Result

    style Hit fill:#90EE90
    style FastPath fill:#87CEEB
```

---

## 4. 端到端数据流转

### 4.1 正常流程（详细版）

展示数据如何从输入到输出的完整变换过程。

```mermaid
sequenceDiagram
    participant A as {模块A}
    participant B as {本技术点}
    participant C as {模块C}
    participant D as {模块D}

    A->>B: {触发条件/输入}
    B->>B: {内部处理}
    B->>C: {调用下游}
    C-->>B: {返回结果}
    B->>D: {副作用/输出}
    B-->>A: {最终结果}
```

**数据变换详情**：

| 阶段 | 输入 | 处理 | 输出 | 代码位置 |
|-----|------|------|------|---------|
| 接收 | {input_type} | {validation_logic} | {validated_data} | `{file}:{line}` |
| 处理 | {validated_data} | {core_logic} | {processed_result} | `{file}:{line}` |
| 输出 | {processed_result} | {formatting} | {final_output} | `{file}:{line}` |

### 4.2 数据流向图

```mermaid
flowchart LR
    subgraph Input["输入阶段"]
        I1[原始输入] --> I2[解析验证]
        I2 --> I3[结构化数据]
    end

    subgraph Process["处理阶段"]
        P1[预处理] --> P2[核心处理]
        P2 --> P3[后处理]
    end

    subgraph Output["输出阶段"]
        O1[结果生成] --> O2[格式化]
        O2 --> O3[副作用]
    end

    I3 --> P1
    P3 --> O1

    style Process fill:#f9f,stroke:#333
```

### 4.3 异常/边界流程

```mermaid
flowchart TD
    A[开始] --> B{条件判断}
    B -->|正常| C[正常处理]
    B -->|异常1| D[错误处理1]
    B -->|异常2| E[错误处理2]
    C --> F[结束]
    D --> F
    E --> G[补偿/回滚]
    G --> F
```

---

## 5. 关键代码实现

### 5.1 核心数据结构

```language
// {文件路径}:{行号}
{关键结构定义}
```

**字段说明**：
| 字段 | 类型 | 用途 |
|-----|------|------|
| `{field1}` | `{type1}` | {purpose1} |
| `{field2}` | `{type2}` | {purpose2} |

### 5.2 主链路代码

**关键代码**（核心逻辑）：

```language
// {文件路径}:{起始行}-{结束行}
{核心方法实现（15-25行）}
```

**设计意图**（非逐行解释, 说明关键设计）：
1. **{设计点1}**：{说明为什么这样设计}
2. **{设计点2}**：{说明为什么这样设计}
3. **{设计点3}**：{说明为什么这样设计}

<details>
<summary>📋 查看完整实现</summary>

```language
// {文件路径}:{完整起始行}-{完整结束行}
{完整方法实现（如有需要）}
```

</details>

### 5.3 关键调用链

```text
{entry_method}()          [{entry_file}:{line}]
  -> {method2}()          [{file2}:{line}]
    -> {method3}()        [{file3}:{line}]
      -> {core_method}()  [{core_file}:{line}]
        - {关键操作1}
        - {关键操作2}
        - {关键操作3}
```

---

## 6. 设计意图与 Trade-off

### 6.1 {Project} 的选择

| 维度 | {Project} 的选择 | 替代方案 | 取舍分析 |
|-----|-----------------|---------|---------|
| {维度1} | {choice1} | {alternative1} | {tradeoff1} |
| {维度2} | {choice2} | {alternative2} | {tradeoff2} |
| {维度3} | {choice3} | {alternative3} | {tradeoff3} |

### 6.2 为什么这样设计？

**核心问题**：{design_question}

**{Project} 的解决方案**：
- 代码依据：`{file}:{line}`
- 设计意图：{intent_explanation}
- 带来的好处：
  - {benefit1}
  - {benefit2}
- 付出的代价：
  - {cost1}
  - {cost2}

### 6.3 与其他项目的对比

```mermaid
gitGraph
    commit id: "传统方案"
    branch "{Project}"
    checkout "{Project}"
    commit id: "{Project}选择"
    checkout main
    branch "{Project2}"
    checkout "{Project2}"
    commit id: "{Project2}选择"
    checkout main
    branch "{Project3}"
    checkout "{Project3}"
    commit id: "{Project3}选择"
```

| 项目 | 核心差异 | 适用场景 |
|-----|---------|---------|
| {Project} | {difference1} | {scenario1} |
| {Project2} | {difference2} | {scenario2} |
| {Project3} | {difference3} | {scenario3} |

---

## 7. 边界情况与错误处理

### 7.1 终止条件

| 终止原因 | 触发条件 | 代码位置 |
|---------|---------|---------|
| {reason1} | {condition1} | `{file1}:{line1}` |
| {reason2} | {condition2} | `{file2}:{line2}` |
| {reason3} | {condition3} | `{file3}:{line3}` |

### 7.2 超时/资源限制

```language
// {文件路径}:{行号}
{资源限制相关代码}
```

### 7.3 错误恢复策略

| 错误类型 | 处理策略 | 代码位置 |
|---------|---------|---------|
| {error1} | {strategy1} | `{file1}:{line1}` |
| {error2} | {strategy2} | `{file2}:{line2}` |

---

## 8. 关键代码索引

| 功能 | 文件 | 行号 | 说明 |
|-----|------|------|------|
| 入口 | `{entry_file}` | {line} | {description} |
| 核心 | `{core_file}` | {line} | {description} |
| 配置 | `{config_file}` | {line} | {description} |
| 测试 | `{test_file}` | {line} | {description} |

---

## 9. 延伸阅读

- 前置知识：`{prereq_doc}`
- 相关机制：`{related_doc1}`、`{related_doc2}`
- 深度分析：`{deep_dive_doc}`

---

*✅ Verified: 基于 {project}/{file}:{line} 等源码分析*
*基于版本：{version} | 最后更新：{date}*
```

---

## 使用示例：填写 Agent Loop 模板

### TL;DR 示例

```markdown
## TL;DR

一句话定义：Agent Loop 是 Code Agent 的控制核心，让 LLM 从"一次性回答"变成"多轮执行"。

Kimi CLI 的核心取舍：**命令式 while 循环 + Checkpoint 回滚**
（对比 Gemini CLI 的递归 continuation、Codex 的 Actor 消息驱动）

### 核心要点速览

| 维度 | 关键决策 | 代码位置 |
|-----|---------|---------|
| 核心机制 | while 循环驱动多轮 LLM 调用 | `kimisoul.py:302` |
| 状态管理 | Checkpoint 文件保存对话状态 | `checkpoint.py:156` |
| 错误处理 | 显式 step 上限，超限报错 | `kimisoul.py:629` |
```

### 2.1 整体架构示例（Agent Loop）

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI 入口 / Session Runtime                                   │
│ src/kimi_cli/cli/__init__.py:457                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ 用户输入
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ ▓▓▓ Agent Loop ▓▓▓                                          │
│ src/kimi_cli/soul/kimisoul.py                                │
│ - run()      : 单次 Turn 入口                               │
│ - _turn()    : Checkpoint + 用户消息处理                     │
│ - _agent_loop(): 核心循环（step 计数、compaction）           │
│ - _step()    : 单次 LLM 调用 + 工具执行                      │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ LLM API      │ │ Tool System  │ │ Context      │
│ kosong       │ │ 工具执行     │ │ 状态管理     │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 5.2 主链路代码示例（对应第 5 节）

**关键代码**（核心逻辑）：

```python
# src/kimi_cli/soul/kimisoul.py:302-342
async def _agent_loop(self, context: Context) -> None:
    """核心 Agent 循环：驱动多轮 LLM 调用直到任务完成"""
    step_count = 0
    while True:
        # 1. 检查 step 上限，防止无限循环
        if step_count >= self.config.max_steps_per_turn:
            raise MaxStepsExceeded()

        # 2. 必要时压缩上下文，避免 token 超限
        if context.token_count > self.config.compact_threshold:
            await self._compact_context(context)

        # 3. 执行单步：LLM 调用 + 工具执行
        step_result = await self._step(context)
        step_count += 1

        # 4. 如无工具调用，说明任务完成
        if not step_result.has_tool_calls:
            break
```

**设计意图**：
1. **双层循环设计**：外层 `_turn` 管对话周期，内层 `_agent_loop` 管单次任务迭代
2. **显式 step 计数**：可配置上限，防止无限循环和资源耗尽
3. **前置 compaction**：在调用 LLM 前压缩上下文，确保请求不超限

<details>
<summary>📋 查看完整实现（含异常处理、日志等）</summary>

```python
# src/kimi_cli/soul/kimisoul.py:302-382
async def _agent_loop(self, context: Context) -> None:
    """核心 Agent 循环"""
    step_count = 0
    start_time = time.time()

    while True:
        # 安全检查：step 上限
        if step_count >= self.config.max_steps_per_turn:
            raise MaxStepsExceeded(f"超过最大步数限制: {self.config.max_steps_per_turn}")

        # 性能检查：超时
        if time.time() - start_time > self.config.turn_timeout:
            raise TurnTimeoutError("单次 turn 执行超时")

        # 资源检查：上下文压缩
        if context.token_count > self.config.compact_threshold:
            logger.debug(f"上下文 {context.token_count} tokens，触发压缩")
            await self._compact_context(context)

        # 执行单步
        try:
            step_result = await self._step(context)
            step_count += 1
        except Exception as e:
            logger.error(f"Step 执行失败: {e}")
            raise

        # 完成检查
        if not step_result.has_tool_calls:
            logger.info(f"Agent loop 完成，共 {step_count} 步")
            break
```

</details>

### 6.1 Trade-off 示例（对应第 6 节）

| 维度 | Kimi CLI 的选择 | 替代方案 | 取舍分析 |
|-----|----------------|---------|---------|
| 循环结构 | while 迭代 | Gemini 的递归 continuation | 简单直观易于调试，但状态管理需手动处理 |
| 状态回滚 | Checkpoint 文件 | 无回滚（Codex）/ 内存快照 | 支持对话回滚，但文件副作用不自动回滚 |
| 并发执行 | 并发派发、顺序收集 | 完全并行 | 工具触发可以并发，但结果按序注入保持确定性 |

---

## 模板设计原则

### 1. 结构清晰，分层阅读
- **顶层（TL;DR）**：一句话定义 + 核心要点速览表，30 秒了解全貌
- **中层（架构+机制）**：架构图 + 状态流转，5 分钟理解设计
- **底层（实现细节）**：关键代码 + 折叠完整代码，深度研究时展开

### 2. 代码展示规范
- **关键代码**：15-25 行，展示核心逻辑，必须带行内注释说明设计意图
- **完整代码**：使用 `<details>` 折叠块，需要时展开查看
- **代码索引**：每个关键声明必须有文件路径和行号

### 3. 图表使用原则
| 图表类型 | 使用场景 | 简洁性要求 |
|---------|---------|-----------|
| ASCII 架构图 | 第 2 节系统位置 | 控制在 15 行以内 |
| Mermaid Sequence | 第 2.3/3.3 节交互 | 控制在 6 步以内 |
| Mermaid State | 第 3 节状态流转 | 4-5 个核心状态 |
| Mermaid Flowchart | 第 4 节数据流 | 突出主路径，异常用注释 |

### 4. 对比视角
- 第 6 节必须有与其他项目的对比
- 每个对比维度说明：为什么选择 A 而不是 B

---

## 图表类型选择指南

不同图表适用于展示不同类型的信息，根据内容选择合适的图表类型：

| 图表类型 | 适合展示 | 示例场景 | 模板位置 |
|---------|---------|---------|---------|
| **ASCII 架构图** | 系统层次结构、模块关系、复杂分支 | 展示技术点在系统中的位置、主循环分支 | 第 2.1 节 |
| **Mermaid Sequence** | 时序关系、组件交互、异步流程 | 组件间调用顺序、工具调用时序 | 第 2.3 节、第 3.3 节 |
| **Mermaid State Diagram** | 状态机、生命周期、转换条件 | 组件内部状态流转、连接状态 | 第 3.1/3.2 节 |
| **Mermaid Flowchart** | 状态流转、决策分支、并行处理 | 异常处理分支、数据变换流程 | 第 3.1/3.2 节、第 4.3 节 |
| **Mermaid Graph** | 架构关系、依赖关系、层次结构 | 模块依赖、调用关系 | 第 4.2 节 |
| **ASCII 数据流图** | 分层数据处理、流水线 | 组件内部数据分层处理 | 第 3.1/3.2 节 |
| **表格** | 对比分析、参数说明、索引清单 | 配置项对比、代码位置索引 | 各节说明 |

### 图表绘制建议

1. **先画架构图，再画流程图**：从宏观到微观
2. **颜色标注重点**：使用 Mermaid 的 `style` 语法高亮关键路径
3. **保持简洁**：一张图表达一个核心概念，复杂逻辑拆分为多张图
4. **图文结合**：图表后紧跟简短文字说明，解释关键设计决策

---

## 验证清单

### 结构完整性
- [ ] TL;DR 有一句话说清技术点，附「核心要点速览」表
- [ ] 第 1 节用场景对比说明"为什么需要这个机制"
- [ ] 第 2 节有 ASCII 架构图 + 组件职责表 + 交互时序图
- [ ] 第 3 节有核心组件状态机 + 关键接口表
- [ ] 第 4 节有正常流程 + 异常流程图
- [ ] 第 5 节有核心数据结构 + 关键代码（15-25 行）+ 完整代码折叠块
- [ ] 第 6 节有与其他项目的对比表格
- [ ] 第 7 节有终止条件 + 错误恢复策略表
- [ ] 第 8 节有关键代码索引表

### 代码规范
- [ ] 每个关键声明都有文件路径和行号
- [ ] 关键代码带行内注释说明设计意图
- [ ] 完整代码使用 `<details>` 折叠块
- [ ] 代码片段控制：关键代码 15-25 行，完整代码按需

### 图表规范
- [ ] ASCII 架构图 ≤ 15 行
- [ ] Mermaid Sequence ≤ 6 步
- [ ] 状态图 4-5 个核心状态
- [ ] 图表后紧跟文字说明关键设计决策

---

## 应用该模板的技术点示例

| 技术点 | 核心问题 | 主要对比维度 |
|-------|---------|-------------|
| **Agent Loop** | 如何驱动多轮 LLM 调用 | while/递归/Actor/状态机 |
| **Checkpoint** | 如何保存和恢复对话状态 | 文件/内存/数据库 |
| **Tool System** | 如何定义和执行工具 | YAML/代码/Zod Schema |
| **Context Compaction** | 如何处理 token 超限 | 摘要/截断/分层 |
| **MCP Integration** | 如何集成外部工具 | 协议实现差异 |
| **Safety Control** | 如何防止危险操作 | 沙箱/审批/静态分析 |

---

*模板版本：v3.0 | 适用于：docs/{project}/{编号}-{project}-{技术点}.md*
