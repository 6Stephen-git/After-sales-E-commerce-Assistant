# 电商应诉助手 — 项目总结（面试版）

## 一、项目定位

帮助电商中小商家处理售后纠纷的 AI 系统。核心痛点：商家不熟悉平台规则、不懂有效举证、情绪化决策导致不必要损失。

- **辅助模式**：人工主导，AI 侧边栏提供事实摘要、策略建议、多版本话术
- **智能模式**：AI 主导对话，商家随时可接管（前端路由尚未完成）
- 两种模式共享同一套 5-Agent 核心代码

---

## 二、技术架构

### 2.1 整体分层

```
前端 Vue 3 + Element Plus
        |
路由层 FastAPI (routers/)
        |
控制器层 (controllers/assisted_controller.py)
        |
Agent 层 (agents/agent1~5)
        |
工具层 (tools/) + 缓存层 (cache/) + 数据层 (db/)
```

### 2.2 核心处理链路

```
买家发起纠纷
  -> 材料收集与合并 (merge_materials, 缓存层)
  -> C 层缓存短路检查（命中则直接返回）
  -> Batch0 并行:
       Agent1 事实提取 (fact_extractor.py, 820行)
       买家画像与相似判例查询
  -> Batch1 并行:
       客户价值评估 (evaluate_customer_value)
       恶意行为检测 (detect_malicious_behavior)
  -> 规则匹配门控 (needs_rule_match)
       -> match_rules_from_facts (rule_matcher.py, 877行)
  -> Agent2 策略制定 (strategist.py, 1213行)
       -> 产出 ActionContract（动作类型+补偿政策+规则约束+下一步）
  -> Agent3 话术生成 (script_generator.py, 331行)
       -> 在 ActionContract 约束下生成多版本话术
  -> AnalysisReport 聚合 + 缓存保存
  -> SSE 流式推送给前端 (progress/delta/final_report)
```

### 2.3 Agent 协作关系

| Agent | 文件 | 代码量 | 职责 | 输入 | 输出 |
|-------|------|--------|------|------|------|
| Agent1 事实还原员 | agent1/fact_extractor.py | 820行 | 提取结构化事实，不判责 | 材料（文本+图片+物流） | FactOutput |
| Agent2 策略参谋员 | agent2/strategist.py | 1213行 | 综合分析输出处置方向 | FactOutput + 规则 + 恶意 + 画像 | StrategyOutput (含ActionContract) |
| Agent3 话术生成员 | agent3/script_generator.py | 331行 | 生成店主口吻话术 | ActionContract + 对话上下文 | ScriptOutput |
| Agent4 情绪监控员 | agent4/emotion_monitor.py | 159行 | 消息情绪分析 | 消息文本 | EmotionOutput |
| Agent5 复盘分析师 | agent5/reviewer.py | 244行 | 经验提炼存入判例库 | 完整轨迹 | ReviewOutput |

关键设计：Agent 间通过 schemas.py 中定义的 Pydantic 结构体通信，所有 Agent 的输入输出必须引用该文件，禁止自行发明字段。

---

## 三、功能点清单

### 3.1 核心智能功能（5项）

#### 1) 多模态事实提取
- 位置：backend/agents/agent1/fact_extractor.py
- 流程：LLM 提取核心诉求和意图标签 -> 诉求锚定视觉指导 -> 并行多图分析（最多3张）-> 多图结论合并 -> 证据质量与可决策度评估
- 技术亮点：
  - _build_vision_guidance() 用诉求锚定多模态分析方向
  - pick_balanced_visual_observations() 多图轮询采样，避免截断后只剩首图结论
  - resolve_primary_dispute_frame() 单点判定主争议框架（七天无理由/质量缺陷/描述不符/物流），下游只读不再猜
  - _derive_evidence_quality() 给证据覆盖度档位（high/medium/low）

#### 2) 规则匹配引擎
- 位置：backend/tools/rule_matcher.py + backend/tools/rule_lexicon.py
- 流程：品类slug解析 -> 通道激活 -> doc锁定 -> 节过滤 -> LLM条文选型 -> must/should分级
- 技术亮点：
  - rule_lexicon.py 多层解析策略：精确匹配 -> 去引号键匹配 -> 大小写不敏感 -> 中文核心名子串唯一命中 -> 模糊最长匹配
  - rule_matcher.py 锁定规则文档后，通过LLM从候选节中选出最相关条文
  - must(必须遵守)/should(建议遵守)/weak(参考) 三级相关性分级
  - needs_rule_match() 证据门控：简单案跳过规则匹配，减少LLM调用成本

#### 3) 恶意行为检测
- 位置：backend/tools/agent2_tools.py
- 流程：硬规则层（虚假凭证、AI伪造、差评勒索模式识别）+ LLM语义层（综合上下文判断）-> 两层结果融合
- 技术亮点：
  - 双轨检测降低误判率
  - 硬规则层快速拦截明显恶意，语义层处理复杂场景
  - 检测结果注入 StrategyInput.precomputed_malicious_detection，避免Agent2内部重复调用

#### 4) 客户价值评估
- 位置：backend/tools/agent2_tools.py
- 流程：长期通道触发条件 + 本单通道（视觉损失暴露）+ red_flags拦截
- 技术亮点：
  - 评估结果注入 StrategyInput.precomputed_customer_value
  - 预计算注入模式：Batch1并行预计算，避免Agent2串行等待

#### 5) 动作契约机制
- 位置：backend/agents/agent2/strategist.py 中 _infer_action_contract()
- 流程：策略分析 -> 产出 action_type(6种) + compensation_policy(4种) + rule_constraints + next_step -> Agent3严格在契约内生成话术
- 技术亮点：
  - 核心创新：用结构化契约约束LLM输出范围，解决"策略与话术不一致"问题
  - action_type: rule_explain / evidence_request / return_inspection / merchant_remedy / monetary_settle / defend_prepare
  - compensation_policy: forbid / none / soft_no_amount / explicit_amount
  - Agent3 的 _must_state_compensation_amount() 门禁：仅金额动作且 explicit_amount 时要求报具体金额

### 3.2 工程化亮点（4项）

#### 1) 三级缓存体系
- 位置：backend/cache/
- 分层：材料层缓存（合并后的材料避免重复拉取）+ 事实层缓存（B层，Agent1输出可复用）+ 报告层缓存（C层，完整分析结果直接返回）
- 价值：碎片化对话场景（商家分多次查看）中，C层缓存可跳过整个分析链路

#### 2) 流式SSE响应
- 位置：backend/routers/analyze.py 中 /analyze/stream
- 机制：EventEmitter 回调按 progress / delta / final_report 三阶段推送
- 前端降级：环境变量 VITE_ENABLE_ANALYZE_STREAM=1 控制，支持普通HTTP回退

#### 3) 配置化文本信号
- 位置：data/text_signals.json + backend/tools/text_signals.py
- 设计：所有词表统一管理在JSON配置中，业务代码零散落硬编码关键词
- 价值：新增信号词只需改配置，不需要改代码

#### 4) 质量门禁机制
- 位置：backend/agents/agent3/script_generator.py
- 五项门禁：
  1. 禁客服套话
  2. 禁踢皮球
  3. 禁人机味
  4. 非终局抗辩时禁亮规则条文
  5. 须报金额时禁空泛商量
- 重试：门禁不通过自动重试一次

### 3.3 评估体系（2项）

#### 1) 评测树 + LLM-as-Judge
- 位置：eval/ 目录
- 评测树：20个场景 x 三维度（协商NG:10 / 商责MF:5 / 恶意MA:5）
- Judge评分：11维分项评分（1~5分等权）+ 总分换算（均值/5*100，通过阈值80分）
- 硬失败规则：禁忌触犯>=2条、script_safety<4、推荐话术提前承诺、报告事实冲突、编造规则等
- 11个评分维度：expectation_alignment / forbidden_output_safety / rule_understanding / rule_boundary_ability / evidence_handling / malicious_risk_recognition / customer_value_tradeoff / merchant_interest / buyer_communication / script_safety / script_reliability

#### 2) 防泄题机制
- 位置：eval/pipeline/spec_to_fixture.py
- 字段级拦截：expectation、forbidden_outputs、human_review 等Judge专用字段不得进入fixture
- 文本级拦截：非对话字段扫描"期望策略""标准答案""测试重点"等泄题短语
- 价值：保证LLM跑批时不会通过fixture读到"标准答案"

---

## 四、业务难点与设计决策

### 难点1：规则边界模糊
- 问题：平台规则表述不清晰、存在交叉和歧义，简单关键词匹配无法准确命中
- 方案：rule_lexicon.py 品类导航 + rule_matcher.py 双层匹配。先通过品类slug锁定规则文档，再通过LLM进行条文选型和分级推荐
- 面试要点：为什么不能用简单关键词匹配？因为规则表述存在"一句话涵盖多种场景"和"同一场景涉及多条规则"的交叉问题，需要LLM做语义级条文选型

### 难点2：策略与话术一致性
- 问题：LLM生成话术时容易偏离策略意图，自由发挥导致前后矛盾
- 方案：动作契约机制。Agent2输出结构化的 action_type + compensation_policy + rule_constraints + next_step，Agent3被严格约束在此契约内
- 面试要点：这是项目核心创新点，体现"用确定性结构约束LLM不确定性"的设计思想

### 难点3：证据质量评估
- 问题：从碎片化的聊天记录、图片、物流信息中判断证据充分性
- 方案：Agent1多维度证据融合：文本诉求分析 -> 多模态视觉分析 -> 物流状态 -> 综合判定 evidence_quality 和 decision_readiness
- 面试要点：多图轮询采样（pick_balanced_visual_observations）和诉求锚定视觉指导（_build_vision_guidance）的技术细节

### 难点4：恶意行为识别的准确性
- 问题：需要区分真实纠纷和恶意索赔，避免误判影响正常用户体验
- 方案：双轨检测——硬规则层快速拦截明显恶意 + LLM语义层处理复杂场景，两层结果融合降低误判率
- 面试要点：硬规则和LLM各自的优势和局限，以及融合决策的必要性

### 难点5：确定性与LLM的平衡
- 问题：LLM输出不稳定，但纯规则又无法覆盖所有语义场景
- 方案：确定性优先原则——规则匹配、恶意硬规则、客户价值评分、主争议框架判定全部用纯代码/配置化实现，LLM仅在需要语义推理时介入
- 面试要点：对AI能力边界的理解，以及"核心链路首版即生产强度"的工程原则

### 难点6：AI系统质量评估
- 问题：LLM输出非确定性，传统单元测试无法验证业务语义质量
- 方案：评测树 + LLM-as-Judge体系。20个场景覆盖三维度，11维评分+硬失败规则，防泄题机制保证评估客观性
- 面试要点：评测树设计思路、Judge评分体系的合理性、防泄题机制的必要性

---

## 五、面试讲解大纲（约7分钟）

### 1. 项目概述（1分钟）
"这是一个面向电商中小商家的 AI 纠纷应诉助手。核心解决商家不熟悉平台规则、不懂有效举证、情绪化决策导致不必要损失的问题。系统采用 5-Agent 协作架构，支持辅助模式和智能模式，核心链路是 Agent1(事实提取) -> Agent2(策略制定) -> Agent3(话术生成)。"

### 2. 架构亮点（2分钟）
- Agent 职责严格分离：5个Agent各司其职，通过结构化schema通信，可独立测试和优化
- 确定性优先于LLM：规则匹配、恶意检测等用纯代码实现，确保确定性；LLM仅在语义推理时介入
- 动作契约机制：Agent2产出结构化契约，Agent3严格在契约内生成话术，从结构层面保证策略与话术一致性
- 预计算注入：恶意检测和客户价值在Batch1并行预计算，注入StrategyInput，避免Agent2内部串行等待

### 3. 核心创新（2分钟）
- 动作契约机制：将"策略到话术的一致性"从prompt层面提升到架构层面解决
- 评测树+LLM Judge：20场景x11维评分的科学评估体系，包含防泄题机制
- 多模态融合分析：文本+图片+物流三维度融合，诉求锚定视觉指导

### 4. 技术难点应对（2分钟）
准备好6个难点的30秒版本应答（见第四部分）

---

## 六、量化指标

| 指标 | 数值 |
|------|------|
| 核心 Agent 代码量 | 2767行（Agent1:820 + Agent2:1213 + Agent3:331 + Agent4:159 + Agent5:244） |
| 规则匹配引擎 | 877行 |
| 数据结构定义 | 585行（schemas.py） |
| 评测场景数 | 20个（协商10 + 商责5 + 恶意5） |
| Judge 评分维度 | 11维 |
| 测试用例数 | 33个 |
| 质量门禁项 | 5项 |
| 缓存层级 | 3层 |
| 动作类型 | 6种 |
| 补偿策略 | 4种 |
