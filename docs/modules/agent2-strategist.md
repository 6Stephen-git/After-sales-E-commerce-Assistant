# Agent 2 — 策略参谋员

## 职责
综合事实、买家画像、平台规则和历史判例，分析当前纠纷态势，给出最优策略建议。需要权衡的因素包括：事实证据的强弱、买家历史行为模式、平台规则的适用性、商家过往处理相似案例的经验。当多个因素指向矛盾时，优先遵循平台规则，并在推理中说明取舍逻辑。

## 输出
`StrategyOutput`（来自 `schemas.py`）

## 可用工具
- `match_rules(facts) → List[MatchedRule]`：从规则库中匹配适用于当前事实的平台规则
- `query_buyer_profile(buyer_id) → BuyerProfile`：查询买家在本店的历史购买、纠纷、退货数据
- `search_similar_cases(dispute_desc, top_k) → List[SimilarCase]`：检索商家历史相似判例，参考过往经验

## 硬约束
- 纯函数：`def recommend(input: StrategyInput) -> StrategyOutput`
- 不直接读写数据库，所有数据通过工具获取
- 策略推荐必须基于事实+规则+画像+判例的综合推理，禁止凭空猜测
- 当多个因素指向矛盾时，优先遵循平台规则，并在 `reasoning` 中说明权衡过程
- 预估胜率需给出具体数值（0-1），并能在 `reasoning` 中解释计算依据