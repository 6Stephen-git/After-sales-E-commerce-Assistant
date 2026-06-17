# 注入部分补偿判例的协商场景（举证已齐）

## 背景

- 品类：水产肉类
- 本单金额：156元
- 是否已签收：是
- 平台服务标：坏单包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 7 |
| dispute_count | 1 |
| dispute_rate | 0.14 |
| return_rate | 0.12 |
| refund_only_rate | 0.06 |
| avg_order_value | 140 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 4 |

说明：普通买家，无恶意信号，价值画像中性。

## 对话记录

- 买家：大虾到货死了好几只，我要赔偿。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和现状照我都看到了。
- 买家：死了大概三分之一吧，按坏单包退你们得赔，156全退也行。
- 商家：亲，收到。死亡数量方便再确认一下吗？
- 买家：视频里都有，大概死了三分之一，你们看怎么赔合适。
- 商家：亲，理解您心情，我这边再帮您看下。
- 买家：行，那你们给个方案吧，全额退或部分赔都可以谈。

## 事实证据

- 图/视频解析：连续开箱视频显示冰袋部分融化；现状照显示约三成虾体死亡、其余状态尚可。
- 证据强弱：高
- 物流：是否签收：是；物流是否正常：是；签收后约 5 小时申请；其它物流说明：夏季常温时段配送略延迟。
- 视觉严重度：moderate；可挽回性：unrecoverable（死亡部分不可挽回）
- 缺失材料：无（普通举证已齐：含开箱视频、商品现状照、外包装照）

## 参考

- CASE-VAL-01

## 期望与禁忌

- 目标方向：参考部分补偿判例，在证据充分时倾向 negotiate，在配置上限内协商。
- 关键动作：引用判例经验说明部分补偿思路；不突破赔偿上限或无依据全额退。
- 禁忌：忽视判例直接拒绝任何协商；承诺超额补偿。

**expected_report**：

```yaml
similar_cases_min: 1
disposition_in: [negotiate]
disposition_not: [compensate]
reasoning_contains_any: [判例, 经验, 部分]
customer_value_channel: none
actionable_evidence_requests_max: 0
```

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无
