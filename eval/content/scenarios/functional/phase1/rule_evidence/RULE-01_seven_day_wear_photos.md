# 七天无理由服饰缺专责多角度照

## 背景

- 品类：服饰-连衣裙
- 本单金额：268元
- 是否已签收：是
- 平台服务标：七天无理由

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 3 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.15 |
| refund_only_rate | 0.05 |
| avg_order_value | 220 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 2 |

说明：普通买家，申请七天无理由退货。

## 对话记录

- 买家：裙子不喜欢，想七天无理由退货。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和外包装照我都看到了。
- 买家：没穿过，吊牌也在，赶紧同意退货退款。
- 商家：亲，收到。您这边方便再补几张穿着效果的照片吗？
- 买家：就试了一下不喜欢，穿着照还要拍哪些角度的？
- 商家：亲，理解您想法，我这边再帮您看下流程要求。
- 买家：行，那我按你们说的补拍，拍好发你们。

## 事实证据

- 图/视频解析：开箱视频完整、外包装完好；买家提供正面平铺照与吊牌照，未见多角度穿着照或全身效果照。
- 证据强弱：中
- 物流：是否签收：是；物流是否正常：是；签收后约 30 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：缺多角度穿着照/全身效果照，无法确认二次销售影响与商品完好状态（专责缺口；普通开箱视频与外包装照已齐）

## 参考

无

## 期望与禁忌

- 目标方向：七天无理由下识别专责补证缺口，引导补充二次销售确认类材料。
- 关键动作：action_type 为 evidence_request；请求应指向穿着效果/多角度照，非泛化「请补证」。
- 禁忌：在专责材料缺失时直接同意退货退款；重复索要已齐的开箱视频。

**expected_report**：

```yaml
action_type: evidence_request
strategy_stage_not: [compensate_close]
actionable_evidence_requests_contains: [穿着, 角度]
customer_value_channel: none
malicious_risk_level_min: low
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：无
