"""
将 data/dispute_rules.json 中的可执行规则（带 conditions）写入 MySQL platform_rules。

用途：开发/联调时保证 match_rules 主链能命中通用退换货、七天无理由等引擎规则。
爬虫落库的全文档规则无 conditions，不会参与 match_rules，需单独导入本脚本产物。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from backend.db.connection import get_engine  # noqa: E402
from backend.db.models import PlatformRule  # noqa: E402
from scripts.import_rules_raw_to_mysql import ensure_mysql_platform_rules_schema  # noqa: E402


LOG_PREFIX = "[SeedEngineRules]"
ENGINE_RULE_KEY = "engine_rules::dispute_v1"
RULES_JSON_PATH = ROOT_DIR / "data" / "dispute_rules.json"
logger = logging.getLogger(__name__)


def load_dispute_rules_payload() -> dict:
    """
    读取 dispute_rules.json 全量内容。
    """
    if not RULES_JSON_PATH.is_file():
        raise RuntimeError(f"{LOG_PREFIX} 规则文件不存在：{RULES_JSON_PATH}")
    with RULES_JSON_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise RuntimeError(f"{LOG_PREFIX} dispute_rules.json 结构非法，需包含 rules 数组")
    return payload


def upsert_engine_rules_bundle() -> None:
    """
    以单条 platform_rules 记录存储引擎规则包（rule_content.rules 供 match_rules 加载）。
    """
    payload = load_dispute_rules_payload()
    rule_content = json.dumps(payload, ensure_ascii=False)
    engine = get_engine()
    ensure_mysql_platform_rules_schema(engine)
    with Session(bind=engine) as session:
        try:
            existing = session.execute(
                select(PlatformRule).where(PlatformRule.rule_key == ENGINE_RULE_KEY)
            ).scalar_one_or_none()
            if existing is None:
                session.add(PlatformRule(rule_key=ENGINE_RULE_KEY, rule_content=rule_content))
                logger.info("%s 新增引擎规则包：%s，规则条数=%s", LOG_PREFIX, ENGINE_RULE_KEY, len(payload["rules"]))
            else:
                existing.rule_content = rule_content
                logger.info("%s 更新引擎规则包：%s，规则条数=%s", LOG_PREFIX, ENGINE_RULE_KEY, len(payload["rules"]))
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise RuntimeError(f"{LOG_PREFIX} 写入 MySQL 失败：{exc}") from exc


def main() -> int:
    """脚本入口。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        upsert_engine_rules_bundle()
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("%s %s", LOG_PREFIX, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
