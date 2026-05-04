# Agent 4 — 情绪监控员

## 职责
实时分析商家或买家的消息文本，识别当前情绪状态。除了输出基础情绪标签和强度外，更重要的是**用一小段自然语言细腻描述当前的情绪氛围**，捕捉那些简单的正面/负面标签无法表达的情绪细节（例如：顾客从愤怒转为勉强接受，或表现出失望但保持礼貌）。这将是系统生成最恰当话术的核心依据。

## 输出
`EmotionOutput`（来自 `schemas.py`）

## 可用工具
- `analyze_sentiment(text) → dict`：本地 BERT 模型，返回基础情绪标签（negative/neutral/positive）和强度（0-1）

## 硬约束
- 纯函数：`def monitor(text: str, context: dict) -> EmotionOutput`
- 仅使用本地 BERT 模型获取基础情绪信号，严禁调用 LLM API 做情感分析
- **细腻情绪描述 (`emotion_note`) 的生成是 Agent 4 的核心任务**：必须结合对话上下文 (`context`)，用自然语言精准概括当前的情绪状态、潜在期待和风险点，例如：即使基础标签为 positive，也要在描述中区分“真心满意”、“勉强接受”或“礼貌但不满”，为后续话术提供方向
- 模型在首次加载后常驻内存，后续调用复用，不重复加载
- 预警阈值可配置（默认 intensity > 0.8 且 sentiment 为 negative 时触发预警）