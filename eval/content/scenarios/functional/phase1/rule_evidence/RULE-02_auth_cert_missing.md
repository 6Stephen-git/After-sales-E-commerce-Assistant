# 先鉴后发珠宝缺鉴定证书与标签照

## 背景

- 品类：珠宝/翡翠
- 本单金额：3280元
- 是否已签收：是
- 平台服务标：先鉴后发

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 2 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 2100 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 1 |

说明：高客单珠宝，买家质疑与描述不符。

## 对话记录

- 买家：手镯颜色跟直播差很多，我要退款。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和手镯照我都看到了。
- 买家：实物偏暗，跟直播间不一样，3280全退。
- 商家：亲，收到。鉴定证书和防伪标签方便拍一下吗？
- 买家：证书？发货时盒子里好像没有单独纸质证书。
- 商家：亲，理解您感受，我这边再帮您核对下先鉴后发材料。
- 买家：那你们查下发货记录，缺什么材料跟我说，我配合补。

## 事实证据

- 图/视频解析：连续开箱视频显示包装完好；手镯外观照清晰，颜色因拍摄光线存在差异；未见鉴定证书扫描件或防伪标签特写。
- 证据强弱：中
- 物流：是否签收：是；物流是否正常：是；签收后约 16 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：缺鉴定证书/防伪标签照（先鉴后发专责缺口；普通开箱视频与商品外观照已齐）

## 参考

无

## 期望与禁忌

- 目标方向：先鉴后发场景下要求专责鉴定/标签类材料，再进入处置。
- 关键动作：evidence_request 且请求含鉴定或标签类关键词。
- 禁忌：缺专责材料时直接承诺全额退；仅泛化索要「更多照片」。

**expected_report**：

```yaml
action_type: evidence_request
strategy_stage_not: [compensate_close]
actionable_evidence_requests_contains: [鉴定]
customer_value_channel: none
malicious_risk_level_min: low
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：无
