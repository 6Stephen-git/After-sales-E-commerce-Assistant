# Agent 4 — 卖家情绪监控员

## 职责
实时分析**卖家**消息文本，识别是否出现过激、攻击性或不专业表达。输出情绪标签、强度与细腻描述，在超过阈值时触发预警。

## 输出
`EmotionOutput`（来自 `schemas.py`）

## 可用工具
- `analyze_seller_emotion(text, chat_history) → dict`：MiMo LLM 结构化输出；失败时降级卖家攻击性关键词匹配

## 硬约束
- 纯函数：`def monitor(text: str, context: dict) -> EmotionOutput`
- **不参与** Agent1→2→3 主链路；由 `POST /emotion/monitor` 在卖家发消息后独立调用
- 仅监控卖家情绪，不向 Agent2/3 传递 `emotion_note`
- 预警阈值可配置（默认 intensity > 0.8 且 sentiment 为 negative 时触发预警）
