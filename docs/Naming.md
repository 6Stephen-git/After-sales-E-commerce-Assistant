# 命名约定

## 代码标识符

- 变量、函数、类名统一使用英文 `snake_case`。
- 示例：`goods_received`、`defect_type`、`analyze_image`、`FactOutput`。
- 禁止使用拼音、中英混杂。

## 数据字段

- 所有输入输出字段名必须与 `schemas.py` 中的 Pydantic 字段名完全一致。
- 字段名使用英文，业务含义由注释说明。

## 枚举值

- 策略类型（内部）：`"defend"` / `"negotiate"` / `"compensate"`
- 证据质量（内部）：`"high"` / `"medium"` / `"low"`
- 话术版本（内部）：`"defense_version"` / `"negotiate_version"` / `"compensate_version"`
- 所有内部传输枚举值统一使用英文小写。
- 前端展示时映射为中文（如 `"defend"` → `"抗辩"`），映射表由前端维护。

## 日志前缀

- Agent x：`[Agentx]`

## 话术模板变量

- 使用双花括号包裹：`{{变量名}}`。
- 变量名优先使用英文，且与 `schemas.py` 字段对应。
- 示例：`{{order_id}}`、`{{amount}}`、`{{defect_type}}`。

## 文件名

- 模块文件：小写英文，单词间用连字符，如 `agent1-fact-extractor.md`。
- 文档文件：大写开头或全小写，按现有约定（如 `Prd.md`、`schemas.py`）。