# 送装书架划痕验收与退货方案（多步）

> 三期多步 MS-02：送装一体商责划痕 → 验收材料补齐 → 给退货上门方案 → 抗仅退要挟。

## 背景

- 品类：住宅家具/实木书架
- 本单金额：420元
- 是否已签收：是
- 平台服务标：送装一体

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 3 |
| dispute_count | 1 |
| dispute_rate | 0.33 |
| return_rate | 0.20 |
| refund_only_rate | 0.10 |
| avg_order_value | 280 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 2 |

说明：普通买家，偶有退货记录。

## 参考

无

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无

### Step1 划痕主张

#### 对话记录

- 买家：师傅装完侧面一大道划痕，420白花了。【上传图片】
- 商家：亲，划痕照看到了，真抱歉。
- 买家：我不要了，钱退我，书架空着你们别管了。
- 商家：亲，收到，我帮您处理。
- 买家：反正我不退货，你们赔420！

#### 事实证据

- 图/视频解析：侧板约12厘米明显划痕，安装现场可见；未见整机六面照或验收签字单。
- 物流：是否签收：是；物流是否正常：是；签收后约 6 小时申请；其它物流说明：送货上门安装完成。
- 视觉严重度：severe；可挽回性：unrecoverable
- 缺失材料：整机六面照；安装验收签字单

#### expected_report

```yaml
strategy_stage_in: [evidence_first]
disposition_in: [compensate, negotiate, defend]
action_type_in: [merchant_remedy, evidence_request, rule_explain]
actionable_evidence_requests_min: 1
malicious_risk_level_min: low
customer_value_channel: none
```

### Step2 验收补齐

#### 对话记录

- 买家：师傅装完侧面一大道划痕，420白花了。【上传图片】
- 商家：亲，划痕照看到了，真抱歉。
- 买家：我不要了，钱退我，书架空着你们别管了。
- 商家：亲，收到，我帮您处理。
- 买家：反正我不退货，你们赔420！
- 买家：六面照和签字单都发了，划痕你们自己看。【上传图片】【发送视频】
- 商家：亲，材料齐了，我再确认下。
- 买家：到底退不退？给个准话！

#### 事实证据

- 图/视频解析：六面照与安装验收签字单齐全；侧板划痕严重，影响二次销售。
- 物流：是否签收：是；物流是否正常：是；签收后约 8 小时申请；其它物流说明：送装完成。
- 视觉严重度：severe；可挽回性：unrecoverable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [compensate, negotiate]
action_type_in: [merchant_remedy, rule_explain]
actionable_evidence_requests_max: 0
malicious_risk_level_min: low
customer_value_channel: none
resolution_contract_decision_ready: true
offered_modes_contains: [return_refund]
```

### Step3 仅退要挟

#### 对话记录

- 买家：师傅装完侧面一大道划痕，420白花了。【上传图片】
- 商家：亲，划痕照看到了，真抱歉。
- 买家：我不要了，钱退我，书架空着你们别管了。
- 商家：亲，收到，我帮您处理。
- 买家：反正我不退货，你们赔420！
- 买家：六面照和签字单都发了，划痕你们自己看。【上传图片】【发送视频】
- 商家：亲，材料齐了，我再确认下。
- 买家：到底退不退？给个准话！
- 买家：我不想折腾退货，你们直接打420，货我不要了。
- 商家：亲，理解，我帮您看下方案。
- 买家：不给仅退我就差评，你们自己选！

#### 事实证据

- 图/视频解析：验收材料齐全，划痕严重；与 Step2 一致。
- 物流：是否签收：是；物流是否正常：是；签收后约 10 小时申请；其它物流说明：无。
- 视觉严重度：severe；可挽回性：unrecoverable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [defend, negotiate]
action_type_in: [merchant_remedy, rule_explain]
actionable_evidence_requests_max: 0
malicious_risk_level_in: [low, medium, high]
customer_value_channel: none
resolution_contract_decision_ready: true
offered_modes_contains: [return_refund]
forbidden_modes_contains: [refund_only]
```
