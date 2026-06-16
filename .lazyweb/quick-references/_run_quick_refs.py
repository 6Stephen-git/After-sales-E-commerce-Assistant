# -*- coding: utf-8 -*-
"""Lazyweb quick-references：电商售后 AI 辅助客服关键 UI 组件参考检索与 HTML 报告生成。"""

import html
import json
import os
import pathlib
import re
import urllib.request
from collections import defaultdict

DATE = "2026-06-15"
TOPIC = "aftersales-cs-ui-components"
BASE = pathlib.Path(__file__).resolve().parent / f"{TOPIC}-{DATE}"
REPORT_PATH = BASE / "report.html"

# ---------- Lazyweb MCP HTTP 调用 ----------
def call_lazyweb_tool(name: str, arguments: dict) -> dict:
    token = open(os.path.expanduser("~/.lazyweb/lazyweb_mcp_token"), encoding="utf-8").read().strip()
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://www.lazyweb.com/mcp",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if "result" in obj:
                    return obj["result"]
            except json.JSONDecodeError:
                continue
    return json.loads(raw)


def parse_results(res: dict) -> list:
    text = res.get("content", [{}])[0].get("text", "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data.get("results", [])


# ---------- 多组检索：覆盖话术/预警/上下文/侧栏等组件 ----------
SEARCHES = [
    {"query": "suggested reply macros", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "quick reply templates chat", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "inline alert banner warning", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "customer profile sidebar context", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "AI copilot suggestions panel", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "support inbox conversation detail", "platform": "desktop", "limit": 25, "skill": "quick-references"},
    {"query": "ticket details panel", "company": "Zendesk", "limit": 15, "skill": "quick-references"},
    {"query": "inbox conversation", "company": "Intercom", "limit": 15, "skill": "quick-references"},
]

PATTERNS = {
    "reply_chips": {
        "title": "话术 / 快捷回复 Chips",
        "verdict": "Build this",
        "tag": "strong",
        "keys": ["macro", "quick reply", "canned", "template", "suggested", "snippet", "shortcut"],
        "claim": "AI 建议话术以可点击 chips/按钮呈现，商家一键填入输入框。",
        "refs": [],
    },
    "inline_alert": {
        "title": "非阻断式 inline 预警",
        "verdict": "Build this",
        "tag": "strong",
        "keys": ["alert", "banner", "warning", "notification bar", "inline", "toast"],
        "claim": "情绪/风险预警用顶栏或对话区上方 alert，不用模态弹窗。",
        "refs": [],
    },
    "context_rail": {
        "title": "案件 / 订单上下文轨",
        "verdict": "Build this",
        "tag": "directional",
        "keys": ["sidebar", "customer profile", "order", "ticket detail", "context", "properties panel"],
        "claim": "订单号、买家信息、纠纷标签固定在侧栏或顶栏，分析时只更新状态。",
        "refs": [],
    },
    "copilot_panel": {
        "title": "AI Copilot 策略侧栏",
        "verdict": "Build this",
        "tag": "strong",
        "keys": ["copilot", "assist", "ai suggest", "recommendation", "smart reply", "agent assist"],
        "claim": "右侧策略区分块：摘要、建议、依据；与左侧对话并列。",
        "refs": [],
    },
    "inbox_chat": {
        "title": "会话主舞台（列表 + 对话）",
        "verdict": "Optional",
        "tag": "directional",
        "keys": ["inbox", "conversation", "message thread", "chat window", "support chat"],
        "claim": "多会话场景可加左侧列表；单纠纷场景可省略，仅保留对话区。",
        "refs": [],
    },
}


def score_item(r: dict, pattern_keys: list) -> int:
    vd = (r.get("visionDescription") or r.get("vision_description") or "").lower()
    return sum(1 for k in pattern_keys if k in vd)


def assign_patterns(all_items: list) -> None:
    seen = set()
    for r in all_items:
        uid = r.get("id") or (r.get("company"), r.get("imageUrl") or r.get("image_url"))
        if uid in seen:
            continue
        vd = r.get("visionDescription") or r.get("vision_description") or ""
        if not vd:
            continue
        best_pid, best_score = None, 0
        for pid, pat in PATTERNS.items():
            if len(pat["refs"]) >= 4:
                continue
            sc = score_item(r, pat["keys"])
            if sc > best_score:
                best_score, best_pid = sc, pid
        if best_pid and best_score > 0:
            seen.add(uid)
            PATTERNS[best_pid]["refs"].append(
                {
                    "company": r.get("company") or r.get("companyName") or "Unknown",
                    "platform": r.get("platform", "desktop"),
                    "imageUrl": r.get("imageUrl") or r.get("image_url", ""),
                    "visionDescription": vd,
                    "similarity": r.get("similarity", 0),
                    "matchCount": r.get("matchCount", 0),
                }
            )


def deck_html(refs: list, web: bool = True) -> str:
    if not refs:
        return "<p class=\"deck-hint\">本模式暂无强匹配截图。</p>"
    figures = []
    for ref in refs[:4]:
        shot = " shot-web" if ref.get("platform") == "desktop" else ""
        cap = html.escape(ref["visionDescription"][:160])
        company = html.escape(ref["company"])
        img = html.escape(ref["imageUrl"])
        figures.append(
            f'<figure class="{shot.strip()}"><img src="{img}" alt="{company}" loading="lazy" '
            f'onerror="this.closest(\'figure\').classList.add(\'img-missing\')">'
            f'<figcaption class="cap"><span class="src">[Lazyweb]</span> <b>{company}</b> &mdash; {cap}</figcaption></figure>'
        )
    deck_cls = "deck web" if web else "deck"
    return (
        f'<div class="deckwrap"><div class="{deck_cls}">{"".join(figures)}</div>'
        '<div class="deck-nav">'
        '<button type="button" aria-label="Previous" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:-((f?f.offsetWidth:300)+12),behavior:\'smooth\'})">&#9664;</button>'
        '<button type="button" aria-label="Next" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:(f?f.offsetWidth:300)+12,behavior:\'smooth\'})">&#9654;</button>'
        "</div></div>"
    )


# ---------- 生成 HTML 报告 ----------
def build_report(total_screened: int) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    abs_path = str(REPORT_PATH).replace("\\", "/")

  # 按 Build this 优先排序
    ranked = sorted(
        PATTERNS.items(),
        key=lambda x: (0 if x[1]["verdict"] == "Build this" else 1, -len(x[1]["refs"])),
    )
    lead = ranked[0] if ranked else None

    pat_blocks = ""
    for pid, pat in ranked:
        n = len(pat["refs"])
        prev = f"seen in {n} refs" if n else "0 matches"
        tag_cls = pat["tag"]
        pat_blocks += f"""
<div class="pat">
  <div class="pat-h"><h3>{html.escape(pat['title'])}</h3>
    <span class="tag {tag_cls}">{html.escape(pat['verdict'])}</span>
    <span class="prev">{prev}</span>
    <span class="ebadge directional">Directional</span>
  </div>
  <p class="pat-claim">{html.escape(pat['claim'])}</p>
  {deck_html(pat['refs'])}
</div>"""

    lead_name = lead[1]["title"] if lead else "双栏 Copilot"
    lead_ref = lead[1]["refs"][0] if lead and lead[1]["refs"] else None
    lead_img = html.escape(lead_ref["imageUrl"]) if lead_ref else ""
    lead_co = html.escape(lead_ref["company"]) if lead_ref else ""

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quick References: 电商售后 AI 辅助客服 UI 组件</title>
<style>
:root{{--ink:#1f2328;--mut:#57606a;--line:#d0d7de;--soft:#eef4fb;--accent:#0969da;--do-fg:#0a5d2a;--do-bg:#e6f4ea;--do-bd:#b7e0c4;--ex-fg:#8a5a00;--ex-bg:#fff8e6;--ex-bd:#f0e0b0}}
body{{font-family:system-ui,sans-serif;color:var(--ink);line-height:1.55;margin:0;padding:24px 20px 48px;background:#fafbfc}}
main{{max-width:900px;margin:0 auto}}
h1{{font-size:1.55rem;margin:0 0 6px}}.sub{{color:var(--mut);font-size:14px;margin:0 0 16px}}
.agent-instructions{{background:var(--soft);border-left:4px solid var(--accent);border-radius:8px;padding:14px 16px;margin:18px 0}}
.ai-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.ai-badge{{font-size:11px;font-weight:700;color:#0a3b78}}
.ai-copy{{font:600 12px/1 inherit;cursor:pointer;border:1px solid var(--accent);color:var(--accent);background:#fff;border-radius:6px;padding:5px 11px}}
.ai-human{{margin:0 0 10px;font-size:15px}}
.ai-block{{white-space:pre-wrap;background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px;font:13px/1.5 ui-monospace,Menlo,monospace}}
.corpus{{font-size:13px;color:#8a5a00;background:#fff8e6;border:1px solid #f0e0b0;border-radius:8px;padding:9px 12px;margin:14px 0}}
.legend{{border:1px solid var(--line);background:#fff;border-radius:12px;padding:6px;margin:0 0 20px}}
.legend-row{{display:grid;grid-template-columns:34px 1fr auto;gap:10px;padding:9px 10px;text-decoration:none;color:inherit;align-items:center}}
.legend-row+.legend-row{{border-top:1px solid #eef1f4}}
.legend-row.is-lead{{background:linear-gradient(0deg,#fff,var(--soft))}}
.lg-rank{{width:30px;height:30px;border-radius:50%;background:var(--accent);color:#fff;font:800 14px/30px inherit;text-align:center}}
.lg-name{{font-weight:650}}.lg-why{{display:block;font-weight:400;color:var(--mut);font-size:12px}}
.verdict{{font:700 10px/1 inherit;border-radius:6px;padding:5px 9px}}.verdict.do{{color:var(--do-fg);background:var(--do-bg);border:1px solid var(--do-bd)}}
.recs .rec{{display:grid;grid-template-columns:1.1fr 1fr;border:2px solid var(--accent);border-radius:14px;overflow:hidden;background:#fff;margin:16px 0;box-shadow:0 6px 22px rgba(9,105,218,.12)}}
.rec-proof img{{width:100%;aspect-ratio:16/10;object-fit:cover;object-position:top;display:block}}
.rec-body{{padding:16px}}.rec-body h3{{margin:0 0 8px}}
.deckwrap{{margin:8px 0}}.deck{{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 2px 10px}}
.deck>figure{{flex:0 0 86%;max-width:320px;scroll-snap-align:center;margin:0;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}}
@media(min-width:620px){{.deck>figure{{flex-basis:46%}}.deck.web>figure,.deck>figure.shot-web{{flex-basis:60%;max-width:560px}}}}
.deck>figure>img{{display:block;width:100%;height:auto;max-height:620px;object-fit:contain}}
.deck>figure.shot-web>img{{aspect-ratio:16/10;object-fit:cover;object-position:top;max-height:none}}
.deck .cap{{font-size:12px;color:var(--mut);padding:7px 9px;line-height:1.4}}.deck .src{{font-weight:700;color:var(--accent);font-size:10.5px}}
.deck-nav{{display:flex;gap:6px;justify-content:flex-end}}.deck-nav button{{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:6px;width:34px;height:28px}}
.pat{{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:14px 0;background:#fff}}
.pat-h{{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:4px}}.pat-h h3{{margin:0;font-size:16px}}
.pat-claim{{color:var(--mut);font-size:14px;margin:2px 0 10px}}
.prev{{font:600 11.5px/1 inherit;color:#0a3b78;background:var(--soft);border-radius:20px;padding:4px 10px}}
.tag{{font:700 10.5px/1 inherit;border-radius:5px;padding:3px 7px}}
.tag.strong{{color:#0a5d2a;background:#e6f4ea;border:1px solid #b7e0c4}}
.tag.directional{{color:#8a5a00;background:#fff8e6;border:1px solid #f0e0b0}}
.ebadge{{font:700 10px/1 inherit;border-radius:20px;padding:4px 9px;color:var(--ex-fg);background:var(--ex-bg);border:1px solid var(--ex-bd)}}
.mock{{margin:14px 0}}.mock .frame{{max-width:760px;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}}
.mock .bar{{display:flex;gap:6px;padding:7px 10px;background:#f6f8fa;border-bottom:1px solid var(--line)}}
.mock .dot{{width:9px;height:9px;border-radius:50%;background:#d0d7de}}
.mock .url{{flex:1;text-align:center;font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:5px;padding:2px 8px}}
.mock .body{{padding:14px;display:flex;flex-direction:column;gap:10px}}
.mock .row{{display:flex;gap:10px}}.mock .box{{flex:1;background:var(--soft);border:1px dashed #b9c7d6;border-radius:8px;min-height:36px;display:flex;align-items:center;justify-content:center;font-size:12px;color:#4a5a6a;padding:8px;text-align:center}}
.mock .box.tall{{min-height:100px}}.mock .box.cta{{background:var(--accent);color:#fff;border:0;font-weight:600}}
.mock .cap{{font-size:12px;color:var(--mut);text-align:center;margin-top:6px}}
.lw-foot{{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);text-align:center;font-size:13px;color:var(--mut)}}
@media(max-width:720px){{.recs .rec{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<main>
<h1>Quick References: 电商售后 AI 辅助客服 UI 组件</h1>
<p class="sub">桌面 Web · Vue 售后辅助台 · {len(SEARCHES)} 组 Lazyweb 检索 · {total_screened} 屏已浏览</p>

<section class="agent-instructions">
  <div class="ai-head"><span class="ai-badge">FOR THE CODING AGENT</span>
    <button class="ai-copy" type="button" onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('.ai-block').innerText)">Copy</button>
  </div>
  <p class="ai-human">实现时优先做「话术 chips + inline 预警 + 右侧 Copilot 卡片」，再补案件上下文轨。</p>
  <pre class="ai-block">LAZYWEB REPORT — AGENT HANDOFF
Use the report at {abs_path} as a starting point for building 电商售后 AI 辅助客服关键 UI 组件 using these grouped real-app references as a visual baseline.

TOP RECOMMENDATIONS (do first):
1. StrategyPanel 话术改为可点击 chips，点击填入 ChatPanel 输入框。
2. 情绪/风险用 el-alert 非阻断展示（DisputeView 已有 error/progress alert，扩展为风险标签）。
3. 右侧策略区分「案件摘要 / AI 建议 / 话术选项」三层卡片。

INDEX ON: 快捷回复 chips、inline alert、Copilot 侧栏分块、订单上下文侧栏
DO NOT OVER-INDEX ON: 多会话 inbox 三栏（单纠纷场景可省略列表栏）
DIVE FURTHER: /lazyweb-design-improve — 用 DisputeView 截图对比最接近的参考

Evidence basis: Lazyweb screenshots · {DATE}</pre>
</section>

<div class="corpus"><b>证据说明：</b> {total_screened} 屏已检索分组，结论为方向性；无 A/B 转化数据。</div>

<h2>推荐路径</h2>
<nav class="legend">
  <a class="legend-row is-lead" href="#p1"><span class="lg-rank">1</span><span class="lg-name">{html.escape(lead_name)}<span class="lg-why">与 StrategyPanel 话术区直接对应</span></span><span class="verdict do">Do first</span></a>
  <a class="legend-row" href="#p2"><span class="lg-rank" style="background:#3f6896">2</span><span class="lg-name">非阻断式 inline 预警<span class="lg-why">对齐 Frontend_spec 硬约束</span></span><span class="verdict do">Do</span></a>
  <a class="legend-row" href="#p3"><span class="lg-rank" style="background:#6b7787">3</span><span class="lg-name">AI Copilot 策略侧栏<span class="lg-why">右栏卡片化</span></span><span class="verdict do">Do</span></a>
</nav>

<div class="recs">
  <article class="rec" id="p1">
    <div class="rec-proof"><img src="{lead_img}" alt="lead" loading="lazy"></div>
    <div class="rec-body">
      <h3>首选：{html.escape(lead_name)}</h3>
      <p>参考 <b>{lead_co}</b> 等产品的快捷回复/建议区布局，把 AI 输出拆成可点选短句。</p>
    </div>
  </article>
</div>

<figure class="mock desktop"><div class="frame"><div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="url">组件落位示意</span></div><div class="body">
  <div class="row"><div class="box">顶栏：订单 · 纠纷类型 · 风险 alert</div></div>
  <div class="row"><div class="box tall">左：对话流</div><div class="box tall">右：摘要卡 / 策略卡 / 话术 chips</div></div>
  <div class="row"><div class="box">输入 + 附件</div><div class="box cta">商家发送</div></div>
</div></div><figcaption class="cap">Mock-frame — 组件在 DisputeView 中的落位</figcaption></figure>

<h2>按模式分组</h2>
{pat_blocks}

<footer class="lw-foot">Powered by <a href="https://www.lazyweb.com">Lazyweb</a> — turn your agent into a design researcher… for free!</footer>
</main>
</body>
</html>"""

    REPORT_PATH.write_text(doc, encoding="utf-8")
    print("WROTE", REPORT_PATH)


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    all_items = []
    seen = set()
    for q in SEARCHES:
        print("SEARCH", q.get("query"), q.get("company", ""))
        try:
            res = call_lazyweb_tool("lazyweb_search", q)
            for r in parse_results(res):
                uid = r.get("id") or (r.get("company"), r.get("imageUrl"))
                if uid not in seen:
                    seen.add(uid)
                    all_items.append(r)
        except Exception as exc:
            print("ERR", exc)
    assign_patterns(all_items)
    (BASE / "raw_count.txt").write_text(str(len(all_items)), encoding="utf-8")
    build_report(len(all_items))


if __name__ == "__main__":
    main()
