# 抗辩判例与高仅退画像叠加（举证已齐）

## 背景

- 品类：家装灯饰光源
- 本单金额：245元
- 是否已签收：是
- 平台服务标：破损包赔

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 4 |
| dispute_count | 3 |
| dispute_rate | 0.27 |
| return_rate | 0.38 |
| refund_only_rate | 0.45 |
| avg_order_value | 160 |
| credit_level | medium |
| malicious_flags | 1 |
| positive_review_count | 2 |

说明：高仅退画像；近90天仅退款4次；引用抗辩成功判例。

## 对话记录

- 买家：灯罩又裂了，仅退款245，灯我不寄回。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和裂纹照我都看到了。
- 买家：视频很清楚，我历史订单你们系统都有，别跟我绕，今天必须仅退款。
- 商家：亲，收到。裂纹位置方便再确认一下吗？
- 买家：都发了还要怎样？我熟流程，仅退款到账就行。
- 商家：亲，理解您着急，我这边再看下。
- 买家：别再拖，245一分不能少，不然我申请平台介入！

## 事实证据

- 图/视频解析：开箱视频完整；灯罩约4厘米裂纹，灯座完好，可更换灯罩。
- 物流：是否签收：是；物流是否正常：是；签收后约 8 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：moderate；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含开箱视频、裂纹照、外包装照）

## 参考

- CASE-MAL-01

## 期望与禁忌

- 目标方向：硬规则识别滥用仅退款，同时参考抗辩判例；举证已齐时守住仅退边界。
- 关键动作：similar_cases 注入后策略应引用经验；结合可挽回性说明处理边界。
- 禁忌：忽视高仅退信号与判例直接全额让步；重复索要已齐举证。

**expected_report**：

```yaml
triggered_signal_types_contains: [abuse_refund_only]
malicious_risk_level_min: medium
similar_cases_min: 1
disposition_in: [defend, negotiate]
disposition_not: [compensate]
action_type_in: [rule_explain, defend_prepare]
customer_value_channel: none
actionable_evidence_requests_max: 0
strategy_stage_not: [evidence_first]
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：仅退次数阈值 3；近90天仅退款 4 次
