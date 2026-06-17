# 测试与验收标准

## 模块验收流程
1. Agent 模块开发完成后，运行其任务说明书中嵌入的测试用例。
2. 全部内置用例通过后，再运行跨 Agent 的集成测试。
3. 前一个模块验收通过，方可开始下一个模块。
4. 验收结果记录在 `Roadmap.md` 对应任务状态中。

## 测试用例要求
- 每个 Agent 至少覆盖 3 个典型场景。
- 必须包含 1 个边界或异常场景（如缺失图片、空文本、极端情绪）。
- 测试用例直接嵌入各模块的任务说明书，不单独存放。
- 测试失败时必须输出明确的期望值与实际值对比。
- **勿为单次修复堆叠 pytest**：bug 修通后合并进既有典型用例或删除；业务回归以 §LLM 情景评测（scenario_gen + Judge）为主。

## 代码质量标准
- Agent 函数为无状态纯函数，输出仅依赖输入参数。
- 所有 API 调用有超时与重试机制（最多 3 次）。
- 日志输出带 `[AgentX]` 前缀。
- 数据库写入仅限 Agent 5，其他 Agent 不直接连接数据库。

## 集成测试
- 在 Controller 完成后进行首次集成测试，覆盖“事实提取 → 策略推理 → 话术生成”全链路。
- 集成测试场景需包含碎片化对话：首次分析 → 多轮纯文本后再次请求帮助。
- 每次添加新 Agent 或修改 schemas 后重新运行集成测试。

## 前端验收
- 辅助模式：聊天窗口与侧边栏布局正确，按钮触发分析，话术可填充至输入框。
- 模式切换：设置页可切换模式

## LLM 情景评测（主路径）

流程：**评测树 → 情景 Markdown → scenario_gen → 全链路跑批 → 硬断言 / Judge → 批次汇总**。

当前主测集为 **功能验证三期**（`functional/phase1|2|3/`），评测树见下表。`eval/output/` 按 phase 分子目录存放产物（详见 [`eval/output/README.md`](../eval/output/README.md)）。

### 评测树

| 文件 | 叶数 | 内容 |
|------|------|------|
| `eval_tree_ma.yaml` | 3 | 一期 MA 恶意抗辩 |
| `eval_tree_val.yaml` | 3 | 一期 VAL 客户价值 |
| `eval_tree_prec.yaml` | 3 | 一期 PREC 判例引用 |
| `eval_tree_rule_evidence.yaml` | 3 | 一期 RULE 专责补证 |
| `eval_tree_mixed.yaml` | 6 | 二期 MIX 混合单步 |
| `eval_tree_multistep.yaml` | 8 | 三期 MS 多步 |

### 目录结构

| 路径 | 职责 |
|------|------|
| `eval/content/scenarios/functional/` | 功能验证情景 Markdown |
| `eval/content/scenarios/eval_tree_*.yaml` | 各期评测树元数据 |
| `eval/content/scenarios/scenario_template.md` | 情景模板 |
| `eval/content/prompts/` | 情景生成 / Judge 提示词 |
| `eval/pipeline/` | 跑批与 Judge 实现 |
| `eval/output/` | 跑批产出（gitignore） |
| `tests/eval/` | 评测链路 pytest |

### 1. 写情景

复制 [`scenario_template.md`](../eval/content/scenarios/scenario_template.md) 手工编写，或用 Scenario Designer 生成草稿：

```bash
python -m eval.pipeline.scenario_designer \
  --axis 恶意抗辩 \
  --leaf "差评要挟 + 举证已齐" \
  --target-ability "识别语义勒索并守住仅退边界" \
  --factors "差评要挟, 举证已齐, 仅退款诉求" \
  --output eval/content/scenarios/functional/phase1/malicious/MA-99_example.md
```

批量生成 `status: new` 的叶子：

```bash
python -m eval.pipeline.batch_from_tree --tree eval/content/scenarios/eval_tree_ma.yaml -v
```

失败记录写入 `eval/output/batch_gen_errors.jsonl`。

**文件命名**：

| 层级 | 格式 | 示例 |
|------|------|------|
| 源 Markdown | `{轴缩写}-{序号}_{snake_case}.md` | `MA-01_review_blackmail.md` |
| `source_key` | stem 小写 | `ma-01_review_blackmail` |
| `case_id` / 报告名 | stem 大写 | `MA-01_REVIEW_BLACKMAIL` |

**服务标**：须为 `data/rule_match_lexicon.json` → `E_service_tag_to_doc_id` 已有键。可查 `python -c "from eval.pipeline.eval_tree import list_lexicon_service_tags; print(len(list_lexicon_service_tags()))"`。

### 2. 生成用例并跑链路

```bash
python -m eval.pipeline.scenario_gen \
  --input eval/content/scenarios/functional/phase1/malicious/MA-01_review_blackmail.md --run -v
```

产出（均在 `eval/output/`，按 phase 分子目录）：

| 路径 | 内容 |
|------|------|
| `eval/output/scenarios/<phase>/<slug>/` | `spec.json`、`fixture.json` |
| `eval/output/manual_reports/<phase>/` | `{CASE_ID}.md` / `.json` |
| `eval/output/eval_runs/<phase>/<run_id>/` | 批次汇总 |

```bash
python -m eval.pipeline.scenario_gen --spec eval/output/scenarios/phase1/<slug>/spec.json --run -v
```

### 3. Judge 评测

```bash
python -m eval.pipeline.judge_cases \
  --scenario-output eval/output/scenarios/phase1/ma-01_review_blackmail \
  --report eval/output/manual_reports/phase1/MA-01_REVIEW_BLACKMAIL.json \
  -v
```

### 4. 批量评测

```bash
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_ma.yaml -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_multistep.yaml --skip-gen -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_mixed.yaml --only mix-04 -v
```

产出：`BATCH_SUMMARY.md`、分轴通过率、硬失败清单。**不设批次 pass/fail 门槛**。

### 评测实现模块

| 路径 | 作用 |
|------|------|
| `eval/pipeline/eval_tree.py` | 评测树加载与校验 |
| `eval/pipeline/batch_from_tree.py` | 按树批量调用 Scenario Designer |
| `eval/pipeline/run_batch_eval.py` | 批量 gen + 跑批 + Judge + 批次汇总 |
| `eval/pipeline/scenario_gen.py` | 情景 → spec/fixture → 调跑批 |
| `eval/pipeline/spec_to_fixture.py` | spec → fixture（无 LLM） |
| `eval/pipeline/scenario_spec.py` / `scenario_llm_utils.py` | spec 结构与 LLM 调用 |
| `eval/pipeline/run_manual_cases.py` | 跑批引擎（读 fixture.json） |
| `eval/pipeline/judge_cases.py` / `judge_models.py` | Judge 跑批 |

pytest：`tests/eval/test_*.py` 覆盖上述链路；Agent/Controller 单测仍在 `tests/test_agent*.py` 等，与情景评测并行。

## 功能验证评测（一期）

目标：分轴隔离验证全链路决策能力——**硬断言挡门**，Judge **不挡门**（仅记 warnings）。Agent1 视觉由 `evidence_facts` 注入，不测识别准确率。

计划全文见 Cursor Plan：`功能验证评测计划`。一期 **12 叶 / 4 轴**，情景在 `eval/content/scenarios/functional/phase1/`。

### 三期递进

| 期 | 内容 | 情景数 | 挡门 |
|----|------|--------|------|
| 一期 | MA / VAL / PREC / RULE 单轴隔离 | 12 | **仅硬断言** |
| 二期 | 混合单步 MIX | 6 | **仅硬断言** |
| 三期 | 混合多步 MS | 8 | **每步硬断言** |
| 全程 | Judge LLM | 全跑 | **不挡门** |

### 目录与评测树

| 路径 | 职责 |
|------|------|
| `functional/phase1/malicious/` | MA 恶意抗辩（3 叶） |
| `functional/phase1/value/` | VAL 客户价值（3 叶） |
| `functional/phase1/precedent/` | PREC 判例引用（3 叶） |
| `functional/phase1/rule_evidence/` | RULE 专责补证（3 叶） |
| `functional/phase2/mixed/` | MIX 混合单步（6 叶） |
| `eval_tree_ma.yaml` 等四棵 YAML | 一期各轴 3 叶元数据 |
| `eval_tree_mixed.yaml` | 二期混合 6 叶 |
| `eval_tree_multistep.yaml` | 三期多步 8 叶 |
| `eval/content/cases/CASE-*.json` | 辅助判例（PREC / MIX 引用） |
| `eval/pipeline/assert_report.py` | 结构化硬断言 |

**证据约定**：决策类案普通举证默认已齐；仅 RULE 树在「缺失材料」写明规则专责缺口。

**硬断言键**（写在情景 `expected_report` → `spec.expectation`，不进 fixture）：`malicious_risk_level_min` / `customer_value_channel` / `disposition_in` / `action_type` / `similar_cases_min` / `actionable_evidence_requests_contains` 等，见 [`assert_report.py`](../eval/pipeline/assert_report.py)。

### 单案调试

```bash
python -m eval.pipeline.scenario_gen \
  --input eval/content/scenarios/functional/phase1/malicious/MA-01_review_blackmail.md --run -v
python -m eval.pipeline.assert_report \
  --scenario-output eval/output/scenarios/phase1/ma-01_review_blackmail \
  --report eval/output/manual_reports/phase1/MA-01_REVIEW_BLACKMAIL.json -v
python -m eval.pipeline.judge_cases \
  --scenario-output eval/output/scenarios/phase1/ma-01_review_blackmail \
  --report eval/output/manual_reports/phase1/MA-01_REVIEW_BLACKMAIL.json -v
```

### 一期批次（按轴分别跑）

```bash
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_ma.yaml -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_val.yaml -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_prec.yaml -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_rule_evidence.yaml -v
```

产出：`BATCH_SUMMARY.md` 分 **assert_pass** 与 **judge_warnings**；功能验证以硬断言通过率为准。

## 功能验证评测（二期）

目标：多因子**混合单步**验证——每叶叠 ≥2 因子，普通举证默认已齐；**硬断言挡门**，Judge 不挡门。

情景目录：`eval/content/scenarios/functional/phase2/mixed/`（MIX-01～06）。

| ID | 混合因子 |
|----|----------|
| MIX-01 | 高老客 + 差评勒索（`CASE-CONF-01`，恶意压过优待） |
| MIX-02 | 高老客 + 生鲜超48小时 + 坏单争议 |
| MIX-03 | 抗辩判例 + 高仅退画像（`CASE-MAL-01` + `abuse_refund_only`） |
| MIX-04 | 商责明确 + 善后判例 + 赔偿上限（`CASE-MF-01`） |
| MIX-05 | 职业索赔 + 伤亡大病包退 + 举证已齐 |
| MIX-06 | 高本单金额 + 情绪化 + 部分补偿协商（`CASE-VAL-01`） |

### 二期批次

```bash
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_mixed.yaml -v
```

产出与一期相同：`eval/output/eval_runs/<phase>/<run_id>/BATCH_SUMMARY.md`（`assert_pass` + `judge_warnings`）。

## 功能验证评测（三期）

目标：多步对话弧 — 每步独立快照跑批，终局验「给方案」；**每步硬断言**，Judge 可选。

情景目录：`eval/content/scenarios/functional/phase3/multistep/`（MS-01～08）。

```bash
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_multistep.yaml -v
python -m eval.pipeline.run_batch_eval --tree eval/content/scenarios/eval_tree_multistep.yaml --skip-assert --run-id phase3-ms-judge -v
```

多步报告路径：`eval/output/manual_reports/phase3/{CASE_ID}__stepNN_{标签}.json`。
