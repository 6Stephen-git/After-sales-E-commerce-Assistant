# 工具定义 — 所有 Agent 的行动能力边界

## 目的

本文档定义每个 Agent 可以调用的所有外部能力（API、数据库、本地模型、文件读取等）。未在此文档定义的能力，Agent 无权使用。

## 工程原则

1. 每个工具在代码层面是 `tools/` 目录下的普通 Python 函数，不抽象为 Tool 基类。
2. 工具来源是硬约束，开发 Agent 必须使用指定来源，不得自行替换。
3. 工具不可跨 Agent 共享，每个 Agent 只能使用分配给自己的工具清单。

## Agent 1 — 事实还原员


| 工具名               | 功能              | 来源                        | 技术选型                       |
| ----------------- | --------------- | ------------------------- | -------------------------- |
| `analyze_image`   | 分析买家上传图片，提取视觉特征 | 多模态 API（GPT-4V / Qwen-VL） | 环境变量 `VISION_API_ENDPOINT` |
| `query_logistics` | 查询物流轨迹与状态       | 平台 API（千牛/拼多多开放平台）        | 调用 `platform_api.py`       |


`**analyze_image**`

- 输入：`image_url: str`
- 输出：`dict`，包含：
  - `defect_type: str` — 瑕疵类型
  - `defect_location: str` — 位置
  - `edge_condition: str` — 破损边缘形态（整齐/毛糙/无法判断）
  - `has_tag: bool` — 吊牌是否可见
  - `background: str` — 拍摄背景
  - `wear_signs: str` — 穿着痕迹
- 约束：必须使用环境变量 `VISION_API_ENDPOINT`，不得硬编码 URL。

`**query_logistics**`

- 输入：`order_id: str`
- 输出：`LogisticsInfo`（来自 `schemas.py`）
- 约束：通过统一的 `platform_api.py` 调用，禁止直接拼接 HTTP 请求。

## Agent 2 — 策略参谋员


| 工具名                           | 功能         | 来源                      | 技术选型         |
| ----------------------------- | ---------- | ----------------------- | ------------ |
| `match_rules`                 | 匹配平台规则     | 本地 `dispute_rules.json` | JSON 规则引擎    |
| `query_buyer_profile`         | 查询买家画像     | MySQL                   | 主数据库         |
| `search_similar_cases`        | 检索历史判例     | MySQL                   | 结构化标签检索      |
| `search_similar_cases_vector` | 向量语义检索历史判例 | ChromaDB                | 可选，不可用时返回空列表 |
| `evaluate_customer_value`     | 双维客户价值评估  | 本地计算（纯函数）            | 无外部依赖         |
| `detect_malicious_behavior`   | 恶意行为双层检测  | 本地硬规则 + LLM 语义        | `agent2_tools.py` |


`**match_rules**`

- 输入：`facts: FactOutput`
- 输出：`List[MatchedRule]`
- 实现：读取 `data/dispute_rules.json`，按条件字段精确匹配，非向量检索。
- 约束：规则文件路径通过环境变量 `RULES_PATH` 获取，不可硬编码。

`**query_buyer_profile**`

- 输入：`buyer_id: str`（哈希）
- 输出：`BuyerProfile`
- 约束：只查询，不修改。使用统一数据库连接池。

`**search_similar_cases**`

- 输入：`dispute_desc: str`，`top_k: int = 3`
- 输出：`List[SimilarCase]`
- 实现：基于 `case_type`、`outcome` 等标签 SQL 精确匹配，不涉及向量检索。
- 约束：只查询当前商家自己的判例库（`WHERE merchant_id = 当前商家ID`）。

`**evaluate_customer_value**`

- 输入：`input_data: CustomerValueInput`
- 输出：`CustomerValueOutput`
- 实现：按长期价值与本单价值两条独立评分链路计算，输出分项明细、触发通道和优待建议。
- 约束：纯函数，不读取数据库，不调用 HTTP 或 LLM。

`**detect_malicious_behavior**`

- 输入：`input_data: MaliciousDetectionInput`
- 输出：`MaliciousDetectionOutput`
- 实现：先执行硬规则层，再执行 LLM 语义层，最终融合输出风险分、等级和处置建议。
- 约束：硬规则层必须可解释；语义层必须结构化 JSON 输出，不允许自由文本。

## Agent 3 — 话术生成员


| 工具名                   | 功能     | 来源                                 | 技术选型 |
| --------------------- | ------ | ---------------------------------- | ---- |
| `get_script_template` | 获取话术模板 | 本地 `script_templates.json` 或 MySQL | 模板库  |


`**get_script_template**`

- 输入：`strategy_type: str`（"defend" / "negotiate" / "compensate"）
- 输出：`str`（含 `{{变量}}` 占位符的模板文本）
- 约束：优先读取商家自定义模板（数据库），若无则用系统默认模板。

## Agent 4 — 情绪监控员


| 工具名                 | 功能     | 来源         | 技术选型 |
| ------------------- | ------ | ---------- | ---- |
| `analyze_sentiment` | 分析文本情绪 | 本地 BERT 模型 | 本地推理 |


`**analyze_sentiment**`

- 输入：`text: str`
- 输出：`dict` 含 `label: str`（"negative"/"neutral"/"positive"）和 `intensity: float`（0-1）
- 约束：模型在 `__init__` 只加载一次，后续复用。禁止调用 LLM API 做情感分析（理由：本地模型延迟 < 5ms，零成本）。

## Agent 5 — 复盘分析师


| 工具名               | 功能     | 来源    | 技术选型 |
| ----------------- | ------ | ----- | ---- |
| `save_case_to_db` | 存储经验卡片 | MySQL | 主数据库 |


`**save_case_to_db**`

- 输入：`review: ReviewOutput`
- 输出：`bool`
- 约束：写入时必须携带当前 `merchant_id`，遵循数据隔离策略。

## 错误处理规范

所有工具函数必须实现统一错误处理：

1. API 调用：失败重试最多 3 次，指数退避。最终失败返回 `{"error": "具体错误信息"}`。
2. 数据库查询：失败记录日志，抛出异常由上层 Controller 捕获。
3. 本地模型：加载失败时降级为简单关键词匹配，并记录日志。
4. 本地文件读取：文件不存在时返回默认规则/模板，并记录日志。

