"""
Agent 3 工具集：话术模板读取。

约束：模板路径优先环境变量 TEMPLATES_PATH；文件缺失或解析失败时回退内置模板，避免线上空白话术。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


logger = logging.getLogger(__name__)
AGENT3_LOG_PREFIX = "[Agent3]"
DEFAULT_TEMPLATE_PATH = ROOT_DIR / "data" / "script_templates.json"
VALID_TEMPLATE_KEYS = {"defend", "negotiate", "compensate"}

# ---------- 内置兜底：磁盘不可读或缺键时仍保证三策略模板非空 ----------
_FALLBACK_TEMPLATES: Dict[str, str] = {
    "defend": (
        "您好，订单{{order_id}}我们已经复核。当前事实是：{{fact_summary}}。"
        "为了避免误判，麻烦您补充更清晰的细节证据，我们会马上继续处理。"
    ),
    "negotiate": (
        "您好，订单{{order_id}}我们看过了，当前信息是：{{fact_summary}}。"
        "我们理解您的心情，愿意先按{{offer_amount}}元协商，您看这样可以吗？"
    ),
    "compensate": (
        "您好，订单{{order_id}}问题已确认：{{fact_summary}}。"
        "这次给您添麻烦了，我们愿意按{{offer_amount}}元处理，并尽快完成。"
    ),
}


# ---------- 模板路径解析、JSON 加载与键校验 ----------
def _resolve_template_path() -> Path:
    """
    解析话术模板 JSON 文件路径。

    优先环境变量 TEMPLATES_PATH；未设置时尝试 SCRIPT_TEMPLATES_PATH（兼容旧名）；
    均未设置则使用项目内 data/script_templates.json。

    返回:
        模板文件的 Path（未必存在）。
    """
    env_path = os.getenv("TEMPLATES_PATH") or os.getenv("SCRIPT_TEMPLATES_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_TEMPLATE_PATH


def _load_templates() -> Dict[str, str]:
    """
    读取 JSON 模板文件并校验三键 defend/negotiate/compensate。

    文件不存在或 JSON 非法时回退 _FALLBACK_TEMPLATES；单键缺失时用内置串补全。
    打 info / error / warning 日志，前缀 [Agent3]。

    返回:
        键为策略类型、值为模板字符串的字典，恒含三键。
    """
    template_path = _resolve_template_path()
    logger.info("%s 开始读取话术模板，template_path=%s", AGENT3_LOG_PREFIX, template_path)

    if not template_path.is_file():
        logger.error("%s 模板文件不存在，回退默认模板：%s", AGENT3_LOG_PREFIX, template_path)
        return _FALLBACK_TEMPLATES.copy()

    try:
        with template_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取模板文件失败：%s，回退默认模板", AGENT3_LOG_PREFIX, exc)
        return _FALLBACK_TEMPLATES.copy()

    templates: Dict[str, str] = {}
    for key in VALID_TEMPLATE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            templates[key] = value.strip()
        else:
            logger.warning("%s 模板缺失或为空，使用内置模板：%s", AGENT3_LOG_PREFIX, key)
            templates[key] = _FALLBACK_TEMPLATES[key]

    logger.info("%s 模板读取完成，模板数量=%s", AGENT3_LOG_PREFIX, len(templates))
    return templates


# ---------- 对外工具：按策略类型取模板全文（含 {{变量}}） ----------
def get_script_template(strategy_type: str) -> str:
    """
    返回指定策略类型对应的话术模板字符串（含 {{变量}}）。

    每次调用会经 _load_templates 刷新磁盘内容；未知 strategy_type 时记录 error 并回退 defend 模板。

    参数:
        strategy_type: defend / negotiate / compensate（大小写不敏感）。

    返回:
        模板全文字符串。
    """
    normalized_type = (strategy_type or "").strip().lower()
    templates = _load_templates()

    if normalized_type not in VALID_TEMPLATE_KEYS:
        logger.error("%s 未知策略类型：%s，回退 defend 模板", AGENT3_LOG_PREFIX, strategy_type)
        return templates["defend"]

    return templates[normalized_type]
