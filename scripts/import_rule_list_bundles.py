"""
从 F12 导出的「规则列表」mtop JSON 生成 rule_crawl_targets.json，供正文抓取脚本使用。

适用接口示例：mtop.alibaba.rulechannel.newrule.rule.list（响应含 data.model[].ruleId / lastCategoryId / ruleTitle）。

说明：本项目不实现 mtop 签名；请你在浏览器复制响应体保存为 .json 文件，由本脚本解析。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLES = ROOT_DIR / "data" / "rule_list_bundles.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "rule_crawl_targets.json"
LOG_PREFIX = "[RuleListImport]"

logger = logging.getLogger(__name__)

# 写入 rule_crawl_targets.json 时置于数组首位的说明占位：
# enabled=false，crawl_rule_documents.load_targets 会跳过，仅给人阅读字段含义与流程。
TARGETS_README: dict[str, Any] = {
    "_说明": (
        "本文件由本脚本生成/合并，供 scripts/crawl_rule_documents.py 抓取详情正文。"
        "每条有效记录需含：enabled（默认 true）、doc_id、doc_name、category、url。"
        "本占位条 enabled 为 false，不参与抓取；重新执行导入时会自动保持在本文件最前。"
    ),
    "enabled": False,
}


def parse_args() -> argparse.Namespace:
    """解析 CLI：bundles 路径、输出路径、是否禁止合并。"""
    parser = argparse.ArgumentParser(description="从 mtop 规则列表 JSON 导入到 rule_crawl_targets.json")
    parser.add_argument(
        "--bundles",
        type=Path,
        default=DEFAULT_BUNDLES,
        help="bundle 配置文件路径（默认 data/rule_list_bundles.json）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出的 targets 路径（默认 data/rule_crawl_targets.json）",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="不合并已有 targets，仅输出本次解析结果",
    )
    return parser.parse_args()


def safe_path_token(text: str) -> str:
    """将分类/标题转为文件名安全片段。"""
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", text.strip())
    return cleaned or "unknown"


def make_doc_id(category: str, c_id: int, rule_id: int, title: str) -> str:
    """与 discover 脚本风格一致的 doc_id，保证跨工具一致。"""
    leaf = category.split("/")[-1]
    leaf_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "_", leaf).strip("_") or "rules"
    title_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "_", title.strip()).strip("_") or "untitled"
    return f"{leaf_norm}_{title_norm}_{c_id}_{rule_id}"


def build_detail_url(rule_id: int, c_id: int) -> str:
    """拼装规则中心详情页 URL（与人工在浏览器中复制的格式一致）。"""
    return (
        f"https://rule.taobao.com/?type=detail&ruleId={rule_id}&cId={c_id}"
        f"#/rule/detail?ruleId={rule_id}&cId={c_id}"
    )


def load_mtop_payloads_from_file(path: Path) -> list[dict[str, Any]]:
    """
    读取 .json 文件中的 mtop 响应。
    支持：单对象、或浏览器/编辑器里拼接的多个顶层的 JSON 对象（中间可有空白）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        logger.error("%s 找不到响应文件：%s", LOG_PREFIX, path)
        raise RuntimeError(f"找不到列表响应文件：{path}") from exc
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    payloads: list[dict[str, Any]] = []
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError as exc:
            logger.error("%s 多段 JSON 解析失败：%s — 偏移 %s：%s", LOG_PREFIX, path, idx, exc)
            raise RuntimeError(f"列表响应 JSON 解析失败（偏移 {idx}）：{path} — {exc}") from exc
        if isinstance(obj, dict):
            payloads.append(obj)
        idx = end
    if not payloads:
        raise RuntimeError(f"列表响应文件中未解析到任何 JSON 对象：{path}")
    return payloads


def category_from_model(model: dict[str, Any]) -> str:
    """从 model.category 推导「一级类/二级类」路径（与站点侧栏一致）。"""
    block = model.get("category")
    if not isinstance(block, dict):
        return ""
    parent = str(block.get("categoryName", "")).strip()
    children = block.get("childCategory")
    if isinstance(children, list) and children:
        first = children[0]
        if isinstance(first, dict):
            child = str(first.get("categoryName", "")).strip()
            if parent and child:
                return f"{parent}/{child}"
    return parent


def mtop_list_success(payload: dict[str, Any]) -> bool:
    """判断 mtop 返回是否成功。"""
    ret = payload.get("ret")
    if isinstance(ret, list) and ret:
        first = str(ret[0])
        return first.upper().startswith("SUCCESS")
    return False


def parse_models_from_mtop(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    从列表接口 JSON 抽取 model 数组。
    兼容 data.model；若 empty 为 true 则返回空列表。
    """
    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        return []
    if data_block.get("empty") is True:
        return []
    models = data_block.get("model")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def load_existing_targets(path: Path) -> dict[str, dict[str, Any]]:
    """读取已有 targets，按 url 索引以便合并。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%s 读取已有 targets 失败，将视为空：%s", LOG_PREFIX, exc)
        return {}
    if not isinstance(payload, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in payload:
        if isinstance(item, dict) and item.get("url"):
            out[str(item["url"])] = item
    return out


def load_bundles(path: Path) -> list[dict[str, Any]]:
    """加载 bundle 配置列表（须含 response_file 或 list_response）。"""
    if not path.exists():
        raise FileNotFoundError(f"未找到 bundle 配置：{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("bundle 配置必须为 JSON 数组")
    out: list[dict[str, Any]] = []
    for x in raw:
        if not isinstance(x, dict) or not x.get("enabled", True):
            continue
        if not (x.get("response_file") or x.get("list_response")):
            continue
        out.append(x)
    return out


def model_to_target(category: str, default_c_id: int | None, model: dict[str, Any]) -> dict[str, Any] | None:
    """
    将单条 model 转为 rule_crawl_targets 条目。
    cId 优先 lastCategoryId，其次 bundle 顶层 c_id。
    """
    rule_id = model.get("ruleId")
    title = str(model.get("ruleTitle", "")).replace("\u00a0", " ").replace("\u200b", "").strip()
    last_c = model.get("lastCategoryId")
    if rule_id is None or not title:
        return None
    try:
        rid = int(rule_id)
    except (TypeError, ValueError):
        return None
    cid: int | None = None
    if last_c is not None:
        try:
            cid = int(last_c)
        except (TypeError, ValueError):
            cid = None
    if cid is None and default_c_id is not None:
        cid = int(default_c_id)
    if cid is None:
        logger.warning("%s 跳过条目（缺少 cId）：ruleId=%s title=%s", LOG_PREFIX, rid, title)
        return None

    url = build_detail_url(rid, cid)
    return {
        "enabled": True,
        "doc_id": make_doc_id(category, cid, rid, title),
        "doc_name": title,
        "category": category,
        "url": url,
    }


def load_payloads_for_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """
    从 bundle 得到若干段 mtop 响应：list_response 可为对象或对象数组；
    response_file 可为单对象或多对象拼接文件。
    """
    if "list_response" in bundle:
        inline = bundle["list_response"]
        if isinstance(inline, dict):
            return [inline]
        if isinstance(inline, list):
            out = [x for x in inline if isinstance(x, dict)]
            if not out:
                raise ValueError("list_response 数组中无有效 JSON 对象")
            return out
        raise ValueError("list_response 必须是 JSON 对象或对象数组")
    rel = bundle.get("response_file")
    if isinstance(rel, str) and rel.strip():
        file_path = (ROOT_DIR / rel.strip()).resolve()
        return load_mtop_payloads_from_file(file_path)
    raise ValueError("bundle 需包含 response_file 或 list_response")


def main() -> int:
    """入口：读 bundles → 解析 model → 写 targets（可选合并）。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    try:
        bundles = load_bundles(args.bundles)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取 bundle 配置失败：%s", LOG_PREFIX, exc)
        return 2

    all_by_url: dict[str, dict[str, Any]] = {}
    if not args.no_merge:
        all_by_url = load_existing_targets(args.output)

    for bundle in bundles:
        category_fallback = str(bundle.get("category", "")).strip()
        bundle_label = category_fallback or str(bundle.get("response_file", "list_response"))

        default_c_id: int | None = None
        if bundle.get("c_id") is not None and str(bundle.get("c_id")).strip() != "":
            try:
                default_c_id = int(bundle["c_id"])
            except (TypeError, ValueError):
                default_c_id = None

        try:
            payloads = load_payloads_for_bundle(bundle)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s bundle [%s] 无法加载列表响应：%s", LOG_PREFIX, bundle_label, exc)
            continue

        for payload in payloads:
            if not mtop_list_success(payload):
                api_name = str(payload.get("api", ""))
                logger.warning("%s bundle [%s] 某段响应 ret 非 SUCCESS，已跳过（api=%s）", LOG_PREFIX, bundle_label, api_name)
                continue

            models = parse_models_from_mtop(payload)
            logger.info("%s bundle [%s] 某段响应解析到 %s 条规则", LOG_PREFIX, bundle_label, len(models))
            for model in models:
                category = category_from_model(model) or category_fallback
                if not category:
                    logger.warning("%s 跳过 model：无法推导 category（ruleId=%s）", LOG_PREFIX, model.get("ruleId"))
                    continue
                target = model_to_target(category, default_c_id, model)
                if target is None:
                    continue
                all_by_url[target["url"]] = target

    result = sorted(all_by_url.values(), key=lambda x: (x["category"], x["doc_name"], x["url"]))
    # 说明占位 + 实际目标，保证 targets 文件自带可读注释且不参与抓取
    output_payload = [TARGETS_README, *result]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "%s 已写入 %s（数组共 %s 项：1 条说明占位 + %s 条抓取目标）",
        LOG_PREFIX,
        args.output,
        len(output_payload),
        len(result),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
