# Agent 5 — 复盘分析师

## 职责
在纠纷关闭后异步运行。回顾完整纠纷轨迹（对话记录、AI 各阶段建议、商家实际操作、最终结果），提炼可复用的经验教训，生成结构化经验卡片，存入商家私有判例库，让系统“越用越聪明”。

## 输出
`ReviewOutput`（来自 `schemas.py`）

## 可用工具
- `save_case_to_db(review) → bool`：将经验卡片写入 MySQL 判例库，写入时须携带当前 `merchant_id`

## 硬约束
- 纯函数：`def review(input: ReviewInput) -> ReviewOutput`
- 不直接操作数据库连接，通过工具写入
- 异步执行，不阻塞主流程
- 经验提炼必须基于实际结果，不虚构
- 当商家实际做法优于 AI 建议时，在经验中记录这一分歧，作为未来优化策略的信号
- `case_type` 和 `tags` 便于后续检索，`tags` 应包含多个维度的关键词