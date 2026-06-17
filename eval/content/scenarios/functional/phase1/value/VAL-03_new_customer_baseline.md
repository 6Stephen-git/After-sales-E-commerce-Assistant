# 新客低客单对照（无价值通道）

## 背景

- 品类：服饰-T恤
- 本单金额：59元
- 是否已签收：是
- 平台服务标：试饮可退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 1 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 59 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 0 |

说明：首单新客，无复购与好评沉淀。

## 对话记录

- 买家：T恤领口有点歪，我要退款。【上传图片】
- 商家：亲，不好意思。照片我看到了。
- 买家：领口不对称很明显，59块全退吧，货我不要了。
- 商家：亲，收到。方便再拍一张平铺全貌吗？
- 买家：近照也发了，领口确实有点歪。
- 商家：亲，我这边再帮您看下。
- 买家：那就麻烦你们今天回复我一下，59块钱的事不想拖太久。

## 事实证据

- 图/视频解析：平铺照显示领口略有不对称，衣身无破损污渍，整体可二次销售。
- 证据强弱：中
- 物流：是否签收：是；物流是否正常：是；签收后约 20 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含商品平铺照、领口特写、外包装照）

## 参考

无

## 期望与禁忌

- 目标方向：新客低客单不触发价值优待通道；按规则与证据标准处理，控制成本。
- 关键动作：说明核实结果与可接受处置范围；不因施压直接全额退。
- 禁忌：误触发老客或高本单优待；无依据承诺全额退款。

**expected_report**：

```yaml
customer_value_channel: none
disposition_in: [defend, negotiate]
disposition_not: [compensate]
action_type_not: [monetary_settle]
malicious_risk_level_min: low
actionable_evidence_requests_max: 0
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：无
