# Kimi CLI 为何保留推理内容

**结论**: Kimi CLI 保留 `ThinkPart` 推理内容是为了支持 **D-Mail 时间旅行机制**和**状态回滚**，确保 LLM 在回到过去 checkpoint 时仍能获得当时的完整思考上下文。

---

## 核心原因

### 1. D-Mail 机制依赖

当 Agent 通过 `DenwaRenji.send_dmail()` 发送 D-Mail 到过去的 checkpoint 时：

```python
class DMail(BaseModel):
    message: str        # 要发送的消息内容
    checkpoint_id: int  # 目标 checkpoint
```

LLM 需要看到**当时自己的推理过程**，才能理解为什么要接收来自"未来"的消息。

### 2. 状态回滚的连贯性

`Context.revert_to()` 回滚到指定 checkpoint 时：
- 文件被旋转备份
- 消息历史从 NDJSON 文件重建
- **保留的推理内容让 LLM 能接续之前的思路**

### 3. 加密签名验证

```python
class ThinkPart(BaseModel):
    type: Literal["think"] = "think"
    think: str           # 思考内容
    signature: str | None  # 可加密签名
```

`signature` 字段支持对思考过程进行加密验证，确保推理链的完整性和可追溯性。

---

## 技术实现

**关键代码**:
- `kimi-cli/packages/kosong/src/kosong/message.py` - `ThinkPart` 定义
- `kimi-cli/src/kimi_cli/soul/denwarenji.py` - D-Mail 机制
- `kimi-cli/src/kimi_cli/soul/context.py` - Checkpoint 回滚

---

*2026-02-21*
