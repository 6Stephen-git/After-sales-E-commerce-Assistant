# 冷冻牛排化冻测温与部分补偿（多步）

> 三期多步 MS-03：化冻包退时效边界 → 测温补齐 → 给部分赔具体元 → 情绪化守界。

## 背景

- 品类：水产肉类/冷冻西冷牛排
- 本单金额：168元
- 是否已签收：是
- 平台服务标：化冻包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 1 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 168 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 0 |

说明：首单买家；本单情绪激动但无职业索赔特征。

## 参考

无

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无

### Step1 化冻主张

#### 对话记录

- 买家：收到摸着软了，冰袋都化了，这能吃吗？【上传图片】
- 商家：亲，化冻情况照看到了，不好意思。
- 买家：168全退！化冻了就是你们的问题！
- 商家：亲，收到，我帮您核实下。
- 买家：别跟我扯时效，反正不能吃！

#### 事实证据

- 图/视频解析：包装内壁有水渍，牛排边缘变色；未见开箱测温照或快递面单同框照。
- 物流：是否签收：是；物流是否正常：是；签收后约 20 小时申请；其它物流说明：冷链包装外观完好。
- 视觉严重度：moderate；可挽回性：unrecoverable
- 缺失材料：开箱测温照；快递面单同框照

#### expected_report

```yaml
strategy_stage_in: [evidence_first]
disposition_in: [defend, negotiate]
action_type_in: [evidence_request, rule_explain]
actionable_evidence_requests_min: 1
malicious_risk_level_min: low
customer_value_channel: none
```

### Step2 测温补齐

#### 对话记录

- 买家：收到摸着软了，冰袋都化了，这能吃吗？【上传图片】
- 商家：亲，化冻情况照看到了，不好意思。
- 买家：168全退！化冻了就是你们的问题！
- 商家：亲，收到，我帮您核实下。
- 买家：别跟我扯时效，反正不能吃！
- 买家：测温笔8度，面单一起拍了。【上传图片】【发送视频】
- 商家：亲，材料收到了。
- 买家：到底赔多少，给个数！

#### 事实证据

- 图/视频解析：测温显示约8℃；约35%表面积化冻；面单与开箱同框完整。
- 物流：是否签收：是；物流是否正常：是；签收后约 20 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：moderate；可挽回性：unrecoverable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [negotiate, compensate]
action_type_in: [monetary_settle, merchant_remedy, rule_explain]
actionable_evidence_requests_max: 0
customer_value_channel: none
malicious_risk_level_min: low
resolution_contract_decision_ready: true
offered_modes_contains: [partial_compensate, return_refund]
proposed_compensation_amount_expected: 50.4
```

### Step3 情绪化全额诉求

#### 对话记录

- 买家：收到摸着软了，冰袋都化了，这能吃吗？【上传图片】
- 商家：亲，化冻情况照看到了，不好意思。
- 买家：168全退！化冻了就是你们的问题！
- 商家：亲，收到，我帮您核实下。
- 买家：别跟我扯时效，反正不能吃！
- 买家：测温笔8度，面单一起拍了。【上传图片】【发送视频】
- 商家：亲，材料收到了。
- 买家：到底赔多少，给个数！
- 买家：50块侮辱人！168全退，今天必须处理！
- 商家：亲，理解您心情，我再看下能怎么处理。
- 买家：今天不退全款我就平台介入！

#### 事实证据

- 图/视频解析：测温与化冻比例与 Step2 一致；证据已齐。
- 物流：是否签收：是；物流是否正常：是；签收后约 22 小时申请；其它物流说明：无。
- 视觉严重度：moderate；可挽回性：unrecoverable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [negotiate, defend, compensate]
action_type_in: [monetary_settle, merchant_remedy, rule_explain]
actionable_evidence_requests_max: 0
customer_value_channel: none
malicious_risk_level_min: low
resolution_contract_decision_ready: true
proposed_compensation_amount_min: 50.4
forbidden_modes_contains: [refund_only]
```
