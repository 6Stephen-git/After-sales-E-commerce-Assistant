# 系统架构 — 辅助模式

## 整体分层架构

前端界面 (Vue)
│ HTTP/WebSocket
▼
后端 API 服务 (Python FastAPI)
│
▼
调度控制层 (Controller)
│
▼
Multi-Agent 核心层（纯函数，无状态）
│
▼
数据与工具层 (MySQL / ChromaDB / Redis / API)

## 各层职责

- **前端**：辅助模式下为左右分栏布局（聊天窗口 + AI策略面板），商家手动点击按钮触发分析。智能模式下切换为自动化仪表盘布局。
- **API 层**：FastAPI 提供 RESTful 端点。辅助模式用 HTTP，智能模式用 WebSocket。
- **调度控制层**：根据商家配置的 `mode` 字段加载不同控制器。辅助控制器响应手动触发，智能控制器自动监听消息流。控制器是 Agent 的唯一调用方。
- **Agent 核心层**：每个 Agent 为独立模块，暴露无状态纯函数。不感知调用方是谁，不关心是辅助模式还是智能模式。
- **数据层**：MySQL 存储数据，ChromaDB（可选）存储判例经验向量，Redis 作为缓存和任务队列，平台 API 拉取订单信息，多模态与 LLM API 供 Agent 调用。

## 辅助模式数据流

1. 商家打开纠纷或点击「请求 AI 帮助」→ 前端调用 `POST /analyze`。
2. 后端 AssistedController 从平台 API 拉取全部对话记录和举证材料。
3. Controller 调用 Agent1 → Agent2 → Agent3
4. 分析报告和话术返回前端侧边栏展示。
5. 商家选择话术手动发送。Agent 回到休眠状态，等待下次商家手动触发。

## 智能模式数据流

1. 智能模式启动后，IntelligentController 通过 WebSocket 监听买家消息。
2. 每条消息经轻量决策层分类：客套话直接自动回复；纯文本调 Agent2+Agent3；新图片调 Agent1+Agent2+Agent3 全链路。
3. 检测到需转人工时，Controller 生成移交摘要，暂停自动回复，切换回辅助模式。

## 控制器与 Agent 的关系

- Controller 是调度者，Agent 是被调用方。
- Agent 之间互不调用，数据传递由 Controller 完成。
- 同一套 Agent 代码被两种控制器共享，无需任何修改。

## 模式切换机制

- 商家配置表 `mode` 字段决定当前使用的控制器和前端布局。
- 辅助模式 → 智能模式：商家在设置页点击切换，后端加载 IntelligentController，前端切换为仪表盘。
- 智能模式 → 辅助模式：系统检测到需转人工或商家主动点击接管，后端生成移交摘要，前端切回辅助模式。
- Agent 核心层在切换过程中不受任何影响。