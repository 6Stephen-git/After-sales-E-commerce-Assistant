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

流程：**情景 Markdown → 用例 LLM → 全链路跑批 → 报告 → Judge LLM**。

### 1. 写情景

复制 `tests/scenarios/scenario_template.md` 到 `tests/scenarios/case/<名称>.md`，按 **6 段 + 其他说明** 填写。

| 段落 | 写什么 |
|------|--------|
| 背景 | 品类、金额、签收、服务标（lexicon 短名） |
| 买家 | 表格 +「特殊说明」；`return_rate` / `refund_only_rate` 分开 |
| 争议 | 买家诉求 + 关键聊天 |
| 事实证据 | 同 case2 bullet → `evidence_facts`（不传 URL） |
| 参考 | 判例或「无」 |
| 期望与禁忌 | 期望策略 + **禁止**逐条 |
| 其他说明 | 老客价值/赔偿/恶意规则自然语言 → `test_overrides` 数字键 |

### 2. 生成用例并跑链路

```bash
python tests/scenario_gen.py --input tests/scenarios/case/case3.md --run -v
```

产出（均在 `tests/output/`，已 gitignore）：

| 路径 | 内容 |
|------|------|
| `tests/output/scenarios/<slug>/` | `REVIEW.md`、`spec.json`、`fixture.json` |
| `tests/output/manual_reports/` | `SCENARIO-001.md` / `.json`（全链路报告） |

生成后核对 `fixture.json` 中 `platform_service_tags`、`product_category_slug`。仅重跑链路：

```bash
python tests/scenario_gen.py --spec tests/output/scenarios/<slug>/spec.json --run -v
```

### 3. Judge 评测

```bash
python tests/judge_cases.py \
  --scenario-output tests/output/scenarios/<slug> \
  --report tests/output/manual_reports/SCENARIO-001.json \
  -v
```

产出：`tests/output/eval_runs/<run_id>/records.jsonl`、`SUMMARY.md`。环境变量：`JUDGE_LLM_MODEL`（可回退 `AGENT2_LLM_MODEL`）。

### 评测相关代码（保留）

| 路径 | 作用 |
|------|------|
| `tests/scenarios/` | 情景 Markdown（`scenario_template.md`、`case/*.md`） |
| `tests/scenario_gen.py` | 情景 → spec/fixture → 调跑批 |
| `tests/spec_to_fixture.py` | spec → fixture（无 LLM） |
| `tests/scenario_spec.py` / `scenario_llm_utils.py` | spec 结构与 LLM 调用 |
| `tests/run_manual_cases.py` | 跑批引擎（读 fixture.json） |
| `tests/judge_cases.py` / `judge_models.py` | Judge 跑批 |
| `tests/prompts/` | `scenario_gen_system.md`、`judge_system.md`、JSON Schema |

单元测试：`test_spec_to_fixture.py`、`test_scenario_llm_json.py`、`test_run_manual_cases.py`、`test_judge_cases.py` 覆盖上述链路；Agent/Controller 单测仍在 `tests/test_agent*.py` 等，与情景评测并行。
