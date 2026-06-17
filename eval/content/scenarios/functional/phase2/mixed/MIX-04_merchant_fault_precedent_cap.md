# 商责破损判例善后与赔偿上限（举证已齐）

## 背景

- 品类：服饰-连衣裙
- 本单金额：268元
- 是否已签收：是
- 平台服务标：破损包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 4 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.08 |
| refund_only_rate | 0.02 |
| avg_order_value | 220 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 3 |

说明：普通买家；物流破损导致裙身明显撕裂，商责较清晰。

## 对话记录

- 买家：裙子收到就有大口子，快递盒子也瘪了，必须处理。【上传图片】【发送视频】
- 商家：亲，真不好意思。开箱视频和破损照我都看到了，确实是我们这边物流问题。
- 买家：那你们怎么赔？268全退或者换一条新的。
- 商家：亲，收到。您更倾向退款还是换货？
- 买家：换货也行，但我不想再等了，退款更快。
- 商家：亲，理解，我这边给您安排方案。
- 买家：行，那你们给个明确说法，别光说在处理。

## 事实证据

- 图/视频解析：连续开箱视频显示外箱明显压瘪；裙身侧边约15厘米撕裂，吊牌完整，属运输破损。
- 物流：是否签收：是；物流是否正常：否；签收后约 3 小时申请；其它物流说明：外箱压痕与内物破损一致。
- 视觉严重度：severe；可挽回性：unrecoverable
- 缺失材料：无（普通举证已齐：含开箱视频、破损照、外包装照）

## 参考

- CASE-MF-01

## 期望与禁忌

- 目标方向：商责明确时参考退货善后判例，主动担责并给出可执行方案；补偿受商家配置上限约束。
- 关键动作：disposition 偏善后/协商；可退货退款或换货，引用判例说明标准流程。
- 禁忌：商责清晰仍推诿；承诺突破配置上限的补偿；重复索要已齐材料。

**expected_report**：

```yaml
similar_cases_min: 1
disposition_in: [compensate, negotiate]
malicious_risk_level_min: low
action_type_in: [merchant_remedy, rule_explain]
actionable_evidence_requests_max: 0
strategy_stage_not: [evidence_first]
```

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无
