"""
商家配置路由。

职责：提供商家 mode 与阈值配置的查询和更新接口。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.connection import get_db_session
from backend.db.models import MerchantConfig
from backend.defaults import default_merchant_id


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/merchants", tags=["merchants"])
VALID_MODES = {"assisted", "intelligent"}


# ---------- 响应体模型：统一商家配置返回 ----------
class MerchantConfigResponse(BaseModel):
    """
    商家配置响应结构。
    """

    merchant_id: str
    mode: str
    auto_threshold: float


# ---------- 请求体模型：限制配置更新字段 ----------
class MerchantConfigUpdateRequest(BaseModel):
    """
    商家配置更新请求结构。
    """

    mode: str = Field(..., description="模式：assisted/intelligent")
    auto_threshold: float = Field(0.8, ge=0.0, le=1.0, description="自动化阈值 0~1")


# ---------- 默认商家配置：前端无需传 merchant_id ----------
@router.get("/config")
def get_default_merchant_config(session: Session = Depends(get_db_session)) -> MerchantConfigResponse:
    """
    读取默认商家配置（DEFAULT_MERCHANT_ID）。
    """
    return get_merchant_config(default_merchant_id(), session)


@router.put("/config")
def update_default_merchant_config(
    request: MerchantConfigUpdateRequest,
    session: Session = Depends(get_db_session),
) -> MerchantConfigResponse:
    """
    更新默认商家配置（DEFAULT_MERCHANT_ID）。
    """
    return update_merchant_config(default_merchant_id(), request, session)


# ---------- 读取流程：若不存在则初始化默认配置 ----------
@router.get("/{merchant_id}/config")
def get_merchant_config(merchant_id: str, session: Session = Depends(get_db_session)) -> MerchantConfigResponse:
    """
    获取商家配置，不存在时自动初始化默认配置。
    """
    normalized_merchant_id = merchant_id.strip()
    if not normalized_merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")

    logger.info("%s 开始读取商家配置，merchant_id=%s", API_LOG_PREFIX, normalized_merchant_id)
    try:
        config = session.get(MerchantConfig, normalized_merchant_id)
        if config is None:
            config = MerchantConfig(merchant_id=normalized_merchant_id, mode="assisted", auto_threshold=0.8)
            session.add(config)
            session.commit()
            session.refresh(config)

        logger.info("%s 商家配置读取完成，merchant_id=%s", API_LOG_PREFIX, normalized_merchant_id)
        return MerchantConfigResponse(
            merchant_id=config.merchant_id,
            mode=config.mode,
            auto_threshold=config.auto_threshold,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("%s 商家配置读取失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"读取商家配置失败：{exc}") from exc


# ---------- 更新流程：模式校验 + upsert ----------
@router.put("/{merchant_id}/config")
def update_merchant_config(
    merchant_id: str,
    request: MerchantConfigUpdateRequest,
    session: Session = Depends(get_db_session),
) -> MerchantConfigResponse:
    """
    更新商家配置。
    """
    normalized_merchant_id = merchant_id.strip()
    if not normalized_merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")
    if request.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail="mode 非法，必须为 assisted 或 intelligent")

    logger.info("%s 开始更新商家配置，merchant_id=%s", API_LOG_PREFIX, normalized_merchant_id)
    try:
        config = session.get(MerchantConfig, normalized_merchant_id)
        if config is None:
            config = MerchantConfig(merchant_id=normalized_merchant_id)
            session.add(config)

        config.mode = request.mode
        config.auto_threshold = request.auto_threshold
        session.commit()
        session.refresh(config)
        logger.info("%s 商家配置更新完成，merchant_id=%s", API_LOG_PREFIX, normalized_merchant_id)
        return MerchantConfigResponse(
            merchant_id=config.merchant_id,
            mode=config.mode,
            auto_threshold=config.auto_threshold,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("%s 商家配置更新失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"更新商家配置失败：{exc}") from exc
