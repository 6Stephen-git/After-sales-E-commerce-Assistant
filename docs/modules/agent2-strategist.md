# Agent 2 — 策略参谋员

## 职责
综合事实、买家画像、平台规则和历史判例，分析当前纠纷态势，给出最优策略建议。需要权衡的因素包括：事实证据的强弱、买家历史行为模式、平台规则的适用性、商家过往处理相似案例的经验。当多个因素指向矛盾时，优先遵循平台规则，并在推理中说明取舍逻辑。

## 输出

`StrategyOutput`（来自 `schemas.py`）

## 可用工具

- `match_rules(facts) → List[MatchedRule]`：基于 Agent1 `rule_match_plan` 与 lexicon，从 MySQL 爬取正文匹配条款；策略 LLM 使用 `rule_briefs`
- `query_buyer_profile(buyer_id) → BuyerProfile`：查询买家在本店的历史购买、纠纷、退货数据
- `search_similar_cases(dispute_desc, top_k) → List[SimilarCase]`：检索商家历史相似判例，参考过往经验

## 硬约束

- 纯函数：`def recommend(input: StrategyInput) -> StrategyOutput`
- 不直接读写数据库，所有数据通过工具获取
- 策略推荐必须基于事实+规则+画像+判例的综合推理，禁止凭空猜测
- 当多个因素指向矛盾时，优先遵循平台规则，并在 `reasoning` 中说明权衡过程
- 预估胜率需给出具体数值（0-1），并能在 `reasoning` 中解释计算依据

## 举证门控（分级）

实现见 `backend/agents/agent2/evidence_readiness.py`，策略层唯一判定是否锁定 `evidence_first`：

| 条件 | 行为 |
|------|------|
| 结构化 `RuleConstraint.status == missing_fact` | 锁定 `evidence_first`，`action_type=evidence_request` |
| 恶意 `risk_level == high` | 同上 |
| 中/高证据且已有视觉观察 | 举证约束降为 `applies`，缺证仅 `[证据提示]`，不锁 `evidence_first` |
| 低证据或无视觉结论的缺证 | 举证约束 `missing_fact`，锁定 `evidence_first` |
| 其余疑点 | 写入 `risk_factors`，不阻断协商/善后/抗辩 |
| 对话中买家已拒举证 | 确定性写入 `dialogue_context.blocked_evidence_requests`，不再索要 |

条文匹配侧：买家面向缺证非空时，举证类约束的 `status` 升为 `missing_fact`（`rule_matcher._build_rule_constraints`）。

## 内嵌测试用例
1. **高质量瑕疵场景**：证据质量高且规则指向体面善后，验证输出策略为 `compensate`。
2. **低证据高风险买家场景**：证据不足且买家画像风险高，验证输出策略为 `defend`，并包含风险项。
3. **色差中证据场景**：规则与事实偏向协商，验证输出策略为 `negotiate`。
4. **边界场景**：无规则、无判例输入，验证仍返回合法策略与 0-1 区间置信度。
5. **分级举证**：仅 `missing_fact` 或恶意 high 锁 `evidence_first`；中等证据缺开箱视频时走协商并附 `[证据提示]`。
6. **对话拒证**：商家已问开箱视频且买家拒录时，该项进入 `blocked_evidence_requests`。