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

## 内嵌测试用例

1. **完整材料场景**：有订单号、图片、聊天记录，验证可提取 `defect_type`、`has_tag_visible`、`logistics_normal`。
2. **冲突场景**：买家文本称“未收到货”但物流已签收，验证 `red_flags` 包含签收冲突提示。
3. **证据缺失场景**：无图片，仅文本+物流，验证 `missing_evidence` 包含“缺少举证图片”。
4. **边界场景**：空输入 `{}`，验证函数不崩溃，`evidence_quality=low` 且有 `uncertainty_note`。