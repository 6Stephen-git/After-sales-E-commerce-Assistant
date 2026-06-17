# 高老客生鲜超时效与坏单争议（举证已齐）

## 背景

- 品类：水产肉类
- 本单金额：128元
- 是否已签收：是
- 平台服务标：坏单包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 24 |
| dispute_count | 2 |
| dispute_rate | 0.08 |
| return_rate | 0.10 |
| refund_only_rate | 0.03 |
| avg_order_value | 110 |
| credit_level | high |
| malicious_flags | 0 |
| positive_review_count | 10 |

说明：生鲜品类老客，历史消费高；本次签收后超48小时才申请，主张部分虾体死亡要求全额退。

## 对话记录

- 买家：大虾放冰箱第二天发现死了不少，老客户了，128全退吧。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和现状照我都看到了。
- 买家：死了大概一半，坏单包退你们得管，我买了二十多次了。
- 商家：亲，收到。方便说下签收后大概多久发现的吗？
- 买家：签收第二天晚上才拆的，反正就是坏了，你们看着赔。
- 商家：亲，理解您心情，我这边再帮您看下。
- 买家：那你们给个说法，全额退或部分赔都行，别拖。

## 事实证据

- 图/视频解析：开箱视频显示冰袋部分融化；现状照约半数虾体死亡；外包装完好。
- 物流：是否签收：是；物流是否正常：是；签收后约 56 小时申请；其它物流说明：夏季配送，无异常轨迹。
- 视觉严重度：moderate；可挽回性：unrecoverable（死亡部分不可挽回）
- 缺失材料：无（普通举证已齐：含开箱视频、商品现状照、外包装照）

## 参考

无

## 期望与禁忌

- 目标方向：识别高老客价值通道，同时规则时效已超48小时边界；在价值与规则冲突时守时效边界，不无依据全额退。
- 关键动作：说明签收间隔与规则时效；可协商部分处理但受规则与证据约束。
- 禁忌：因老客身份无视超时效直接全额让步；重复索要已齐材料。

**expected_report**：

```yaml
customer_value_channel: long_term
disposition_in: [defend, negotiate]
disposition_not: [compensate]
malicious_risk_level_min: low
action_type_in: [monetary_settle, rule_explain]
actionable_evidence_requests_max: 0
strategy_stage_not: [evidence_first]
```

## 其他说明

- 老客价值：累计消费高、复购稳定
- 赔偿：不超过订单 30%
- 恶意规则：无
