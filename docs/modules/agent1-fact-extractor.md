# Agent 1 — 事实还原员

## 职责
从纠纷的全部材料中提取结构化事实。输入包括买家文字描述、上传图片、完整聊天记录、物流信息。不判断责任归属，不给出策略建议，只描述“客观上有什么”。

## 输出
`FactOutput`（来自 `schemas.py`）

## 可用工具
- `analyze_image(image_url) → dict`：多模态 API，提取图片中破损、吊牌、背景等视觉特征
- `query_logistics(order_id) → LogisticsInfo`：查询物流签收状态与停滞天数

## 硬约束
- 纯函数：`def extract(materials: dict) -> FactOutput`
- 不读写数据库，不感知当前是辅助模式还是智能模式
- 多模态 API 端点从环境变量获取，禁止硬编码
- 每次调用传入纠纷开始以来的全部材料，不只处理增量
- 字段值不确定时设为 null，并在 `uncertainty_note` 中用一句自然语言说明原因