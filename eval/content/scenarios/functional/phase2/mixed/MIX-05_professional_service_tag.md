# 职业索赔话术与服务标边界（举证已齐）

## 背景

- 品类：宠物活体（幼猫）
- 本单金额：1680元
- 是否已签收：是
- 平台服务标：伤亡大病包退

## 买家

| 字段 | 值 |
|---|---|
| purchase_count | 5 |
| dispute_count | 6 |
| dispute_rate | 0.38 |
| return_rate | 0.05 |
| refund_only_rate | 0.69 |
| avg_order_value | 1420 |
| credit_level | medium |
| malicious_flags | 2 |
| positive_review_count | 1 |

说明：宠物类目高频仅退买家；话术熟练，常引用伤亡大病包退施压。

## 对话记录

- 买家：猫精神不好，平台写着伤亡大病包退，你们必须仅退款。【上传图片】【发送视频】
- 商家：亲，不好意思。开箱视频和现状照我都看到了。
- 买家：我买过十几单宠物了，流程我很熟。服务标摆在那，不退我就投诉卖病宠。
- 商家：亲，收到。方便说下目前具体症状吗？
- 买家：就是萎靡，你们别拿验货吓唬我。伤亡大病包退就是死了病了都退，今天必须仅退款1680。
- 商家：亲，理解您担心，我这边再看下。
- 买家：最后说一次，按服务标仅退款，不然我联合其他买家维权！

## 事实证据

- 图/视频解析：开箱视频包装完好；现状视频幼猫可站立走动，鼻头湿润，未见明显外伤；静态照无严重分泌物。
- 物流：是否签收：是；物流是否正常：是；签收后约 10 小时申请；其它物流说明：无异常轨迹。
- 视觉严重度：minor；可挽回性：resalable
- 缺失材料：无（普通举证已齐：含开箱视频、现状视频、外包装照）

## 参考

无

## 期望与禁忌

- 目标方向：识别职业索赔话术与服务标滥用；举证已齐时说明服务标生效条件与当前证据范围，守仅退边界。
- 关键动作：结合材料说明实际状态；抗辩或协商，不被联合维权话术带偏。
- 禁忌：因服务标名称直接同意全额仅退；忽视高仅退画像；重复索要已齐举证。

**expected_report**：

```yaml
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
- 恶意规则：无
