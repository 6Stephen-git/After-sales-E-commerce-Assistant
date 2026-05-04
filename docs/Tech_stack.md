# 技术选型

## 后端

- **框架**：Python FastAPI
- **任务队列**：Celery + Redis
- **通信**：辅助模式 HTTP REST API，智能模式升级 WebSocket

## 前端

- **框架**：Vue 3
- **UI 组件库**：Element Plus

## 数据库

- **主数据库**：MySQL
- **向量数据库**：ChromaDB（可选，用于判例经验文本的语义检索。MVP 阶段不部署，Agent 2 默认使用 SQL 标签检索）

## AI 模型

- **多模态（图片分析）**：qwen-vl-max，用于 Agent 1
- **LLM（文本推理生成）**：小米 MiMo（OpenAI 兼容），mimo-v2.5-pro 用于 Agent 2，mimo-v2.5 用于 Agent 3、Agent 5
- **情感分析（本地推理）**：BERT-base 微调模型，CPU 运行，用于 Agent 4

## 外部 API

- **平台 API**：千牛开放平台，用于拉取订单、物流、买家信息
- **Embedding API**：text-embedding-v4（智能模式启用）

## 数据隔离

- 商家数据按 merchant_id 逻辑隔离
- 买家敏感信息（手机号）SHA256 哈希脱敏
- 恶意买家标记跨商家共享计数，不暴露具体商家