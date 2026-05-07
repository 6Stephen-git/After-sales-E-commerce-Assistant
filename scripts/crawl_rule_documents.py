"""
淘宝规则抓取脚本（仅落地文件，不写数据库）。

设计目标：
1) 从配置文件读取待抓取 URL；
2) 使用 Selenium 串行抓取（低频 + 随机延迟 + 重试）；
3) 仅提取规则正文条文，过滤导航/页脚噪声；
4) 按分类输出到 data/rules_raw 目录，供人工核对后再入库。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.webdriver import WebDriver


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS_PATH = ROOT_DIR / "data" / "rule_crawl_targets.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "rules_raw"

DATE_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?$")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+章")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇0-9]+条)\s*(.*)$")

# 触达这些词一般代表正文已结束（进入投票、页脚、友情链接等区域）
END_MARKERS = (
    "这篇文章是否易于理解",
    "规则协议",
    "平台服务协议",
    "新手上路",
    "关于淘宝",
    "阿里巴巴集团",
    "客服",
)


@dataclass
class CrawlResult:
    doc_id: str
    url: str
    category: str
    status: str
    message: str
    output_txt: str | None = None
    output_json: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取淘宝规则正文并按分类输出文件")
    parser.add_argument(
        "--targets",
        type=Path,
        default=DEFAULT_TARGETS_PATH,
        help="URL 配置文件路径（默认 data/rule_crawl_targets.json）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="输出目录（默认 data/rules_raw）",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="使用有头模式（便于手工登录后复用会话）",
    )
    parser.add_argument(
        "--user-data-dir",
        type=str,
        default="",
        help="浏览器用户数据目录（用于复用登录态，可选）",
    )
    parser.add_argument(
        "--profile-directory",
        type=str,
        default="",
        help="浏览器配置文件目录，如 Default（可选）",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=5.0,
        help="每个 URL 抓取前最小等待秒数（默认 5）",
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=10.0,
        help="每个 URL 抓取前最大等待秒数（默认 10）",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="单个 URL 失败重试次数（默认 2）",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="最多抓取多少条（0 表示不限制）",
    )
    return parser.parse_args()


def build_driver(headful: bool, user_data_dir: str, profile_directory: str) -> WebDriver:
    opts = EdgeOptions()
    if not headful:
        opts.add_argument("--headless=new")

    # 基础稳定性参数
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=zh-CN")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    if user_data_dir:
        opts.add_argument(f"--user-data-dir={user_data_dir}")
    if profile_directory:
        opts.add_argument(f"--profile-directory={profile_directory}")

    driver = webdriver.Edge(options=opts)
    driver.set_page_load_timeout(90)
    # 降低被自动化特征识别概率（基础处理）
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def load_targets(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"未找到配置文件：{path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("配置文件格式错误：根节点应为数组")

    targets: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        url = str(item.get("url", "")).strip()
        category = str(item.get("category", "")).strip()
        doc_id = str(item.get("doc_id", "")).strip()
        if not url or not category or not doc_id:
            continue
        targets.append(item)
    return targets


def normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 纯行号或明显占位符可过滤
        if line.isdigit():
            continue
        lines.append(line)
    return lines


def find_content_start(lines: list[str], doc_name: str) -> int:
    if doc_name:
        for idx, line in enumerate(lines):
            if doc_name in line:
                return idx
    for idx, line in enumerate(lines):
        if CHAPTER_RE.match(line) or ARTICLE_RE.match(line):
            return idx
    return 0


def find_content_end(lines: list[str], start_idx: int) -> int:
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if any(marker in line for marker in END_MARKERS):
            return idx
    return len(lines)


def extract_core_lines(raw_text: str, doc_name: str) -> list[str]:
    lines = normalize_lines(raw_text)
    if not lines:
        return []
    start = find_content_start(lines, doc_name)
    end = find_content_end(lines, start)
    core = lines[start:end]
    return core


def parse_articles(core_lines: list[str]) -> tuple[str, str | None, list[dict[str, Any]]]:
    """
    返回：
    - title：文档标题（尽量从正文前几行推断）
    - published_at：日期（若能识别）
    - articles：条款数组
    """
    if not core_lines:
        return "", None, []

    title = core_lines[0]
    published_at: str | None = None
    for line in core_lines[1:6]:
        if DATE_LINE_RE.match(line):
            published_at = line
            break

    articles: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_chapter = ""

    for line in core_lines:
        if CHAPTER_RE.match(line):
            current_chapter = line
            continue

        match = ARTICLE_RE.match(line)
        if match:
            if current:
                current["content"] = "\n".join(current["content_lines"]).strip()
                current.pop("content_lines", None)
                articles.append(current)

            article_no = match.group(1).strip()
            tail = match.group(2).strip()
            article_title = tail.strip("【】[] ").strip()
            current = {
                "chapter": current_chapter,
                "article_no": article_no,
                "article_title": article_title,
                "raw_header": line,
                "content_lines": [],
            }
            continue

        if current:
            current["content_lines"].append(line)

    if current:
        current["content"] = "\n".join(current["content_lines"]).strip()
        current.pop("content_lines", None)
        articles.append(current)

    return title, published_at, articles


def safe_path_component(value: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", value).strip()
    return sanitized or "unknown"


def category_to_dir(base_dir: Path, category: str) -> Path:
    # 支持 "交易管理/物流规范" 这种层级写法
    parts = [safe_path_component(p) for p in category.split("/") if p.strip()]
    out = base_dir
    for part in parts:
        out = out / part
    return out


def slow_scroll(driver: WebDriver) -> None:
    # 轻量滚动触发懒加载，不做高频操作
    for y in (200, 700, 1200, 1800):
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(random.uniform(0.8, 1.8))


def safe_driver_get(driver: WebDriver, url: str, label: str, max_retries: int = 4) -> bool:
    """带重试打开页面；连续失败时返回 False（中文日志便于本机排查网络/风控）。"""
    for attempt in range(1, max_retries + 1):
        try:
            driver.get(url)
            return True
        except WebDriverException as exc:
            backoff = (2**attempt) + random.uniform(1.0, 3.0)
            print(
                f"[WARN] {label} 打开失败（{attempt}/{max_retries}），"
                f"{backoff:.1f}s 后重试：{exc}",
            )
            time.sleep(backoff)
    print(f"[ERROR] {label} 多次重试仍无法打开：{url}")
    return False


def fetch_body_text(driver: WebDriver, url: str) -> str:
    if not safe_driver_get(driver, url, "规则详情页"):
        raise RuntimeError(
            "无法打开规则详情页（常见原因：本机网络不可达 rule.taobao.com、"
            "或触发 net::ERR_CONNECTION_CLOSED）。请在可正常打开该站的网络下重试，"
            "必要时使用 --headful 并配置 Edge 用户数据目录。",
        )
    time.sleep(random.uniform(5.5, 9.5))
    slow_scroll(driver)
    time.sleep(random.uniform(1.5, 3.5))
    body = driver.find_element("tag name", "body")
    return body.text or ""


def write_outputs(
    output_dir: Path,
    target: dict[str, Any],
    core_lines: list[str],
    title: str,
    published_at: str | None,
    articles: list[dict[str, Any]],
) -> tuple[Path, Path]:
    category = str(target["category"])
    doc_id = safe_path_component(str(target["doc_id"]))
    doc_name = str(target.get("doc_name", "")).strip()
    source_url = str(target["url"])
    fetched_at = datetime.now().isoformat(timespec="seconds")

    cat_dir = category_to_dir(output_dir, category)
    cat_dir.mkdir(parents=True, exist_ok=True)

    txt_path = cat_dir / f"{doc_id}.txt"
    json_path = cat_dir / f"{doc_id}.json"

    txt_path.write_text("\n".join(core_lines).strip() + "\n", encoding="utf-8")
    payload = {
        "doc_id": doc_id,
        "doc_name": doc_name or title,
        "category": category,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "title": title,
        "published_at": published_at,
        "article_count": len(articles),
        "articles": articles,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return txt_path, json_path


def crawl_one(driver: WebDriver, output_dir: Path, target: dict[str, Any], max_retries: int) -> CrawlResult:
    doc_id = str(target["doc_id"])
    url = str(target["url"])
    category = str(target["category"])
    doc_name = str(target.get("doc_name", "")).strip()

    for attempt in range(1, max_retries + 2):
        try:
            body_text = fetch_body_text(driver, url)
            core_lines = extract_core_lines(body_text, doc_name)
            if not core_lines:
                raise RuntimeError("未提取到正文条文，请检查 URL 或页面结构")

            title, published_at, articles = parse_articles(core_lines)
            txt_path, json_path = write_outputs(
                output_dir=output_dir,
                target=target,
                core_lines=core_lines,
                title=title,
                published_at=published_at,
                articles=articles,
            )
            return CrawlResult(
                doc_id=doc_id,
                url=url,
                category=category,
                status="ok",
                message=f"抓取成功，条款数：{len(articles)}",
                output_txt=str(txt_path),
                output_json=str(json_path),
            )
        except Exception as exc:  # noqa: BLE001
            if attempt > max_retries:
                return CrawlResult(
                    doc_id=doc_id,
                    url=url,
                    category=category,
                    status="error",
                    message=f"抓取失败（已重试 {max_retries} 次）：{type(exc).__name__}: {exc}",
                )
            # 指数退避 + 抖动
            backoff = (2 ** attempt) + random.uniform(1.0, 3.0)
            print(f"[WARN] {doc_id} 第 {attempt} 次失败，{backoff:.1f}s 后重试：{exc}")
            time.sleep(backoff)

    return CrawlResult(doc_id=doc_id, url=url, category=category, status="error", message="未知错误")


def main() -> int:
    args = parse_args()
    if args.min_delay <= 0 or args.max_delay <= 0 or args.min_delay > args.max_delay:
        print("[ERROR] 延迟参数非法：请确保 0 < min_delay <= max_delay")
        return 2

    try:
        targets = load_targets(args.targets)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 读取 targets 失败：{exc}")
        return 2

    if not targets:
        print("[INFO] 未找到可抓取目标（请检查 enabled/category/doc_id/url）")
        return 0

    if args.max_items > 0:
        targets = targets[: args.max_items]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "_crawl_report.json"

    print(f"[INFO] 待抓取目标数：{len(targets)}")
    print(f"[INFO] 输出目录：{args.output_dir}")

    driver = build_driver(
        headful=args.headful,
        user_data_dir=args.user_data_dir,
        profile_directory=args.profile_directory,
    )
    results: list[CrawlResult] = []
    try:
        for idx, target in enumerate(targets, start=1):
            delay = random.uniform(args.min_delay, args.max_delay)
            print(f"\n[INFO] ({idx}/{len(targets)}) 即将抓取 {target['doc_id']}，等待 {delay:.1f}s")
            time.sleep(delay)
            result = crawl_one(
                driver=driver,
                output_dir=args.output_dir,
                target=target,
                max_retries=args.max_retries,
            )
            print(f"[{result.status.upper()}] {result.doc_id} - {result.message}")
            results.append(result)
    finally:
        driver.quit()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "targets_file": str(args.targets),
        "output_dir": str(args.output_dir),
        "ok_count": sum(1 for r in results if r.status == "ok"),
        "error_count": sum(1 for r in results if r.status != "ok"),
        "results": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INFO] 报告已写入：{report_path}")

    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
