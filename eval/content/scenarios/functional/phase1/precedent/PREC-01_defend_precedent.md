# 注入抗辩成功判例的品质争议（举证已齐）

## 背景

- 品类：家装灯饰光源
- 本单金额：198元
- 是否已签收：是
- 平台服务标：破损包赔

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 5 |
| dispute_count | 1 |
| dispute_rate | 0.20 |
| return_rate | 0.10 |
| refund_only_rate | 0.08 |
| avg_order_value | 175 |
| credit_level | medium |
| malicious_flags | 0 |
| positive_review_count | 3 |

说明：普通买家画像，无极端价值或恶意标记。

## 对话记录

- 买家：吸顶灯亮度不够，跟页面描述差很多，我要全额退款。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和灯具照我都看到了。
- 买家：视频里装好了吧？亮度就是不行，198全退，灯拆下来太麻烦，仅退款。
- 商家：亲，收到。您说的亮度问题，方便说下使用环境吗？
- 买家：就是客厅用的，亮度跟直播间演示差一截，描述不符吧。
- 商家：亲，我这边再帮您核实下。
- 买家：材料都在了，你们看完告诉我怎么处理就行。

## 事实证据

- 图/视频解析：开箱视频完整；安装后视频显示灯具正常点亮，亮度主观感受无法从影像客观量化；外观无破损。
- 证据强弱：中
- 物流：是否签收：是；物流是否正常：是；签收后约 48 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含开箱视频、安装点亮视频、外包装照）

## 参考

- CASE-MAL-01

## 期望与禁忌

- 目标方向：参考抗辩成功判例，在描述不符主张下坚持核实边界，倾向 defend。
- 关键动作：similar_cases 注入后策略推理应引用历史经验；说明证据与描述争议的可核实范围。
- 禁忌：忽视判例经验直接全额让步；判例注入后仍给出无依据补偿承诺。

**expected_report**：

```yaml
similar_cases_min: 1
disposition_in: [defend, negotiate]
disposition_not: [compensate]
reasoning_contains_any: [判例, 经验, 案例]
customer_value_channel: none
malicious_risk_level_min: low
actionable_evidence_requests_max: 0
```

## 其他说明

- 老客价值：无
- 赔偿：无
- 恶意规则：无
