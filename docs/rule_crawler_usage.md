# 规则抓取脚本使用说明（精简版）

当前仅保留一条稳定链路：**F12 导出列表响应 -> 生成 targets -> 抓正文落盘**。

## 0) 目录职责

### `scripts`

- `import_rule_list_bundles.py`: 解析 F12 导出的列表响应，生成 `rule_crawl_targets.json`
- `crawl_rule_documents.py`: 按 targets 抓取正文并落盘到 `data/rules_raw/`

### `data`

- `rule_list_bundles.json`: 列表响应导入配置
- `rule_list_exports/*.json`: 你手工导出的列表响应原始文件
- `rule_crawl_targets.json`: 待抓取规则详情链接清单
- `rules_raw/`: 抓取结果目录（`.txt` / `.json` / `_crawl_report.json`）

## 1) 准备列表响应 JSON

淘宝规则列表接口常见为 `mtop.alibaba.rulechannel.newrule.rule.list`，响应里 `data.model[]` 包含 `ruleId`、`lastCategoryId`、`ruleTitle`。

1. 浏览器 F12 -> Network -> 找到该请求。
2. 复制响应体为 UTF-8 的 `.json` 文件，放到 `data/rule_list_exports/`。
3. 多段响应可以直接拼接在同一个文件中（多个顶层 JSON 对象，中间有空行也可）。

> 本仓库不实现 mtop 签名，只解析你从浏览器导出的响应体。

## 2) 配置导入清单

编辑 `data/rule_list_bundles.json`，每条可用字段：

- `enabled`: 是否启用
- `response_file`: 列表响应文件（相对项目根路径）
- `list_response`: 也可直接内嵌响应对象/对象数组（小数据临时用）
- `c_id`: 可选，作为 `lastCategoryId` 缺失时兜底
- `category`: 可选，无法从 model 推导分类时兜底
- `_说明`: 可选，给人看的说明文字；可与真实配置写在同一对象中

可增加 **`enabled: false` 且仅含 `_说明`** 的条目作为文件头注释，导入脚本会跳过该条。

## 3) 生成待抓取 URL

在项目根执行：

```bash
python scripts/import_rule_list_bundles.py
```

默认会合并写入 `data/rule_crawl_targets.json`；如需只保留本次导入结果可加 `--no-merge`。

写入后该文件 **数组首项** 会固定带一条 `enabled: false` 的说明占位（供阅读、不参与抓取）；其余为真实目标。

## 4) 抓取规则正文

在项目根执行：

```bash
python scripts/crawl_rule_documents.py
```

默认行为：

- 串行抓取（低频）
- 每条前随机等待 5~10 秒
- 单条失败自动重试 2 次
- 输出目录为 `data/rules_raw/`

## 5) 常用参数

```bash
python scripts/crawl_rule_documents.py --headful
python scripts/crawl_rule_documents.py --min-delay 3 --max-delay 6
python scripts/crawl_rule_documents.py --max-items 5
python scripts/crawl_rule_documents.py --user-data-dir "C:/Users/xxx/AppData/Local/Microsoft/Edge/User Data" --profile-directory "Default"
```

## 6) 输出文件说明

- `data/rule_crawl_targets.json`: 本次可抓取链接清单
- `data/rules_raw/<category>/<doc_id>.txt`: 清洗后的正文文本
- `data/rules_raw/<category>/<doc_id>.json`: 结构化结果（含条款切分）
- `data/rules_raw/_crawl_report.json`: 汇总报告（成功/失败统计和文件路径）
