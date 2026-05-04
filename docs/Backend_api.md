# 后端 API 规范

## 框架
Python FastAPI。辅助模式使用 HTTP REST，智能模式使用 WebSocket。

## 职责边界
- API 层只负责接收请求、校验参数、调用 Controller、返回结果。
- 禁止在端点内编写任何业务逻辑，业务逻辑属于 Controller 和 Agent。
- 不与 Agent 核心层直接耦合，所有调用通过 Controller。

## 通信方式
- 辅助模式：HTTP REST，请求/响应模式，无状态。
- 智能模式（预留）：WebSocket 长连接，服务端可主动推送消息。

## 缓存
- 对话历史由 Controller 维护缓存，首次从平台 API 全量拉取，后续增量追加，存入 Redis。
- 分析报告可缓存，短时间重复请求且对话无新消息时直接返回缓存结果。

## 异步
- Agent 5 复盘分析通过 Celery 异步任务触发，不阻塞用户请求。
- Celery Broker 使用 Redis。

## 异常处理
- 统一异常处理中间件。
- 所有错误返回格式：`{"error": "具体错误描述"}`。
- 不返回 200 状态码包裹的错误信息。