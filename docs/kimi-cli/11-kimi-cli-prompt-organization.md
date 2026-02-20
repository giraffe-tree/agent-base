# Prompt Organization（kimi-cli）

结论先行：kimi-cli 采用"文件系统 + Jinja2 模板 + Agent 继承体系"的 prompt 组织方式，通过 `src/kimi_cli/prompts/*.md` 文件定义基础模板，支持子 Agent 配置继承（extend 机制），使用 `${}` 语法进行变量注入。

---

## 1. Prompt 组织流程图

```text
+------------------------+
| Prompt 文件目录         |
| (src/kimi_cli/prompts/) |
+-----------+------------+
            |
            v
+------------------------+
| Agent 配置解析          |
| (default.yaml)          |
+-----------+------------+
            |
            v
+------------------------+
| 继承链解析              |
| (extend: parent-agent)  |
+-----------+------------+
            |
            v
+------------------------+
| 模板文件加载            |
| (system.md, etc.)       |
+-----------+------------+
            |
            v
+------------------------+
| 变量上下文组装          |
| (state, env, config)    |
+-----------+------------+
            |
            v
+------------------------+
| Jinja2 渲染             |
| (${variable} 替换)      |
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
│ Layer 4: Runtime Context                             │
│  - 动态状态变量                                       │
│  - 对话历史摘要                                       │
│  - 工具执行结果                                       │
├─────────────────────────────────────────────────────┤
│ Layer 3: Agent-Specific Layer                        │
│  - 子 Agent 特定指令                                  │
│  - 继承父 Agent 并覆盖/扩展                           │
├─────────────────────────────────────────────────────┤
│ Layer 2: Base Agent Layer                            │
│  - 系统身份定义 (system.md)                           │
│  - 基础能力和约束                                     │
├─────────────────────────────────────────────────────┤
│ Layer 1: Configuration Layer                         │
│  - Agent 配置文件 (default.yaml)                      │
│  - 继承关系定义                                       │
└─────────────────────────────────────────────────────┘
```

---

## 3. Prompt 文件位置

| 文件路径 | 职责 |
|---------|------|
| `kimi-cli/src/kimi_cli/prompts/*.md` | 基础 prompt 模板文件 |
| `kimi-cli/src/kimi_cli/prompts/system.md` | 系统身份定义 |
| `kimi-cli/agents/default/` | 默认 Agent 配置目录 |
| `kimi-cli/agents/default/system.md` | 默认 Agent 系统 prompt |
| `kimi-cli/agents/default/config.yaml` | Agent 配置文件 |
| `kimi-cli/agents/{custom}/` | 自定义 Agent 目录 |

---

## 4. 加载与管理机制

### 4.1 Agent 配置结构

```yaml
# agents/default/config.yaml
name: default
description: Default agent for general tasks

# 继承机制（可选）
# extend: base

# Prompt 配置
prompt:
  system: system.md
  template_vars:
    - cwd
    - home
    - files
    - history

# 工具配置
tools:
  - read
  - write
  - bash
  - search
```

### 4.2 继承体系实现

```python
class AgentConfig:
    """Agent 配置类，支持继承"""

    def __init__(self, config_path: str):
        self.raw_config = yaml.safe_load(open(config_path))
        self.inherited_config = self._resolve_inheritance()

    def _resolve_inheritance(self) -> dict:
        """解析继承链，合并配置"""
        if 'extend' not in self.raw_config:
            return self.raw_config

        parent_name = self.raw_config['extend']
        parent_config = self._load_parent(parent_name)

        # 子配置覆盖父配置
        return self._merge_configs(parent_config, self.raw_config)

    def _merge_configs(self, parent: dict, child: dict) -> dict:
        """合并父配置和子配置"""
        merged = deepcopy(parent)
        for key, value in child.items():
            if key == 'extend':
                continue
            if key in merged and isinstance(merged[key], dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged
```

### 4.3 Prompt 文件加载

```python
class PromptLoader:
    """Prompt 文件加载器"""

    def __init__(self, agents_dir: str):
        self.agents_dir = Path(agents_dir)
        self.core_prompts_dir = Path(__file__).parent / 'prompts'

    def load_system_prompt(self, agent_name: str) -> str:
        """加载指定 Agent 的系统 prompt"""
        agent_dir = self.agents_dir / agent_name
        system_file = agent_dir / 'system.md'

        if system_file.exists():
            return system_file.read_text()

        # 回退到默认
        default_file = self.core_prompts_dir / 'system.md'
        return default_file.read_text()
```

---

## 5. 模板与变量系统

### 5.1 Jinja2 模板语法（${} 风格）

```markdown
# system.md 示例

You are Kimi, a helpful AI assistant.

Current directory: ${cwd}
User home: ${home}

{% if files %}
Relevant files:
{% for file in files %}
- ${file.path}: ${file.description}
{% endfor %}
{% endif %}

{% if history %}
Previous conversation:
${history_summary}
{% endif %}
```

### 5.2 变量类型

| 变量名 | 类型 | 说明 |
|-------|------|------|
| `cwd` | string | 当前工作目录 |
| `home` | string | 用户主目录 |
| `files` | list | 相关文件列表 |
| `history` | list | 对话历史 |
| `history_summary` | string | 历史摘要 |
| `tools` | list | 可用工具 |
| `env` | dict | 环境变量 |

### 5.3 变量注入流程

```python
class PromptRenderer:
    """Prompt 渲染器"""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.jinja_env = Environment(
            variable_start_string='${',
            variable_end_string='}',
        )

    def render(self, template_str: str, context: dict) -> str:
        """渲染模板"""
        template = self.jinja_env.from_string(template_str)
        return template.render(**context)

    def build_context(self, runtime_state: dict) -> dict:
        """构建渲染上下文"""
        return {
            'cwd': os.getcwd(),
            'home': os.path.expanduser('~'),
            'files': runtime_state.get('files', []),
            'history': runtime_state.get('history', []),
            'history_summary': self._summarize_history(),
            'tools': self.config.get_tools(),
            'env': dict(os.environ),
        }
```

---

## 6. Prompt 工程方法

### 6.1 多 Agent 架构

```
agents/
├── default/
│   ├── config.yaml
│   └── system.md
├── code-reviewer/
│   ├── config.yaml
│   │   └── extend: default
│   └── system.md
├── test-writer/
│   ├── config.yaml
│   │   └── extend: default
│   └── system.md
└── doc-writer/
    ├── config.yaml
    │   └── extend: default
    └── system.md
```

### 6.2 子 Agent 覆盖示例

```yaml
# agents/code-reviewer/config.yaml
name: code-reviewer
extend: default
description: Specialized agent for code review

prompt:
  system: system.md
  template_vars:
    - cwd
    - files
    - diff
    - review_criteria
```

```markdown
# agents/code-reviewer/system.md

${parent_system_prompt}

## Additional Instructions for Code Review

When reviewing code:
1. Check for security vulnerabilities
2. Verify coding standards compliance
3. Suggest performance improvements
4. Ensure test coverage
```

### 6.3 动态上下文管理

```python
def get_relevant_files(query: str, max_files: int = 10) -> list:
    """根据查询获取相关文件"""
    # 使用向量搜索或关键词匹配
    all_files = scan_project_files()
    scored_files = score_relevance(all_files, query)
    return scored_files[:max_files]

def inject_context(prompt: str, context: dict) -> str:
    """将上下文注入 prompt"""
    files = get_relevant_files(context['query'])
    context['files'] = files
    return render_template(prompt, context)
```

---

## 7. 证据索引

- `kimi-cli` + `kimi-cli/src/kimi_cli/prompts/*.md` + 基础 prompt 模板文件
- `kimi-cli` + `kimi-cli/src/kimi_cli/prompts/system.md` + 系统身份定义
- `kimi-cli` + `kimi-cli/agents/default/` + 默认 Agent 配置
- `kimi-cli` + `kimi-cli/agents/default/config.yaml` + Agent 配置和继承定义
- `kimi-cli` + `docs/kimi-cli/04-kimi-cli-agent-loop.md` + Agent 循环中的 prompt 使用

---

## 8. 边界与不确定性

- Agent 继承的具体合并策略（深度合并 vs 浅层覆盖）需验证
- 模板变量的完整列表需以实码为准
- 子 Agent 配置文件的具体字段名可能有所调整

