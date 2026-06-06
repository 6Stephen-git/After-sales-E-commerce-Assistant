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

流程：**情景 Markdown → 用例 LLM → 全链路跑批 → 报告 → Judge LLM**。

### 目录结构

| 路径 | 职责 |
|------|------|
| `eval/content/scenarios/` | 情景 Markdown（模板 + `case/*.md`） |
| `eval/content/prompts/` | 情景生成 / Judge 提示词与 JSON Schema |
| `eval/pipeline/` | 跑批与 Judge 实现（`scenario_gen`、`run_manual_cases`、`judge_cases` 等） |
| `eval/output/` | 跑批与 Judge 产出（gitignore） |
| `tests/eval/` | 评测链路的 pytest 单测 |
| `tests/test_agent*.py` 等 | Agent / Controller 单元与集成测试 |

### 1. 写情景

复制 `eval/content/scenarios/scenario_template.md` 到 `eval/content/scenarios/case/<名称>.md`，按 **6 段 + 其他说明** 填写。

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
python -m eval.pipeline.scenario_gen --input eval/content/scenarios/case/case3.md --run -v
```

产出（均在 `eval/output/`，已 gitignore）：

| 路径 | 内容 |
|------|------|
| `eval/output/scenarios/<slug>/` | `REVIEW.md`、`spec.json`、`fixture.json` |
| `eval/output/manual_reports/` | `CASE-*.md` / `.json`（全链路报告） |

生成后核对 `fixture.json` 中 `platform_service_tags`、`product_category_slug`。仅重跑链路：

```bash
python -m eval.pipeline.scenario_gen --spec eval/output/scenarios/<slug>/spec.json --run -v
```

### 3. Judge 评测

```bash
python -m eval.pipeline.judge_cases \
  --scenario-output eval/output/scenarios/<slug> \
  --report eval/output/manual_reports/CASE-CASE3.json \
  -v
```

产出：`eval/output/eval_runs/<run_id>/records.jsonl`、`SUMMARY.md`。环境变量：`JUDGE_LLM_MODEL`（可回退 `AGENT2_LLM_MODEL`）。

### 评测实现模块

| 路径 | 作用 |
|------|------|
| `eval/pipeline/scenario_gen.py` | 情景 → spec/fixture → 调跑批 |
| `eval/pipeline/spec_to_fixture.py` | spec → fixture（无 LLM） |
| `eval/pipeline/scenario_spec.py` / `scenario_llm_utils.py` | spec 结构与 LLM 调用 |
| `eval/pipeline/run_manual_cases.py` | 跑批引擎（读 fixture.json） |
| `eval/pipeline/judge_cases.py` / `judge_models.py` | Judge 跑批 |

pytest：`tests/eval/test_*.py` 覆盖上述链路；Agent/Controller 单测仍在 `tests/test_agent*.py` 等，与情景评测并行。
