# Prompt Organization（swe-agent）

结论先行：swe-agent 采用"配置驱动 + Jinja2 模板引擎"的 prompt 组织方式，通过 YAML 配置文件定义多类模板（system/instance/next_step/strategy），支持运行时动态渲染和多级继承。

---

## 1. Prompt 组织流程图

```text
+------------------------+
| YAML 配置文件           |
| (config/*.yaml)         |
+-----------+------------+
            |
            v
+------------------------+
| Pydantic 模型解析       |
| (TemplateConfig)        |
+-----------+------------+
            |
            v
+------------------------+
| 模板类型选择            |
| (system/instance/       |
|  next_step/strategy)    |
+-----------+------------+
            |
            v
+------------------------+
| 变量上下文组装          |
| (state, environment,    |
|   problem info)         |
+-----------+------------+
            |
            v
+------------------------+
| Jinja2 渲染             |
| (模板变量替换)           |
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
│ Template Type: strategy                              │
│  - 高层解决策略                                       │
│  - 问题分解思路                                       │
├─────────────────────────────────────────────────────┤
│ Template Type: next_step                             │
│  - 下一步行动指导                                     │
│  - 基于当前状态的决策提示                             │
├─────────────────────────────────────────────────────┤
│ Template Type: instance                              │
│  - 特定问题实例描述                                   │
│  - 代码库上下文                                       │
├─────────────────────────────────────────────────────┤
│ Template Type: system                                │
│  - 系统身份定义                                      │
│  - 核心能力和约束                                    │
│  - 工具使用说明                                      │
└─────────────────────────────────────────────────────┘
```

---

## 3. Prompt 文件位置

| 文件路径 | 职责 |
|---------|------|
| `swe-agent/config/` | YAML 配置文件目录，按场景组织 |
| `swe-agent/config/default.yaml` | 默认配置，基础模板定义 |
| `swe-agent/config/codebase.yaml` | 代码库特定配置 |
| `swe-agent/sweagent/agent/` | Agent 实现，模板渲染逻辑 |
| `swe-agent/sweagent/agent/prompts/` | Prompt 辅助函数和工具 |

---

## 4. 加载与管理机制

### 4.1 YAML 配置结构

```yaml
# config/default.yaml 示例结构
templates:
  system: |
    You are a software engineering assistant.
    {{ extra_instructions }}

  instance: |
    Problem: {{ problem_statement }}
    Repository: {{ repo_name }}
    Files: {{ file_context }}

  next_step: |
    Based on the current state:
    {{ state_summary }}
    What should be the next action?

  strategy: |
    Approach to solve this issue:
    {{ strategy_hint }}

# 变量定义
variables:
  - problem_statement
  - repo_name
  - file_context
  - state_summary
  - strategy_hint
```

### 4.2 Pydantic 模型解析

```python
from pydantic import BaseModel
from typing import Dict, Optional

class TemplateConfig(BaseModel):
    """模板配置模型"""
    templates: Dict[str, str]
    variables: Optional[Dict[str, any]] = None
    extends: Optional[str] = None  # 继承其他配置
```

### 4.3 配置继承机制

```text
base.yaml
    │
    ├── extends: codebase.yaml
    │       │
    │       └── 覆盖/扩展基础模板
    │
    └── extends: test.yaml
            │
            └── 测试场景特定模板
```

---

## 5. 模板与变量系统

### 5.1 Jinja2 模板语法

```text
{{ variable }}           {# 变量插值 #}
{% if condition %}...{% endif %}   {# 条件渲染 #}
{% for file in files %}...{% endfor %}  {# 循环渲染 #}
{{ variable | filter }}  {# 过滤器处理 #}
```

### 5.2 变量上下文结构

```python
prompt_context = {
    # 问题相关
    "problem_statement": issue_body,
    "repo_name": repository.full_name,
    "file_context": get_relevant_files(),

    # 状态相关
    "state_summary": agent.state.summary(),
    "history": conversation_history,
    "previous_actions": executed_actions,

    # 环境相关
    "workspace_path": env.cwd,
    "available_tools": tool_descriptions,
    "lint_results": linter.output if linter else None,

    # 策略相关
    "strategy_hint": strategy_planner.hint(),
}
```

### 5.3 常用过滤器

| 过滤器 | 用途 |
|-------|------|
| `truncate` | 截断长文本 |
| `indent` | 代码缩进处理 |
| `tojson` | JSON 序列化 |
| `escape` | 特殊字符转义 |

---

## 6. Prompt 工程方法

### 6.1 多模板组合策略

```python
def build_full_prompt(config: TemplateConfig, context: dict) -> str:
    """组合多个模板生成完整 prompt"""

    # 1. 系统层
    system_prompt = render_template(
        config.templates['system'],
        context
    )

    # 2. 实例层
    instance_prompt = render_template(
        config.templates['instance'],
        context
    )

    # 3. 策略层（可选）
    if 'strategy' in config.templates:
        strategy_prompt = render_template(
            config.templates['strategy'],
            context
        )
        instance_prompt = f"{strategy_prompt}\n\n{instance_prompt}"

    # 4. 下一步指导（用于决策）
    next_step_prompt = render_template(
        config.templates['next_step'],
        context
    )

    return combine_prompts([
        system_prompt,
        instance_prompt,
        next_step_prompt
    ])
```

### 6.2 动态工具描述

```python
def render_tool_descriptions(tools: List[Tool]) -> str:
    """根据可用工具动态生成描述"""
    template = """
Available tools:
{% for tool in tools %}
- {{ tool.name }}: {{ tool.description }}
  Args: {{ tool.args_schema | tojson }}
{% endfor %}
"""
    return Template(template).render(tools=tools)
```

### 6.3 上下文压缩策略

```text
原始上下文 → 相关性评分 → 截断/摘要 → 注入模板
                ↑
         基于问题关键词
         基于文件依赖图
```

---

## 7. 实际示例

### 示例 1：Bug 修复 - Django Issue

**场景设定**：处理 GitHub Issue，django/django 项目中 `get_user_model()` 函数在使用自定义用户模型时返回错误。

**运行时变量值**：
```json
{
  "repo_name": "django/django",
  "problem_statement": "get_user_model() returns wrong model when using custom user model with swappable dependency",
  "file_context": [
    {"path": "django/contrib/auth/__init__.py", "description": "Contains get_user_model() function"},
    {"path": "django/contrib/auth/models.py", "description": "User model definitions"},
    {"path": "tests/auth_tests/models.py", "description": "Test models for auth"}
  ],
  "strategy_hint": "Check how get_user_model() resolves the swappable dependency",
  "state_summary": "Initial state. Need to understand the issue and locate relevant code."
}
```

**完整渲染结果（发送给模型的 Prompt）**：

```markdown
You are a software engineering assistant specialized in fixing bugs.
You have access to a code sandbox where you can:
- View and edit files
- Run tests and commands
- Search the codebase

Follow best practices:
- Write minimal, focused changes
- Ensure tests pass
- Follow existing code style

Repository: django/django
Problem: get_user_model() returns wrong model when using custom user model with swappable dependency

Relevant files:
- django/contrib/auth/__init__.py: Contains get_user_model() function
- django/contrib/auth/models.py: User model definitions
- tests/auth_tests/models.py: Test models for auth

Approach to solve this issue:
Check how get_user_model() resolves the swappable dependency

Break down the problem:
1. Understand the expected behavior
2. Find the problematic code
3. Create a minimal reproduction
4. Implement and verify the fix

Current state:
Initial state. Need to understand the issue and locate relevant code.

Based on the current progress, what should be the next action?
Choose from: view, edit, search, run_tests, submit
```

---

### 示例 2：功能实现 - 新增 API 端点

**场景设定**：在 Flask 项目中添加用户搜索 API 端点，已进行了部分工作。

**运行时变量值**：
```json
{
  "repo_name": "mycompany/flask-api",
  "problem_statement": "Add a new API endpoint /api/v1/users/search that allows searching users by email with pagination",
  "file_context": [
    {"path": "app/routes/users.py", "description": "Existing user routes"},
    {"path": "app/models/user.py", "description": "User model with SQLAlchemy"},
    {"path": "app/schemas/user.py", "description": "User serialization schemas"}
  ],
  "strategy_hint": "Follow existing route patterns, add pagination using existing utility",
  "state_summary": "Viewed existing routes and models. Understand the pattern. Ready to implement the new endpoint."
}
```

**完整渲染结果（发送给模型的 Prompt）**：

```markdown
You are a software engineering assistant specialized in implementing features.
You have access to a code sandbox where you can:
- View and edit files
- Run tests and commands
- Search the codebase

Follow best practices:
- Write minimal, focused changes
- Ensure tests pass
- Follow existing code style

Repository: mycompany/flask-api
Problem: Add a new API endpoint /api/v1/users/search that allows searching users by email with pagination

Relevant files:
- app/routes/users.py: Existing user routes
- app/models/user.py: User model with SQLAlchemy
- app/schemas/user.py: User serialization schemas

Approach to solve this issue:
Follow existing route patterns, add pagination using existing utility

Break down the problem:
1. Understand the existing route structure
2. Add the search endpoint
3. Implement pagination
4. Add tests
5. Verify the implementation

Current state:
Viewed existing routes and models. Understand the pattern. Ready to implement the new endpoint.

Based on the current progress, what should be the next action?
Choose from: view, edit, search, run_tests, submit
```

---

### 示例 3：测试修复 - 失败的单元测试

**场景设定**：修复一个因重构导致的测试失败，已经进行了一些修复尝试。

**运行时变量值**：
```json
{
  "repo_name": "acme/payment-service",
  "problem_statement": "Test test_process_payment_refund is failing after the refactor in PR #234",
  "file_context": [
    {"path": "tests/test_payment.py", "description": "Failing test file"},
    {"path": "src/payment/processor.py", "description": "Payment processor implementation"},
    {"path": "src/payment/refund.py", "description": "Refund handling module"}
  ],
  "strategy_hint": "Check if the test mocks need updating after the refactor",
  "state_summary": "Ran tests and confirmed failure. Error shows AttributeError: 'Mock' object has no attribute 'refund_id'. Previous action: viewed test file."
}
```

**完整渲染结果（发送给模型的 Prompt）**：

```markdown
You are a software engineering assistant specialized in fixing tests.
You have access to a code sandbox where you can:
- View and edit files
- Run tests and commands
- Search the codebase

Follow best practices:
- Write minimal, focused changes
- Ensure tests pass
- Follow existing code style

Repository: acme/payment-service
Problem: Test test_process_payment_refund is failing after the refactor in PR #234

Relevant files:
- tests/test_payment.py: Failing test file
- src/payment/processor.py: Payment processor implementation
- src/payment/refund.py: Refund handling module

Approach to solve this issue:
Check if the test mocks need updating after the refactor

Break down the problem:
1. Understand the test failure
2. Compare test with implementation
3. Update mocks or assertions
4. Run tests to verify fix

Current state:
Ran tests and confirmed failure. Error shows AttributeError: 'Mock' object has no attribute 'refund_id'. Previous action: viewed test file.

Based on the current progress, what should be the next action?
Choose from: view, edit, search, run_tests, submit
```

---

## 8. 证据索引

- `swe-agent` + `swe-agent/config/` + YAML 配置文件目录，模板定义
- `swe-agent` + `swe-agent/sweagent/agent/models.py` + Pydantic 配置模型定义
- `swe-agent` + `swe-agent/sweagent/agent/prompts.py` + Prompt 渲染工具和辅助函数
- `swe-agent` + `swe-agent/sweagent/agent/agents/` + Agent 实现，模板调用逻辑
- `swe-agent` + `docs/swe-agent/04-swe-agent-agent-loop.md` + Agent 循环中的 prompt 注入点

---

## 9. 边界与不确定性

- YAML 配置的具体字段名需以实码为准
- Jinja2 模板版本和扩展功能需核对依赖
- 配置继承的具体合并策略（覆盖 vs 追加）需验证

