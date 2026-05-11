"""
将 data/rules_raw 下的规则文件落库到 MySQL platform_rules 表。

设计原则：
1) 复用项目现有数据库连接配置（DB_URL 或 DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD）；
2) 以 doc_id 生成稳定 rule_key，按 rule_key 做 upsert；
3) 先做 schema 兜底，保证 MySQL 下 rule_content 为 LONGTEXT，避免大文本截断。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 与 uvicorn 启动后端一致：从项目根 .env 注入 DB_*，避免命令行单独跑脚本时读不到密码
load_dotenv(ROOT_DIR / ".env")

from backend.db.connection import get_engine  # noqa: E402
from backend.db.models import PlatformRule  # noqa: E402


LOG_PREFIX = "[RuleMysqlImport]"
logger = logging.getLogger(__name__)
DEFAULT_INPUT_DIR = ROOT_DIR / "data" / "rules_raw"
DEFAULT_REPORT_PATH = DEFAULT_INPUT_DIR / "_crawl_report.json"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="将 rules_raw 规则文件导入 MySQL platform_rules")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="规则文件目录（默认 data/rules_raw）",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="抓取报告路径（默认 data/rules_raw/_crawl_report.json）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅解析并统计，不写数据库",
    )
    return parser.parse_args()


def ensure_mysql_platform_rules_schema(engine: Engine) -> None:
    """
    在 MySQL 下兜底创建/修正 platform_rules 表结构。

    说明：
    - create table if not exists：兼容首次部署；
    - modify column rule_content longtext：兼容早期 TEXT 长度不足场景。
    """
    if engine.dialect.name != "mysql":
        logger.info("%s 当前方言为 %s，跳过 MySQL DDL 兜底", LOG_PREFIX, engine.dialect.name)
        return
    logger.info("%s 开始校验 MySQL 表结构（platform_rules）", LOG_PREFIX)
    ddl_create = """
    CREATE TABLE IF NOT EXISTS platform_rules (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      rule_key VARCHAR(191) NOT NULL UNIQUE,
      rule_content LONGTEXT NOT NULL,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    ddl_modify = "ALTER TABLE platform_rules MODIFY COLUMN rule_content LONGTEXT NOT NULL"
    try:
        with engine.begin() as conn:
            conn.execute(text(ddl_create))
            conn.execute(text(ddl_modify))
        logger.info("%s MySQL 表结构校验完成", LOG_PREFIX)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s MySQL 表结构校验失败：%s", LOG_PREFIX, exc)
        raise RuntimeError(f"{LOG_PREFIX} MySQL 表结构校验失败：{exc}") from exc


def collect_rule_json_paths(input_dir: Path, report_path: Path) -> list[Path]:
    """
    收集待入库的规则 JSON 文件路径。

    合并抓取报告与目录扫描结果，确保历史遗漏文件也能被补写入库。
    """
    report_paths: list[Path] = []
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            results = report.get("results", []) if isinstance(report, dict) else []
            for row in results:
                if not isinstance(row, dict):
                    continue
                if row.get("status") != "ok":
                    continue
                output_json = str(row.get("output_json", "")).strip()
                if not output_json:
                    continue
                path = Path(output_json)
                if path.exists():
                    report_paths.append(path)
            logger.info("%s 从抓取报告收集到 %s 个规则文件", LOG_PREFIX, len(report_paths))
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 解析抓取报告失败，改用目录扫描：%s", LOG_PREFIX, exc)

    if not input_dir.exists():
        raise FileNotFoundError(f"规则目录不存在：{input_dir}")
    scanned_paths = [p for p in input_dir.rglob("*.json") if p.name != "_crawl_report.json"]
    logger.info("%s 目录扫描收集到 %s 个规则文件", LOG_PREFIX, len(scanned_paths))

    deduped: dict[str, Path] = {}
    for path in report_paths + scanned_paths:
        deduped[str(path.resolve())] = path
    merged = sorted(deduped.values(), key=lambda p: str(p))
    logger.info("%s 合并后待入库规则文件数：%s", LOG_PREFIX, len(merged))
    return merged


def load_rule_document(path: Path) -> dict[str, Any]:
    """读取并校验单个规则 JSON。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"读取 JSON 失败：{path}，原因：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON 根节点必须是对象：{path}")
    doc_id = str(payload.get("doc_id", "")).strip()
    source_url = str(payload.get("source_url", "")).strip()
    if not doc_id or not source_url:
        raise RuntimeError(f"缺少关键字段 doc_id/source_url：{path}")
    return payload


def make_rule_key(doc: dict[str, Any]) -> str:
    """按 doc_id 生成稳定唯一 key。"""
    return f"taobao_rule::{str(doc.get('doc_id', '')).strip()}"


def upsert_platform_rules(engine: Engine, documents: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    """
    执行 platform_rules upsert。

    返回：
        (insert_count, update_count)
    """
    insert_count = 0
    update_count = 0
    if dry_run:
        return (0, 0)

    with Session(bind=engine) as session:
        try:
            for doc in documents:
                rule_key = make_rule_key(doc)
                # 全量规则对象按 JSON 字符串存储，保留来源、类目、条款结构等完整信息。
                rule_content = json.dumps(doc, ensure_ascii=False)
                existing = session.execute(
                    select(PlatformRule).where(PlatformRule.rule_key == rule_key)
                ).scalar_one_or_none()
                if existing is None:
                    session.add(PlatformRule(rule_key=rule_key, rule_content=rule_content))
                    insert_count += 1
                else:
                    existing.rule_content = rule_content
                    update_count += 1
            session.commit()
            return (insert_count, update_count)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.error("%s 执行 upsert 失败：%s", LOG_PREFIX, exc)
            raise RuntimeError(f"{LOG_PREFIX} 执行 upsert 失败：{exc}") from exc


def main() -> int:
    """程序入口：收集规则 -> 校验 -> 入库。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    logger.info("%s 开始执行规则文件落库", LOG_PREFIX)
    logger.info("%s 输入目录：%s", LOG_PREFIX, args.input_dir)
    logger.info("%s 报告文件：%s", LOG_PREFIX, args.report_file)

    try:
        paths = collect_rule_json_paths(args.input_dir, args.report_file)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 收集规则文件失败：%s", LOG_PREFIX, exc)
        return 2

    if not paths:
        logger.error("%s 未找到可入库规则文件", LOG_PREFIX)
        return 2

    documents: list[dict[str, Any]] = []
    for path in paths:
        try:
            documents.append(load_rule_document(path))
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 跳过文件：%s", LOG_PREFIX, exc)

    if not documents:
        logger.error("%s 所有文件均校验失败，终止入库", LOG_PREFIX)
        return 2

    logger.info("%s 有效规则文件数：%s", LOG_PREFIX, len(documents))
    if args.dry_run:
        logger.info("%s dry-run 模式：未写入数据库", LOG_PREFIX)
        return 0

    try:
        engine = get_engine()
        ensure_mysql_platform_rules_schema(engine)
        insert_count, update_count = upsert_platform_rules(engine, documents, dry_run=False)
        logger.info("%s 入库完成：新增 %s，更新 %s", LOG_PREFIX, insert_count, update_count)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("%s 落库失败：%s", LOG_PREFIX, exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
