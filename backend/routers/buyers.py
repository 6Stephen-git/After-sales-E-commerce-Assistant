"""
买家画像路由。

职责：提供按 merchant_id 隔离的买家画像查询、写入与删除能力。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.connection import get_db_session
from backend.db.models import BuyerProfileRecord
from schemas import BuyerProfile


API_LOG_PREFIX = "[API]"
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/merchants", tags=["buyers"])


# ---------- 请求体模型：限制写入字段并沿用 BuyerProfile 语义 ----------
class BuyerProfileUpsertRequest(BaseModel):
    """
    买家画像写入请求结构。
    """

    purchase_count: int = Field(default=0, ge=0, description="在本店累计购买次数")
    dispute_count: int = Field(default=0, ge=0, description="在本店历史纠纷次数")
    dispute_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="本店纠纷率")
    avg_order_value: float = Field(default=0.0, ge=0.0, description="本店平均客单价")
    return_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="本店退货率")
    malicious_flags: int = Field(default=0, ge=0, description="被标记恶意次数")
    credit_level: str | None = Field(default=None, description="平台信誉等级")


# ---------- 数据转换：数据库记录转标准 BuyerProfile ----------
def _record_to_profile(record: BuyerProfileRecord, buyer_hash: str) -> BuyerProfile:
    """
    将 buyer_profiles 表记录转换为 BuyerProfile。
    """
    try:
        payload = json.loads(record.profile_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"profile_json 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("profile_json 根节点必须为对象")
    payload["buyer_id"] = buyer_hash
    return BuyerProfile(**payload)


# ---------- 查询端点：按 merchant_id + buyer_hash 读取画像 ----------
@router.get("/{merchant_id}/buyers/{buyer_hash}", response_model=BuyerProfile)
def get_buyer_profile(
    merchant_id: str,
    buyer_hash: str,
    session: Session = Depends(get_db_session),
) -> BuyerProfile:
    """
    查询单个买家画像；不存在返回 404。
    """
    normalized_merchant_id = merchant_id.strip()
    normalized_buyer_hash = buyer_hash.strip()
    if not normalized_merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")
    if not normalized_buyer_hash:
        raise HTTPException(status_code=400, detail="buyer_hash 不能为空")

    logger.info(
        "%s 开始查询买家画像，merchant_id=%s buyer_hash=%s",
        API_LOG_PREFIX,
        normalized_merchant_id,
        normalized_buyer_hash,
    )
    try:
        record = session.execute(
            select(BuyerProfileRecord).where(
                BuyerProfileRecord.merchant_id == normalized_merchant_id,
                BuyerProfileRecord.buyer_hash == normalized_buyer_hash,
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="买家画像不存在")
        profile = _record_to_profile(record, normalized_buyer_hash)
        logger.info(
            "%s 买家画像查询完成，merchant_id=%s buyer_hash=%s",
            API_LOG_PREFIX,
            normalized_merchant_id,
            normalized_buyer_hash,
        )
        return profile
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 买家画像查询失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"买家画像查询失败：{exc}") from exc


# ---------- 写入端点：按 merchant_id + buyer_hash 做 upsert ----------
@router.put("/{merchant_id}/buyers/{buyer_hash}", response_model=BuyerProfile)
def upsert_buyer_profile(
    merchant_id: str,
    buyer_hash: str,
    request: BuyerProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> BuyerProfile:
    """
    写入或更新买家画像。
    """
    normalized_merchant_id = merchant_id.strip()
    normalized_buyer_hash = buyer_hash.strip()
    if not normalized_merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")
    if not normalized_buyer_hash:
        raise HTTPException(status_code=400, detail="buyer_hash 不能为空")

    profile = BuyerProfile(
        buyer_id=normalized_buyer_hash,
        purchase_count=request.purchase_count,
        dispute_count=request.dispute_count,
        dispute_rate=request.dispute_rate,
        avg_order_value=request.avg_order_value,
        return_rate=request.return_rate,
        malicious_flags=request.malicious_flags,
        credit_level=request.credit_level,
    )
    logger.info(
        "%s 开始写入买家画像，merchant_id=%s buyer_hash=%s",
        API_LOG_PREFIX,
        normalized_merchant_id,
        normalized_buyer_hash,
    )
    try:
        record = session.execute(
            select(BuyerProfileRecord).where(
                BuyerProfileRecord.merchant_id == normalized_merchant_id,
                BuyerProfileRecord.buyer_hash == normalized_buyer_hash,
            )
        ).scalar_one_or_none()
        profile_json = json.dumps(profile.model_dump(exclude={"buyer_id"}), ensure_ascii=False)
        if record is None:
            record = BuyerProfileRecord(
                merchant_id=normalized_merchant_id,
                buyer_hash=normalized_buyer_hash,
                profile_json=profile_json,
            )
            session.add(record)
        else:
            record.profile_json = profile_json
        session.commit()
        logger.info(
            "%s 买家画像写入完成，merchant_id=%s buyer_hash=%s",
            API_LOG_PREFIX,
            normalized_merchant_id,
            normalized_buyer_hash,
        )
        return profile
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("%s 买家画像写入失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"买家画像写入失败：{exc}") from exc


# ---------- 删除端点：按 merchant_id + buyer_hash 删除画像 ----------
@router.delete("/{merchant_id}/buyers/{buyer_hash}")
def delete_buyer_profile(
    merchant_id: str,
    buyer_hash: str,
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """
    删除买家画像记录。
    """
    normalized_merchant_id = merchant_id.strip()
    normalized_buyer_hash = buyer_hash.strip()
    if not normalized_merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id 不能为空")
    if not normalized_buyer_hash:
        raise HTTPException(status_code=400, detail="buyer_hash 不能为空")

    logger.info(
        "%s 开始删除买家画像，merchant_id=%s buyer_hash=%s",
        API_LOG_PREFIX,
        normalized_merchant_id,
        normalized_buyer_hash,
    )
    try:
        record = session.execute(
            select(BuyerProfileRecord).where(
                BuyerProfileRecord.merchant_id == normalized_merchant_id,
                BuyerProfileRecord.buyer_hash == normalized_buyer_hash,
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="买家画像不存在")
        session.delete(record)
        session.commit()
        logger.info(
            "%s 买家画像删除完成，merchant_id=%s buyer_hash=%s",
            API_LOG_PREFIX,
            normalized_merchant_id,
            normalized_buyer_hash,
        )
        return {"message": "买家画像删除成功"}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("%s 买家画像删除失败：%s", API_LOG_PREFIX, exc)
        raise HTTPException(status_code=500, detail=f"买家画像删除失败：{exc}") from exc
