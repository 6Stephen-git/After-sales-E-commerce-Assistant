# 高本单金额下的品质争议（新客画像）

## 背景

- 品类：大家电
- 本单金额：628元
- 是否已签收：是
- 平台服务标：破损包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 2 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 310 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 1 |

说明：本店新客，仅两笔订单，无老客优待标签。

## 对话记录

- 买家：电饭煲内胆有划痕，我要全额退款，锅我也不退了。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和划痕照我都看到了。
- 买家：视频里很清楚，内胆一道长划痕，这算质量问题吧？628全退。
- 商家：亲，收到。方便再拍一张内胆全景吗？
- 买家：都发了，你们看下吧。六百多块的东西，给个处理意见就行。
- 商家：亲，理解您着急，我这边再核对下。
- 买家：好的，麻烦今天内回复我，我好安排下一步。

## 事实证据

- 图/视频解析：连续开箱视频显示外箱轻微压痕、拆封完整；内胆照显示约 8 厘米浅表划痕，无变形或涂层大面积脱落。
- 证据强弱：高
- 物流：是否签收：是；物流是否正常：是；签收后约 12 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：moderate；可挽回性：resalable（划痕较浅，功能未受影响）
- 缺失材料：无（普通举证已齐：含开箱视频、内胆划痕照、外包装照）

## 参考

无

## 期望与禁忌

- 目标方向：识别本单金额触发的 order 价值通道；快速响应并控制本单损失，不无依据全额退。
- 关键动作：核实事实并给出本单优先的处理思路；可协商部分方案但守赔偿边界。
- 禁忌：按老客策略过度让利；忽视本单金额影响直接全额退。

**expected_report**：

```yaml
customer_value_channel: order
disposition_in: [negotiate, defend]
disposition_not: [compensate]
action_type_not: [monetary_settle]
malicious_risk_level_min: low
actionable_evidence_requests_max: 0
```

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无
