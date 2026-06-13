"""
智能模式测试模拟数据：可编辑的订单与买家画像，替代平台 API 供本地联调。

数据文件：data/intelligent_simulation.json，可通过 API 或手工修改。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from schemas import BuyerProfile, LogisticsInfo

LOG_PREFIX = "[SimulationFixture]"
logger = logging.getLogger(__name__)

_ROOT_DIR = Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _ROOT_DIR / "data" / "intelligent_simulation.json"


def _default_fixture() -> dict[str, Any]:
    """返回空壳默认配置，文件不存在或损坏时使用。"""
    return {
        "enabled": True,
        "orders": {},
        "buyers": {},
    }


def get_fixture_path() -> Path:
    """返回模拟数据文件路径。"""
    return _FIXTURE_PATH


def load_simulation_fixture() -> dict[str, Any]:
    """
    读取模拟配置文件。

    返回:
        配置字典；读取失败时返回默认空配置并写日志。
    """
    try:
        if not _FIXTURE_PATH.is_file():
            logger.warning("%s 模拟文件不存在，使用默认空配置 path=%s", LOG_PREFIX, _FIXTURE_PATH)
            return _default_fixture()
        raw = _FIXTURE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("根节点必须是对象")
        data.setdefault("enabled", True)
        data.setdefault("orders", {})
        data.setdefault("buyers", {})
        return data
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 读取模拟配置失败：%s", LOG_PREFIX, exc)
        return _default_fixture()


def save_simulation_fixture(data: dict[str, Any]) -> None:
    """
    持久化模拟配置到 JSON 文件。

    参数:
        data: 完整配置对象。

    异常:
        ValueError: 结构不合法。
        OSError: 写入失败。
    """
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须是对象")
    normalized = {
        "enabled": bool(data.get("enabled", True)),
        "orders": data.get("orders") if isinstance(data.get("orders"), dict) else {},
        "buyers": data.get("buyers") if isinstance(data.get("buyers"), dict) else {},
    }
    try:
        _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("%s 模拟配置已保存 path=%s", LOG_PREFIX, _FIXTURE_PATH)
    except OSError as exc:
        raise OSError(f"写入模拟配置失败：{exc}") from exc


def is_simulation_enabled() -> bool:
    """模拟开关是否开启。"""
    return bool(load_simulation_fixture().get("enabled", False))


def get_simulated_order(order_id: str) -> dict[str, Any] | None:
    """
    按订单号查找模拟订单；未命中或未启用时返回 None。

    参数:
        order_id: 平台订单号。

    返回:
        订单配置字典或 None。
    """
    normalized = str(order_id or "").strip()
    if not normalized or not is_simulation_enabled():
        return None
    orders = load_simulation_fixture().get("orders") or {}
    if not isinstance(orders, dict):
        return None
    entry = orders.get(normalized)
    return entry if isinstance(entry, dict) else None


def get_simulated_buyer_profile(buyer_id: str) -> BuyerProfile | None:
    """
    按买家 ID 查找模拟画像；未命中或未启用时返回 None。

    参数:
        buyer_id: 买家脱敏 ID。

    返回:
        BuyerProfile 或 None。
    """
    normalized = str(buyer_id or "").strip()
    if not normalized or not is_simulation_enabled():
        return None
    buyers = load_simulation_fixture().get("buyers") or {}
    if not isinstance(buyers, dict):
        return None
    entry = buyers.get(normalized)
    if not isinstance(entry, dict):
        return None
    try:
        payload = dict(entry)
        payload["buyer_id"] = normalized
        return BuyerProfile(**payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 解析模拟买家画像失败 buyer_id=%s：%s", LOG_PREFIX, normalized, exc)
        return None


def get_simulated_logistics(order_id: str) -> LogisticsInfo | None:
    """
    按订单号返回模拟物流；未配置时返回 None。

    参数:
        order_id: 平台订单号。

    返回:
        LogisticsInfo 或 None。
    """
    order_entry = get_simulated_order(order_id)
    if not order_entry:
        return None
    logistics_raw = order_entry.get("logistics")
    if not isinstance(logistics_raw, dict):
        return None
    try:
        return LogisticsInfo(
            is_shipped=bool(logistics_raw.get("is_shipped", False)),
            is_signed=bool(logistics_raw.get("is_signed", False)),
            stagnant_days=int(logistics_raw.get("stagnant_days", 0) or 0),
            is_abnormal=bool(logistics_raw.get("is_abnormal", False)),
        )
    except (TypeError, ValueError) as exc:
        logger.error("%s 解析模拟物流失败 order_id=%s：%s", LOG_PREFIX, order_id, exc)
        return None


def get_simulated_logistics_text(order_id: str) -> str:
    """
    返回面向 Agent 的物流状态描述文本（含 status_text 扩展字段）。

    参数:
        order_id: 平台订单号。

    返回:
        描述文本；无模拟数据时返回空字符串。
    """
    order_entry = get_simulated_order(order_id)
    if not order_entry:
        return ""
    logistics_raw = order_entry.get("logistics")
    if not isinstance(logistics_raw, dict):
        return ""
    status_text = str(logistics_raw.get("status_text") or "").strip()
    info = get_simulated_logistics(order_id)
    if info is None:
        return status_text
    parts = [
        f"已发货={info.is_shipped}",
        f"已签收={info.is_signed}",
        f"停滞天数={info.stagnant_days}",
        f"异常={info.is_abnormal}",
    ]
    if status_text:
        parts.append(f"状态说明={status_text}")
    return "，".join(parts)


def apply_simulated_order_context(
    *,
    order_id: str,
    order_amount: float,
    buyer_id: str,
    product_category_slug: str,
    platform_service_tags: list[str] | None,
) -> tuple[float, str, str, list[str]]:
    """
    用模拟订单补全请求上下文中为空的字段（不覆盖调用方已填写的值）。

    返回:
        (order_amount, buyer_id, product_category_slug, platform_service_tags)
    """
    entry = get_simulated_order(order_id)
    if not entry:
        return order_amount, buyer_id, product_category_slug, list(platform_service_tags or [])

    resolved_amount = order_amount
    if resolved_amount <= 0 and entry.get("order_amount") is not None:
        try:
            resolved_amount = float(entry["order_amount"])
        except (TypeError, ValueError):
            pass

    resolved_buyer_id = buyer_id.strip() or str(entry.get("buyer_id") or "").strip()
    resolved_category = product_category_slug.strip() or str(entry.get("product_category_slug") or "").strip()
    resolved_tags = list(platform_service_tags or [])
    if not resolved_tags and isinstance(entry.get("platform_service_tags"), list):
        resolved_tags = [str(t).strip() for t in entry["platform_service_tags"] if str(t).strip()]

    return resolved_amount, resolved_buyer_id, resolved_category, resolved_tags
