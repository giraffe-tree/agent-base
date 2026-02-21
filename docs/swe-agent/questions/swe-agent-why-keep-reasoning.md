# SWE-agent 为何保留推理内容

**结论**: SWE-agent 保留 `thinking_blocks` 推理内容是为了支持 **forward_with_handling 的错误恢复**和**多轮尝试的最佳选择**，使 LLM 在代码修复任务中能自我纠错并从中学习。

---

## 核心原因

### 1. 错误恢复与 Requery

```python
def forward_with_handling(self, history: list[dict[str, str]]) -> StepOutput:
    """Forward the model and handle errors, requerying the model if we can."""
    n_format_fails = 0
    while n_format_fails < self.max_requeries:
        try:
            return self.forward(history)
        except FormatError as e:
            # 基于原推理内容重新查询
            history = handle_error_with_retry(...)
```

保留推理内容让 LLM 在 **FormatError、BashIncorrectSyntaxError** 等错误时：
- 看到**自己原来的思考过程**
- 理解哪里出错了
- 生成修正后的 action

### 2. Autosubmit 紧急恢复

```python
def attempt_autosubmission_after_error(self, step: StepOutput) -> StepOutput:
    """即使环境崩溃，也尝试从 trajectory 中提取 patch"""
```

`thinking_blocks` 保存在 `HistoryItem` 中，即使执行失败也能用于：
- 分析失败原因
- 生成部分提交
- 为下一次尝试提供参考

### 3. 多轮尝试的最佳选择

```python
class ScoreRetryLoop:
    def get_best(self) -> int | None:
        """从多次尝试中选择最佳结果"""
        # 比较推理内容和执行结果
```

Reviewer 可以基于各轮次的 `thinking_blocks`：
- 评估哪一轮的推理最合理
- 比较不同策略的有效性
- 选择最短且有效的 trajectory

### 4. History Processors 的智能过滤

```python
class LastNObservations:
    """Elide all but the last n observations"""
    always_keep_output_for_tags: set[str] = {"keep_output"}
```

通过 `tags` 标记保留关键的推理内容，在截断历史时确保重要思考不丢失。

---

## 技术实现

**关键代码**:
- `SWE-agent/sweagent/agent/agents.py` - `forward_with_handling`
- `SWE-agent/sweagent/types.py` - `HistoryItem.thinking_blocks`
- `SWE-agent/sweagent/agent/reviewer.py` - `ScoreRetryLoop`
- `SWE-agent/sweagent/agent/history_processors.py` - 历史处理器

---

*2026-02-21*
