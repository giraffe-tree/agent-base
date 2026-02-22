# SWE-agent 上下文压缩机制

## 是否支持

❌ **不支持 LLM 压缩** - SWE-agent 没有实现 LLM 驱动的上下文压缩机制，仅使用简单的滑动窗口和历史处理器来管理上下文长度。

## 核心设计

**滑动窗口 + 历史处理器**: 不主动压缩，仅通过 `LastNObservations` 保留最近 N 条观察，配合多个 `HistoryProcessor` 进行轻量级内容处理。

## 关键代码位置

| 文件路径 | 职责 |
|---------|------|
| `SWE-agent/sweagent/agent/history_processors.py` | 历史处理器集合 |
| `SWE-agent/sweagent/agent/history_processors.py:25-60` | `LastNObservations` 滑动窗口 |
| `SWE-agent/sweagent/agent/history_processors.py:80-120` | `CacheControlHistoryProcessor` |
| `SWE-agent/sweagent/agent/history_processors.py:140-180` | `ClosedWindowHistoryProcessor` |
| `SWE-agent/sweagent/agent/history_processors.py:200-240` | `RemoveRegex` 正则移除 |

## 处理流程

```
History
    │
    ▼
┌─────────────────┐
│  History        │──► 按顺序应用多个处理器
│  Processors     │
│  Pipeline       │
└─────────────────┘
    │
    ├─► LastNObservations ──► 只保留最近 N 条
    ├─► CacheControl ───────► Claude 缓存控制
    ├─► ClosedWindow ───────► 替换已关闭文件窗口
    └─► RemoveRegex ────────► 正则移除内容
    │
    ▼
Final History (无 LLM 压缩)
```

## 实现细节

### 1. LastNObservations（滑动窗口）

```python
# SWE-agent/sweagent/agent/history_processors.py:25
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class LastNObservations:
    """
    只保留最近 N 条观察记录的简单滑动窗口。

    这是 SWE-agent 最主要的上下文控制机制。
    没有智能压缩，只是简单的截断。
    """
    n: int = 10  # 默认保留最近 10 条

    def process(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """返回最近 N 条历史记录"""
        if len(history) <= self.n:
            return history
        return history[-self.n:]

    def should_warn(self, history: List[Dict[str, Any]]) -> bool:
        """当历史被截断时发出警告"""
        return len(history) > self.n
```

### 2. CacheControlHistoryProcessor（Claude 缓存控制）

```python
# SWE-agent/sweagent/agent/history_processors.py:80
@dataclass
class CacheControlHistoryProcessor:
    """
    为 Claude 模型添加缓存控制标记。

    不是压缩，而是优化 token 使用效率。
    """
    cache_breakpoint_every: int = 5  # 每 5 条设置缓存断点

    def process(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """添加 cache_control 标记到消息"""
        processed = []

        for i, item in enumerate(history):
            new_item = dict(item)

            # 每隔 N 条设置缓存断点
            if i % self.cache_breakpoint_every == 0:
                new_item["cache_control"] = {"type": "ephemeral"}

            processed.append(new_item)

        return processed

    def estimate_cost_savings(self, history: List[Dict[str, Any]]) -> Dict[str, float]:
        """估算缓存带来的成本节省"""
        total_tokens = sum(self._count_tokens(item) for item in history)
        cached_tokens = sum(
            self._count_tokens(item)
            for i, item in enumerate(history)
            if i % self.cache_breakpoint_every == 0
        )

        return {
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "estimated_savings_ratio": cached_tokens / total_tokens if total_tokens > 0 else 0,
        }
```

### 3. ClosedWindowHistoryProcessor

```python
# SWE-agent/sweagent/agent/history_processors.py:140
@dataclass
class ClosedWindowHistoryProcessor:
    """
    替换已关闭文件的窗口内容摘要。

    这是 SWE-agent 最接近"压缩"的功能，
    但只是简单的文本替换，不是 LLM 摘要。
    """
    closed_marker: str = "[Window closed for: {path}]"

    def process(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将已关闭文件的窗口内容替换为标记"""
        processed = []
        open_files: set = set()

        # 第一次遍历：追踪哪些文件是打开的
        for item in history:
            if item.get("action") == "open":
                open_files.add(item.get("path"))
            elif item.get("action") == "close":
                open_files.discard(item.get("path"))

        # 第二次遍历：替换已关闭文件的窗口内容
        for item in history:
            new_item = dict(item)

            if "window_content" in item:
                file_path = item.get("path", "unknown")
                if file_path not in open_files:
                    # 文件已关闭，替换为简单标记
                    new_item["window_content"] = self.closed_marker.format(
                        path=file_path
                    )
                    new_item["_note"] = "Content hidden as window was closed"

            processed.append(new_item)

        return processed
```

### 4. RemoveRegex（正则移除）

```python
# SWE-agent/sweagent/agent/history_processors.py:200
import re
from typing import List, Pattern

@dataclass
class RemoveRegex:
    """
    使用正则表达式移除历史中的特定内容。

    用于移除冗余或不需要的内容。
    """
    patterns: List[str] = None

    def __post_init__(self):
        if self.patterns is None:
            # 默认模式：移除 ANSI 颜色代码、过长空行等
            self.patterns = [
                r"\x1b\[[0-9;]*m",  # ANSI 颜色代码
                r"\n{3,}",          # 3+ 连续换行
                r"\s+$",            # 行尾空格
            ]
        self.compiled: List[Pattern] = [re.compile(p) for p in self.patterns]

    def process(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """应用所有正则替换"""
        processed = []

        for item in history:
            new_item = dict(item)

            if "content" in item and isinstance(item["content"], str):
                content = item["content"]
                for pattern in self.compiled:
                    content = pattern.sub("", content)
                new_item["content"] = content

            if "observation" in item and isinstance(item["observation"], str):
                obs = item["observation"]
                for pattern in self.compiled:
                    obs = pattern.sub("", obs)
                new_item["observation"] = obs

            processed.append(new_item)

        return processed
```

### 5. 历史处理器管道

```python
# SWE-agent/sweagent/agent/history_processors.py:260
class HistoryProcessorPipeline:
    """
    组合多个历史处理器按顺序执行。
    """

    def __init__(self):
        self.processors: List[Any] = [
            RemoveRegex(),                      # 1. 清理格式
            ClosedWindowHistoryProcessor(),     # 2. 替换已关闭窗口
            LastNObservations(n=15),            # 3. 滑动窗口截断
            CacheControlHistoryProcessor(),     # 4. 添加缓存控制
        ]

    def process(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """依次应用所有处理器"""
        result = history
        for processor in self.processors:
            result = processor.process(result)
        return result

    def get_stats(self, original: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取处理统计信息"""
        final = self.process(original)

        original_tokens = sum(self._estimate_tokens(item) for item in original)
        final_tokens = sum(self._estimate_tokens(item) for item in final)

        return {
            "original_messages": len(original),
            "final_messages": len(final),
            "original_tokens": original_tokens,
            "final_tokens": final_tokens,
            "reduction_ratio": 1 - (final_tokens / original_tokens) if original_tokens > 0 else 0,
            "processors_applied": len(self.processors),
        }
```

### 6. 配置示例

```yaml
# SWE-agent/config/default.yaml
history:
  # 是否启用历史处理
  enabled: true

  # 滑动窗口配置
  last_n_observations:
    enabled: true
    n: 15  # 保留最近 15 条

  # 缓存控制（仅 Claude 模型）
  cache_control:
    enabled: true
    breakpoint_every: 5

  # 已关闭窗口处理
  closed_window:
    enabled: true

  # 正则清理
  remove_regex:
    enabled: true
    patterns:
      - "\\x1b\\[[0-9;]*m"  # ANSI 颜色
      - "\\n{3,}"          # 多余空行

  # ❌ 注意：没有 LLM 压缩配置
```

## 设计权衡

### 优点

| 优势 | 说明 |
|------|------|
| **简单可靠** | 无 LLM 调用，无额外成本和延迟 |
| **确定性** | 行为完全可预测，无随机性 |
| **零成本** | 不消耗额外的 LLM token |
| **快速** | 本地正则处理，毫秒级完成 |

### 缺点

| 劣势 | 说明 |
|------|------|
| **无智能压缩** | 无法生成语义摘要，可能丢失重要信息 |
| **粗暴截断** | 滑动窗口直接丢弃旧内容，无选择性 |
| **状态丢失** | 长任务早期的决策和上下文可能丢失 |
| **不适用于长任务** | 复杂多步任务容易丢失关键历史 |

### 与其他 Agent 对比

| 维度 | SWE-agent | Codex | Gemini CLI | OpenCode |
|------|-----------|-------|------------|----------|
| **LLM 压缩** | ❌ 无 | ✅ 有 | ✅ 有 | ✅ 有 |
| **机制** | 滑动窗口 | LLM 摘要 | 两阶段验证 | 双重机制 |
| **成本** | 零 | 中 | 高（双重） | 高（双重） |
| **智能度** | 低 | 高 | 高 | 高 |
| **适用任务** | 短任务 | 长任务 | 长任务 | 长任务 |

### 适用场景

- ✅ 短任务场景（< 20 轮交互）
- ✅ 成本敏感的场景
- ✅ 确定性要求高的自动化任务
- ⚠️ 复杂多文件修改任务
- ❌ 需要长期记忆的长对话

### 设计哲学

SWE-agent 的设计反映了其最初的定位——**软件工程研究工具**：

1. **可复现性优先**: 确定性行为比智能压缩更重要
2. **成本控制**: 避免额外的 LLM 调用开销
3. **简单至上**: 滑动窗口足够应对大多数研究场景
4. **开源生态**: 用户可以根据需要自行扩展

对于需要智能上下文压缩的场景，建议考虑其他 Agent（Codex、Gemini CLI、OpenCode）或自行扩展 SWE-agent 的历史处理器。
