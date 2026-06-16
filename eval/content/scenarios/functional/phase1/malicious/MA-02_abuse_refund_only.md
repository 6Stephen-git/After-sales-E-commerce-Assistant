# 高仅退画像下的举证已齐仅退款申请

## 背景

- 品类：家装灯饰光源
- 本单金额：268元
- 是否已签收：是
- 平台服务标：破损包赔

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 9 |
| dispute_count | 2 |
| dispute_rate | 0.22 |
| return_rate | 0.40 |
| refund_only_rate | 0.44 |
| avg_order_value | 155 |
| credit_level | medium |
| malicious_flags | 1 |
| positive_review_count | 2 |

说明：近90天在本店及他店合计4次仅退款；购买次数不高，无老客优待标签。

## 对话记录

- 买家：灯罩裂了，我要仅退款，灯我不退了，你们看着办。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和照片我都看到了。
- 买家：视频里很清楚吧？裂纹那么明显，赶紧仅退款268。
- 商家：亲，收到。灯罩裂纹位置方便再拍一张近照吗？
- 买家：都发了还要怎样？我历史订单你们系统里都有，别跟我扯流程，今天必须仅退款到账。
- 商家：亲，理解您着急，我这边再核对下材料。
- 买家：别再拖了，我就是要仅退款，268一分不能少！

## 事实证据

- 图/视频解析：连续开箱视频显示外包装有轻微压痕、拆封过程完整；静态图片显示吸顶灯灯罩一处约5厘米放射状裂纹，灯座与光源组件外观完好。
- 证据强弱：高
- 物流：是否签收：是；物流是否正常：是；签收后约 6 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：moderate；可挽回性：resalable（灯罩可更换，底座完好）
- 缺失材料：无（普通举证已齐：含开箱视频、灯罩裂纹照、外包装照）

## 参考

无

## 期望与禁忌

- 目标方向：结合高仅退画像与硬规则识别滥用仅退款风险；举证已齐时评估瑕疵程度，不无依据承诺全额仅退。
- 关键动作：引用已收材料说明可挽回性；抗辩或协商，聚焦本次证据与规则边界。
- 禁忌：忽视高仅退硬规则信号直接全额让步；重复索要已齐的普通举证。

**expected_report**：

```yaml
triggered_signal_types_contains: [abuse_refund_only]
malicious_risk_level_min: medium
disposition_in: [defend, negotiate]
disposition_not: [compensate]
action_type_not: [monetary_settle]
strategy_stage_not: [evidence_first]
customer_value_channel: none
actionable_evidence_requests_max: 0
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：仅退次数阈值 3；近90天仅退款 4 次
