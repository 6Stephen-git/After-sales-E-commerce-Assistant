# 高老客价值与差评要挟冲突（举证已齐）

## 背景

- 品类：咖啡/麦片/冲饮
- 本单金额：92元
- 是否已签收：是
- 平台服务标：坏单包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 32 |
| dispute_count | 1 |
| dispute_rate | 0.03 |
| return_rate | 0.05 |
| refund_only_rate | 0.02 |
| avg_order_value | 88 |
| credit_level | high |
| malicious_flags | 0 |
| positive_review_count | 15 |

说明：店铺核心老客，累计消费与复购高；本次以差评与投诉要挟全额仅退。

## 对话记录

- 买家：这包挂耳咖啡漏粉严重，老客户了，今天必须92块仅退款。【上传图片】
- 商家：亲，不好意思。照片和开箱视频我都看到了。
- 买家：都发你了，别让我再补材料。我在你们家买了三十多次，这次必须全退。
- 商家：亲，收到。您这边希望怎么处理？
- 买家：不给仅退款我就改差评，还要投诉你们以次充好！
- 商家：亲，理解您着急，我这边再看下情况。
- 买家：最后说一次，要么现在仅退款到账，要么我马上差评加投诉！

## 事实证据

- 图/视频解析：外包装完好；开箱视频完整；静态照显示外袋一角轻微压痕，内袋挂耳完整，粉末量目测正常，未见大面积漏粉。
- 物流：是否签收：是；物流是否正常：是；签收后约 20 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含开箱视频、商品照、外包装照）

## 参考

- CASE-CONF-01

## 期望与禁忌

- 目标方向：同时识别高老客价值与差评要挟；恶意风险应压过单纯优待，不无依据承诺全额仅退。
- 关键动作：可体现老客重视，但守住举证与风控边界；抗辩或协商，引用判例经验说明恶意优先。
- 禁忌：因老客身份忽略要挟信号直接全额让步；判例注入后仍无依据妥协。

**expected_report**：

```yaml
malicious_risk_level_min: medium
customer_value_channel: long_term
disposition_in: [defend, negotiate]
disposition_not: [compensate]
action_type_in: [monetary_settle, rule_explain, merchant_remedy]
similar_cases_min: 1
actionable_evidence_requests_max: 0
strategy_stage_not: [evidence_first]
```

## 其他说明

- 老客价值：累计消费高、复购稳定
- 赔偿：不超过订单 30%
- 恶意规则：无
