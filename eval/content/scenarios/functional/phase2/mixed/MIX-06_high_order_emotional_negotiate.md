# 高本单金额情绪化与部分补偿协商（举证已齐）

## 背景

- 品类：手机
- 本单金额：5299元
- 是否已签收：是
- 平台服务标：无

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 2 |
| dispute_count | 0 |
| dispute_rate | 0.00 |
| return_rate | 0.00 |
| refund_only_rate | 0.00 |
| avg_order_value | 3200 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 1 |

说明：本单金额高；买家情绪激动主张屏幕瑕疵，证据部分可核实。

## 对话记录

- 买家：手机边框有划痕，五千多的东西就这样？太气人了！【上传图片】【发送视频】
- 商家：亲，真不好意思。开箱视频和划痕照我都看到了。
- 买家：划痕很明显好吧！我要求赔偿，全退也行，部分赔也行，你们得给个说法！
- 商家：亲，收到。划痕位置我这边再确认下。
- 买家：视频里都拍清楚了，别跟我打太极，今天必须给方案！
- 商家：亲，理解您心情，我这边帮您看下能怎么处理。
- 买家：五千多块不是小数目，你们给个具体补偿数字，别敷衍我！

## 事实证据

- 图/视频解析：连续开箱视频完整；静态照显示边框一处约2厘米浅划痕，屏幕点亮正常，功能未见异常。
- 物流：是否签收：是；物流是否正常：是；签收后约 12 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含开箱视频、划痕照、点亮视频）

## 参考

- CASE-VAL-01

## 期望与禁忌

- 目标方向：识别本单高金额通道与情绪化沟通；参考部分补偿判例，在配置上限内协商具体方案。
- 关键动作：disposition 倾向 negotiate；可给出明确金额或方案并征求接受，不突破赔偿上限。
- 禁忌：情绪化时无依据全额退；空泛「再商量」不报具体方案；忽视高本单价值信号。

**expected_report**：

```yaml
customer_value_channel: order
disposition_in: [negotiate]
disposition_not: [compensate]
similar_cases_min: 1
action_type_in: [monetary_settle, rule_explain]
malicious_risk_level_min: low
actionable_evidence_requests_max: 0
strategy_stage_not: [evidence_first]
```

## 其他说明

- 老客价值：无
- 赔偿：不超过订单 30%
- 恶意规则：无
