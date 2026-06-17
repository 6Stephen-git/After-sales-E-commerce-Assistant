# 大家电退货缺验货与原包装说明

## 背景

- 品类：大家电
- 本单金额：1899元
- 是否已签收：是
- 平台服务标：送货上门

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 1 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 1899 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 0 |

说明：首单购买洗衣机，申请退货。

## 对话记录

- 买家：洗衣机噪音太大，我不要了，退货退款。【上传图片】【发送视频】
- 商家：亲，不好意思。运行视频和外观照我都看到了。
- 买家：视频里能听到吧？反正我不要了，1899全退，你们派人来拉走。
- 商家：亲，收到。验货时的整机外观和原包装情况方便说明一下吗？
- 买家：包装箱拆了扔了，机器还在，你们上门拉走的话需要我补什么材料？
- 商家：亲，理解您不方便，我这边再帮您看下退货验货需要哪些材料。
- 买家：验货要拍什么你列一下，我按你们要求弄。

## 事实证据

- 图/视频解析：运行视频显示滚筒转动，噪音程度主观；外观照未见明显磕碰；买家自述原包装箱已丢弃，无验货签收单或包装替代说明。
- 证据强弱：中
- 物流：是否签收：是；物流是否正常：是；签收后约 72 小时申请；其它物流说明：送货上门已签收
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：缺退货验货材料/原包装替代说明（大家电退货专责缺口；普通开箱视频与运行视频已齐）

## 参考

无

## 期望与禁忌

- 目标方向：大家电退货场景下坚持验货流程，引导补充验货或包装替代类专责材料。
- 关键动作：evidence_request，请求指向验货、包装或签收验收类材料。
- 禁忌：缺验货材料时口头承诺退货退款；重复索要已提供的运行视频。

**expected_report**：

```yaml
action_type: evidence_request
strategy_stage_not: [compensate_close]
actionable_evidence_requests_contains: [验货]
customer_value_channel: none
malicious_risk_level_min: low
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：无
