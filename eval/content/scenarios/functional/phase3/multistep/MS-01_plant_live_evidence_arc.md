# 包活绿植定植补证与部分补偿（多步）

> 三期多步 MS-01：15天包活专责补证 → 闭环给部分赔/换苗 → 投诉施压守界。

## 背景

- 品类：鲜花绿植/绿萝盆栽
- 本单金额：58元
- 是否已签收：是
- 平台服务标：15天包活

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 1 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 58 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 0 |

说明：首单新客，无老客标签。

## 参考

无

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无

### Step1 初次申诉

#### 对话记录

- 买家：收到第三天叶子全黄了，你们包活的怎么回事？【上传图片】
- 商家：亲，不好意思，黄叶情况我看到了。
- 买家：58块不多，要么全退要么换一盆好的！
- 商家：亲，收到，我帮您核实下。
- 买家：别拖，今天给说法！

#### 事实证据

- 图/视频解析：静态照显示多片黄叶、盆土湿润；未见定植或开箱连续过程。
- 物流：是否签收：是；物流是否正常：是；签收后约 72 小时申请；其它物流说明：无异常。
- 视觉严重度：moderate；可挽回性：repairable
- 缺失材料：定植开箱连续视频；养护环境说明

#### expected_report

```yaml
strategy_stage_in: [evidence_first]
action_type_in: [evidence_request, rule_explain]
disposition_in: [defend, negotiate]
actionable_evidence_requests_min: 1
malicious_risk_level_min: low
customer_value_channel: none
```

### Step2 补证完成

#### 对话记录

- 买家：收到第三天叶子全黄了，你们包活的怎么回事？【上传图片】
- 商家：亲，不好意思，黄叶情况我看到了。
- 买家：58块不多，要么全退要么换一盆好的！
- 商家：亲，收到，我帮您核实下。
- 买家：别拖，今天给说法！
- 买家：视频拍好了，就是收到当天栽的，阳台有晒。【发送视频】
- 商家：亲，视频收到了，我再核对下。
- 买家：就是你们苗不行，赶紧处理！

#### 事实证据

- 图/视频解析：定植视频连续完整；约六成叶片发黄枯萎，其余尚可；养护环境为阳台直射。
- 物流：是否签收：是；物流是否正常：是；签收后约 72 小时申请；其它物流说明：无异常。
- 视觉严重度：moderate；可挽回性：repairable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [negotiate]
action_type_in: [monetary_settle, rule_explain]
actionable_evidence_requests_max: 0
customer_value_channel: none
malicious_risk_level_min: low
resolution_contract_decision_ready: true
offered_modes_contains: [partial_compensate]
proposed_compensation_amount_expected: 17.4
```

### Step3 投诉施压

#### 对话记录

- 买家：收到第三天叶子全黄了，你们包活的怎么回事？【上传图片】
- 商家：亲，不好意思，黄叶情况我看到了。
- 买家：58块不多，要么全退要么换一盆好的！
- 商家：亲，收到，我帮您核实下。
- 买家：别拖，今天给说法！
- 买家：视频拍好了，就是收到当天栽的，阳台有晒。【发送视频】
- 商家：亲，视频收到了，我再核对下。
- 买家：就是你们苗不行，赶紧处理！
- 买家：十几块钱打发谁呢？我要投诉，不全退没完！
- 商家：亲，理解您着急，我再看下能怎么处理。
- 买家：你们自己看着办，今天必须到账！

#### 事实证据

- 图/视频解析：定植视频连续完整；约六成叶片发黄枯萎；证据与 Step2 一致。
- 物流：是否签收：是；物流是否正常：是；签收后约 72 小时申请；其它物流说明：无异常。
- 视觉严重度：moderate；可挽回性：repairable
- 缺失材料：无

#### expected_report

```yaml
strategy_stage_not: [evidence_first]
disposition_in: [negotiate, defend]
action_type_in: [monetary_settle, rule_explain]
actionable_evidence_requests_max: 0
customer_value_channel: none
malicious_risk_level_in: [low, medium, high]
resolution_contract_decision_ready: true
offered_modes_contains: [partial_compensate]
proposed_compensation_amount_min: 17.4
forbidden_modes_contains: [refund_only]
```
