# 修改计划（已按最新口径重置）

## 口径冻结

- 规则主轴：平台规则定责为最高优先级；规则正文来自 MySQL 爬取文档，导航来自 `rule_match_lexicon.json` + Agent1 `rule_match_plan`（已移除 `dispute_rules.json` / conditions 引擎）。
- 决策范式：取消三策略竞争式框架，改为单链路 `disposition` 决策。
- 默认处置：除“明显恶意行为”与“商家过失明确”外，默认进入协商范畴。
- 胜率含义：以“平台规则站位 + 证据强弱 + 恶意/过失强信号”计算，不再用三策略分差推导。

## Agent 2 升级模块（重排）


| #        | 模块名称                     | 核心改动                                                                  | 依赖         |
| -------- | ------------------------ | --------------------------------------------------------------------- | ---------- |
| **A2-1** | **规则数据源切换到 MySQL**       | 新增规则读取工具，从 `platform_rules` 拉取规则；本地 JSON 仅保留开发回退，不参与生产主判定             | 无          |
| **A2-2** | **规则匹配与定责结果结构化**         | 输出规则命中强度与责任站位（商家有利/买家有利/中性），作为后续胜率与 disposition 主输入                   | A2-1       |
| **A2-3** | **恶意检测强化（硬规则+语义）**       | 保留双层检测，明确“勒索=威胁词+条件交换词”；高风险只作为强覆盖条件，不再参与三路打分                          | 无          |
| **A2-4** | **去三策略，改单链 disposition** | `StrategyOutput.strategy` 改为 `disposition`；决策链固定为：恶意强信号 > 商责明确 > 默认协商 | A2-2, A2-3 |
| **A2-5** | **胜率/置信度重塑（规则主轴）**       | 胜率基于规则站位、证据质量、恶意/过失信号；置信度基于证据完整度与规则命中确定性                              | A2-4       |
| **A2-6** | **编排与接口收口**              | 清理旧 `strategy_scores/STRATEGY_`* 相关逻辑，统一 controller/agent 输入输出结构      | A2-4, A2-5 |
| **A2-7** | **测试与前端结构对齐**            | 更新 Agent2/Controller 测试与前端展示字段                                        | A2-6       |


## Agent 3 升级模块（对齐 Agent2 新输出）


| #        | 模块名称                      | 核心改动                                                  | 依赖         |
| -------- | ------------------------- | ----------------------------------------------------- | ---------- |
| **A3-1** | **话术输入改为 disposition 驱动** | 不再依赖 defend/negotiate/compensate 三策略分数，改为单结论+依据生成推荐话术 | A2-6       |
| **A3-2** | **三类应对思想保留为内部模板思想**       | 内部仍区分“商责明确/恶意买家/常规协商”，但对外只输出单一推荐版本                    | A3-1       |
| **A3-3** | **风格与约束系统持续强化**           | 保留禁用词、语气边界、店主人设约束；根据 disposition 与风险级别控温              | A3-1       |
| **A3-4** | **客户价值标记接入话术细化**          | 老客与高价值单在协商路径下调整语气与补偿建议，不改变 disposition 主结论            | A3-1, A2-6 |


### Agent 3 完成状态（2026-05-24）

| 模块 | 状态 | 验收要点 |
| ---- | ---- | -------- |
| A3-1 | ✅ 已完成 | `ScriptInput` 由 `disposition` / `strategy_stage` / `malicious_detection` 驱动，无三策略分数 |
| A3-2 | ✅ 已完成 | 内部 `response_mode` 三类应对思想；对外 `ScriptOutput.script` 单条；前端 `ScriptCard` 已对齐 |
| A3-3 | ✅ 已完成 | 禁用词、店主人设、`compensation_policy` 门禁、对话续写（`dialogue_plan`）、举证期不复述货损 |
| A3-4 | ✅ 已完成 | `tone_hint` / `customer_value_channel` / `compensation_uplift` 注入 LLM payload；prompt 含老客/高价值语气指引与 few-shot |

**超出本表但已落地的增强：** Controller 预计算 `dialogue_plan`；两步 LLM（语境分析 + 话术生成）；已移除 `script_templates` 读取链路。

## 推荐执行顺序

A2-1 → A2-2 → A2-3 → A2-4 → A2-5 → A2-6 → A2-7 → A3-1 → A3-2 → A3-3 → A3-4

---

## 本轮实施验收标准（新增）

1. Agent2 不再依赖本地 `dispute_rules.json` 做生产主判定。
2. `schemas.py` 中不再保留三策略主输出字段与常量依赖链。
3. Agent2 最终仅输出 `disposition` 主结论，且“默认协商”在无强覆盖条件时稳定生效。
4. 胜率与置信度解释文本必须能对应到规则站位与证据情况。
5. Agent2/Controller/前端关键测试通过，旧三策略用例完成迁移或删除。
6. Agent3 输出单一推荐话术；`response_mode` 仅作内部应对思想；客户价值只调节语气与补偿弹性，不改变 disposition。

## LLM 链路优化（2026-05）

- 删除 `fast_path` / `ENABLE_FAST_PATH` / `*_FAST` 模型分支；固定「全链路 mimo-v2.5 + 策略 JSON 唯一 Pro」。
- Agent1 品类 slug 由事实 LLM / 视觉 LLM 同批输出，merge 消费去重 slug；移除 `infer_category_slug_llm` 重复调用。
- 删除独立 `dialogue_plan` LLM；`dialogue_context` 并入 Agent2 策略 JSON（4 键）。
- Agent2 策略 LLM 直出 JSON 展示字段；恶意语义分仅允许 5/10/15。
- 并发：Batch0 画像/判例与 Agent1 重叠；Batch1 规则+价值+恶意并行后再策略+话术。
- env：`AGENT2_LLM_MODEL_VALUE` / `AGENT2_LLM_MODEL_MALICIOUS` / `AGENT2_LLM_MODEL_STRATEGY`。
