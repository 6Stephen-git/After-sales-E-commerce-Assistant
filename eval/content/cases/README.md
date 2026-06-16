# 辅助判例库

每条判例一个 JSON 文件，字段与 `SimilarCase`（`schemas.py`）一致，跑批时原样注入 `spec.similar_cases`。

## 文件

`CASE-{轴}-{序号}.json`，如 `CASE-MAL-01.json`。

## 情景引用

```markdown
## 参考

- CASE-MAL-01
```

无判例时写 `无`。一期 MA/VAL/RULE 轴写 `无`；PREC 轴引用本目录 JSON。

## 字段

`case_id`、`similarity`（0～1）、`merchant_action`、`outcome`、`lesson`
