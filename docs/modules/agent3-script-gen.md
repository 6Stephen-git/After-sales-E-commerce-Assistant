# Agent 3 — 话术生成员

## 职责
根据 Agent2 策略阶段与应对思想，生成单条面向买家的店主口吻话术。语气随情绪与客户价值动态调整，事实描述与 Agent1 输出一致。

## 输入
`ScriptInput`（来自 `schemas.py`），核心信号：
- `strategy_output.strategy_stage`：补偿门禁（举证期禁止承诺补偿）
- `strategy_output.customer_value`：`channel` / `tone_suggestion` / `compensation_uplift` 注入话术 LLM，仅调节语气与补偿弹性
- `facts.issue_summary`：话术主锚点
- `facts.*`：结构化事实边界
- `emotion_note`：语气调节
- `chat_history`：近期对话（`ChatTurn` 列表），用于续写、避免重复索要已拒绝的举证

## 输出
`ScriptOutput`：`script` + `response_mode` + `usage_tip`

## 可用工具
- `generate_buyer_script(payload) → str | None`：在 Agent2 输出的 `dialogue_context` 约束下生成单条话术

## 硬约束
- 纯函数：`def generate(input: ScriptInput) -> ScriptOutput`
- 不读写数据库；LLM 调用封装在 tool 层
- 单一推荐话术，禁止三版并列
- `compensation_policy` 由 `strategy_stage` 决定，举证期禁止提补偿
- 事实以 `issue_summary` 理解背景；举证期话术不评价货损程度，只围绕举证疑点与补证请求
- 店主本人口吻，禁用 AI 客服套话

## 内嵌测试用例
1. **抗辩场景**：disposition=defend，验证 `response_mode=malicious_risk` 且 script 非空。
2. **善后场景**：strategy_stage=compensate_close，验证 `response_mode=merchant_fault`。
3. **协商场景**：disposition=negotiate 且无商责/高恶意，验证 `response_mode=neutral_negotiate`。
4. **举证阶段**：strategy_stage=evidence_first 且 LLM 不可用，验证兜底话术不含补偿承诺。
5. **边界场景**：字段缺失时 script 仍非空。
6. **对话续写**：买家已拒绝开箱视频时，话术不以「您好」开头、不再索要开箱。
7. **客户价值**：`customer_value.channel=long_term` 时，验证 `tone_hint` / `customer_value_channel` 注入话术 LLM payload。
