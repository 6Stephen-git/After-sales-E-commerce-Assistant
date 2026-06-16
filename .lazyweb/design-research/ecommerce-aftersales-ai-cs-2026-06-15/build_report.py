# -*- coding: utf-8 -*-
"""从 Lazyweb 检索结果生成电商售后 AI 辅助客服界面研究报告 HTML。"""

import html
import json
import pathlib
from collections import Counter

BASE = pathlib.Path(__file__).resolve().parent
REPORT_PATH = BASE / "report.html"
SEARCH_PATH = BASE / "search_results.json"
FILTERED_PATH = BASE / "filtered.json"

# ---------- 解析 Lazyweb 原始检索 JSON ----------
def load_search_items():
    raw = json.loads(SEARCH_PATH.read_text(encoding="utf-8"))
    items = []
    seen = set()
    for key, res in raw.items():
        if "error" in res:
            continue
        text = res.get("content", [{}])[0].get("text", "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for r in data.get("results", []):
            uid = r.get("id") or r.get("screenshotId") or (
                r.get("company"),
                r.get("imageUrl") or r.get("image_url"),
            )
            if uid in seen:
                continue
            seen.add(uid)
            vd = (r.get("visionDescription") or r.get("vision_description") or "")
            blob = (vd + " " + key).lower()
            score = 0
            keywords = [
                "chat",
                "support",
                "customer",
                "inbox",
                "conversation",
                "agent",
                "ticket",
                "help",
                "message",
                "copilot",
                "assist",
                "reply",
                "service",
                "sidebar",
            ]
            for kw in keywords:
                if kw in blob:
                    score += 1
            if any(
                k in vd.lower()
                for k in [
                    "chat",
                    "support",
                    "inbox",
                    "conversation",
                    "ticket",
                    "customer service",
                    "help desk",
                    "agent",
                ]
            ):
                score += 3
            if score < 4:
                continue
            items.append(
                {
                    "company": r.get("company") or r.get("companyName") or "Unknown",
                    "platform": r.get("platform", ""),
                    "imageUrl": r.get("imageUrl") or r.get("image_url", ""),
                    "visionDescription": vd,
                    "similarity": r.get("similarity", 0),
                    "matchCount": r.get("matchCount", 0),
                    "pageUrl": r.get("pageUrl", ""),
                    "query": key,
                    "score": score,
                }
            )
    items.sort(
        key=lambda x: (x["score"], x.get("matchCount", 0), x.get("similarity", 0)),
        reverse=True,
    )
    return items


# ---------- 生成 deck 轮播 HTML ----------
def deck_figures(refs, web_class=""):
    parts = []
    for ref in refs:
        cap = html.escape(ref.get("caption", ref.get("visionDescription", "")))
        company = html.escape(ref.get("company", ""))
        src_label = html.escape(ref.get("source", "[Lazyweb]"))
        img = html.escape(ref.get("imageUrl", ""))
        shot = " shot-web" if ref.get("platform") == "desktop" else ""
        parts.append(
            f'<figure class="{shot}"><img src="{img}" alt="{company}" loading="lazy" '
            f'onerror="this.closest(\'figure\').classList.add(\'img-missing\')">'
            f'<figcaption class="cap"><span class="src">{src_label}</span> <b>{company}</b> &mdash; {cap}</figcaption></figure>'
        )
    cls = f"deck {web_class}".strip()
    return (
        f'<div class="deckwrap"><div class="{cls}">{"".join(parts)}</div>'
        '<div class="deck-nav">'
        '<button type="button" aria-label="Previous" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:-((f?f.offsetWidth:300)+12),behavior:\'smooth\'})">&#9664;</button>'
        '<button type="button" aria-label="Next" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:(f?f.offsetWidth:300)+12,behavior:\'smooth\'})">&#9654;</button>'
        "</div></div>"
    )


# ---------- 写入完整 HTML 报告 ----------
def build_report(items):
  top = items[:12]
  FILTERED_PATH.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")

  # 模式归类（基于 vision 描述关键词）
  patterns = {
    "split_inbox_detail": {
      "name": "三栏：会话列表 + 对话 + 辅助侧栏",
      "keys": ["inbox", "sidebar", "conversation list", "three"],
      "refs": [],
    },
    "ai_suggestions_panel": {
      "name": "AI 建议/话术面板紧邻对话区",
      "keys": ["suggest", "copilot", "assist", "ai", "reply", "macro"],
      "refs": [],
    },
    "context_rail": {
      "name": "订单/客户上下文固定在右侧或顶部",
      "keys": ["order", "customer", "profile", "context", "ticket details"],
      "refs": [],
    },
    "composer_actions": {
      "name": "输入区集成快捷动作（模板/图片/转人工）",
      "keys": ["compose", "input", "attachment", "template", "send"],
      "refs": [],
    },
  }

  for it in items:
    vd = it["visionDescription"].lower()
    for pid, pat in patterns.items():
      if len(pat["refs"]) >= 3:
        continue
      if any(k in vd for k in pat["keys"]):
        pat["refs"].append(
          {
            **it,
            "caption": it["visionDescription"][:180],
            "source": "[Lazyweb]",
          }
        )

  total_refs = len(items)
  abs_report = str(REPORT_PATH).replace("\\", "/")

  rec1_refs = [r for r in items if "inbox" in r["visionDescription"].lower() or "conversation" in r["visionDescription"].lower()][:2]
  rec2_refs = [r for r in items if any(k in r["visionDescription"].lower() for k in ["suggest", "copilot", "assist", "ai"])][:2]
  rec3_refs = [r for r in items if any(k in r["visionDescription"].lower() for k in ["order", "customer", "profile", "context"])][:2]
  if not rec1_refs:
    rec1_refs = top[:2]
  if not rec2_refs:
    rec2_refs = top[2:4]
  if not rec3_refs:
    rec3_refs = top[4:6]

  for group in (rec1_refs, rec2_refs, rec3_refs):
    for r in group:
      r.setdefault("caption", r["visionDescription"][:180])
      r.setdefault("source", "[Lazyweb]")

  key_examples = []
  for it in top[:8]:
    key_examples.append({**it, "caption": it["visionDescription"][:200], "source": "[Lazyweb]"})

  companies = Counter(it["company"] for it in items)

  html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design Research: 电商售后 AI 辅助客服界面</title>
<style>
:root{{--ink:#1f2328;--mut:#57606a;--line:#d0d7de;--soft:#eef4fb;--accent:#0969da;--do-fg:#0a5d2a;--do-bg:#e6f4ea;--do-bd:#b7e0c4;--ex-fg:#8a5a00;--ex-bg:#fff8e6;--ex-bd:#f0e0b0;--sk-fg:#6e7781;--sk-bg:#f6f8fa;--sk-bd:#e3e7eb;--single-fg:#a40e26;--single-bg:#fdeef0;--single-bd:#f5c2c7}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);line-height:1.55;margin:0;padding:24px 20px 48px;background:#fafbfc}}
main{{max-width:900px;margin:0 auto}}
h1{{font-size:1.65rem;margin:0 0 6px}}
.sub{{color:var(--mut);margin:0 0 18px;font-size:14px}}
.agent-instructions{{background:var(--soft);border-left:4px solid var(--accent);border-radius:8px;padding:14px 16px;margin:18px 0}}
.ai-head{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}}
.ai-badge{{font-size:11px;font-weight:700;letter-spacing:.04em;color:#0a3b78}}
.ai-copy{{font:600 12px/1 inherit;cursor:pointer;border:1px solid var(--accent);color:var(--accent);background:#fff;border-radius:6px;padding:5px 11px}}
.ai-human{{margin:0 0 10px;font-size:15px}}
.ai-block{{white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px 13px;margin:0;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace}}
.corpus{{display:flex;gap:8px;align-items:flex-start;font-size:13px;color:#8a5a00;background:#fff8e6;border:1px solid #f0e0b0;border-radius:8px;padding:9px 12px;margin:14px 0}}
.deckwrap{{margin:8px 0}}.deck{{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 2px 10px}}.deck>figure{{flex:0 0 86%;max-width:320px;scroll-snap-align:center;margin:0;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}}@media(min-width:620px){{.deck>figure{{flex-basis:46%}}}}.deck>figure.shot-web{{flex-basis:92%;max-width:560px}}.deck>figure>img{{display:block;width:100%;height:auto;max-height:620px;object-fit:contain;background:#fafbfc}}.deck>figure.shot-web>img{{aspect-ratio:16/10;max-height:none;object-fit:cover;object-position:top}}.deck .cap{{font-size:12px;color:var(--mut);padding:7px 9px;line-height:1.4}}.deck .cap b{{color:var(--ink)}}.deck .src{{font-size:10.5px;font-weight:700;color:var(--accent)}}.deck-nav{{display:flex;gap:6px;justify-content:flex-end;margin-top:2px}}.deck-nav button{{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:6px;width:34px;height:28px}}
.pat{{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:14px 0;background:#fff}}.pat-h{{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:4px}}.pat-h h3{{margin:0;font-size:16px}}.pat-claim{{color:var(--mut);font-size:14px;margin:2px 0 10px}}.prev{{font:600 11.5px/1 inherit;color:#0a3b78;background:var(--soft);border-radius:20px;padding:4px 10px}}.tag{{font:700 10.5px/1 inherit;border-radius:5px;padding:3px 7px}}.tag.strong{{color:#0a5d2a;background:#e6f4ea;border:1px solid #b7e0c4}}.tag.directional{{color:#8a5a00;background:#fff8e6;border:1px solid #f0e0b0}}.tag.avoid{{color:#a40e26;background:#fdeef0;border:1px solid #f5c2c7}}
.legend{{border:1px solid var(--line);background:#fff;border-radius:12px;padding:6px;margin:0 0 26px}}.legend-row{{display:grid;grid-template-columns:34px minmax(0,1fr) auto auto;align-items:center;gap:12px;padding:9px 10px;text-decoration:none;color:inherit}}.legend-row+.legend-row{{border-top:1px solid #eef1f4}}.legend-row.is-lead{{background:linear-gradient(0deg,#fff,var(--soft))}}.lg-rank{{font:800 14px/1 inherit;text-align:center;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;background:var(--mut)}}.legend-row.is-lead .lg-rank{{background:var(--accent)}}.lg-name{{font-weight:650}}.lg-name .lg-why{{display:block;font-weight:400;color:var(--mut);font-size:12px}}.verdict{{font:700 10.5px/1 inherit;border-radius:6px;padding:5px 9px}}.verdict.do{{color:var(--do-fg);background:var(--do-bg);border:1px solid var(--do-bd)}}.verdict.explore{{color:var(--ex-fg);background:var(--ex-bg);border:1px solid var(--ex-bd)}}.lg-ev{{font:700 10px/1 inherit;color:var(--mut)}}
.recs{{display:flex;flex-direction:column;gap:16px}}.rec{{display:grid;grid-template-columns:minmax(0,1.18fr) minmax(0,1fr);background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}}.rec.lead{{border:2px solid var(--accent);box-shadow:0 6px 22px rgba(9,105,218,.14);background:linear-gradient(0deg,#fff,var(--soft));margin-top:11px;position:relative}}.rec.lead::before{{content:'★ 推荐路径 — 从这里开始';position:absolute;z-index:4;top:-11px;left:18px;background:var(--accent);color:#fff;font:700 10px/1 inherit;border-radius:10px;padding:6px 11px}}.rec-proof{{background:#0d1117;position:relative}}.rec-proof img{{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;object-position:top}}.browserbar{{display:flex;align-items:center;gap:6px;height:30px;padding:0 11px;background:#f6f8fa;border-bottom:1px solid var(--line);position:absolute;top:0;left:0;right:0;z-index:2}}.browserbar i{{width:9px;height:9px;border-radius:50%;display:block}}.browserbar i:nth-child(1){{background:#ff5f57}}.browserbar i:nth-child(2){{background:#febc2e}}.browserbar i:nth-child(3){{background:#28c840}}.browserbar .url{{margin-left:8px;font:600 10.5px/1 ui-monospace,Menlo,monospace;color:var(--mut);background:#fff;border:1px solid var(--line);border-radius:20px;padding:5px 12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.rank-badge{{position:absolute;z-index:3;left:12px;bottom:12px;display:flex;align-items:center;gap:7px;background:rgba(13,17,23,.84);color:#fff;border-radius:999px;padding:5px 12px 5px 7px}}.rank-badge .num{{width:24px;height:24px;border-radius:50%;background:#fff;color:#0d1117;font:800 13px/1 inherit;display:flex;align-items:center;justify-content:center}}.rec.lead .rank-badge .num{{background:var(--accent);color:#fff}}.proof-src{{position:absolute;z-index:3;right:12px;bottom:12px;background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font:700 10px/1.3 inherit}}.rec-body{{padding:16px 18px}}.rec-body h3{{margin:0 0 5px;font-size:17px}}.rec-what{{margin:0 0 12px;color:var(--mut);font-size:13.5px}}.ebadge{{font:700 10px/1 inherit;border-radius:20px;padding:5px 9px;display:inline-flex}}.ebadge.directional{{color:var(--ex-fg);background:var(--ex-bg);border:1px solid var(--ex-bd)}}.ev-note{{font-size:12px;color:var(--mut)}}.skiprow{{margin-top:12px;padding-top:11px;border-top:1px dashed var(--line);font-size:12.5px;color:var(--mut)}}
.mock{{margin:14px 0}}.mock.desktop .frame{{max-width:760px;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}}.mock .bar{{display:flex;align-items:center;gap:6px;padding:7px 10px;background:#f6f8fa;border-bottom:1px solid var(--line)}}.mock .dot{{width:9px;height:9px;border-radius:50%;background:#d0d7de}}.mock .url{{flex:1;text-align:center;background:#fff;border:1px solid var(--line);border-radius:5px;padding:2px 8px;font-size:11px;color:var(--mut)}}.mock .body{{padding:14px;display:flex;flex-direction:column;gap:10px}}.mock .row{{display:flex;gap:10px}}.mock .box{{background:var(--soft);border:1px dashed #b9c7d6;border-radius:8px;min-height:34px;display:flex;align-items:center;justify-content:center;color:#4a5a6a;font-size:12px;text-align:center;padding:8px;flex:1}}.mock .box.tall{{min-height:120px}}.mock .box.cta{{background:var(--accent);border:0;color:#fff;font-weight:600}}.mock .cap{{font-size:12px;color:var(--mut);margin-top:6px;text-align:center}}
details{{margin:18px 0;border:1px solid var(--line);border-radius:8px;padding:10px 14px;background:#fff}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{border:1px solid var(--line);padding:6px 8px;text-align:left}}th{{background:#f6f8fa}}.lw-foot{{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);text-align:center;font-size:13px;color:var(--mut)}}
@media(max-width:720px){{.rec{{grid-template-columns:1fr}}}}
</style>
</head>
<body class="has-js">
<main>
<h1>Design Research: 电商售后 AI 辅助客服界面</h1>
<p class="sub">目标：商家侧「对话 + AI 策略」辅助模式 · 桌面 Web · 证据来源 Lazyweb HTTP API（MCP 会话未加载，已直连）</p>

<section id="agent-instructions" class="agent-instructions">
  <div class="ai-head"><span class="ai-badge">FOR THE CODING AGENT</span>
    <button class="ai-copy" type="button" onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('.ai-block').innerText)">Copy</button>
  </div>
  <p class="ai-human">保持「左对话、右 AI 策略」双栏，但右侧要拆成可扫读的卡片栈，而不是长报告墙。</p>
  <pre class="ai-block">LAZYWEB REPORT — AGENT HANDOFF
Use the report at {abs_report} as a starting point for implementing 电商售后 AI 辅助客服界面 using competitor-validated design patterns.

TOP RECOMMENDATIONS (do first):
1. 左侧固定聊天主舞台；右侧策略区分「案件摘要 / 建议话术 / 风险预警」三层卡片，商家一键采纳填入输入框。
2. 顶栏放订单号、纠纷类型、情绪/风险标签；分析中显示非阻断进度条，禁止模态打断。
3. 输入区保留商家最终发送权：AI 只预填草稿，发送按钮始终由商家点击。

INDEX ON: 双栏 copilot 布局、会话列表+详情、右侧上下文轨、话术建议 chips
DO NOT OVER-INDEX ON: 纯 C 端买家聊天 UI、无人工确认的自动发送、全屏 AI 聊天机器人
DIVE FURTHER: /lazyweb-design-improve — 用现有 DisputeView 截图对比这些参考

Evidence basis: Lazyweb screenshots · 2026-06-15</pre>
</section>

<div class="corpus"><span>⚠</span><p style="margin:0"><b>证据说明：</b> 共检索 8 组查询，筛出 {total_refs} 条相关截图；无实测转化数据，结论为<b>方向性</b>（设计流行度）。MCP 工具在 Cursor 会话中未挂载，已通过 Bearer token 直连 API。</p></div>

<h2>推荐路径</h2>
<nav class="legend" aria-label="Ranking summary">
  <a class="legend-row is-lead" href="#m1"><span class="lg-rank">1</span><span class="lg-name">双栏 Copilot：对话左、策略右<span class="lg-why">与项目 Frontend_spec 辅助模式一致</span></span><span class="verdict do">Do first</span><span class="lg-ev">DIRECTIONAL</span></a>
  <a class="legend-row" href="#m2"><span class="lg-rank" style="background:#3f6896">2</span><span class="lg-name">策略面板卡片化 + 可点击话术<span class="lg-why">降低阅读成本，支持一键填入</span></span><span class="verdict do">Do</span><span class="lg-ev">DIRECTIONAL</span></a>
  <a class="legend-row" href="#m3"><span class="lg-rank" style="background:#6b7787">3</span><span class="lg-name">顶栏案件上下文轨<span class="lg-why">订单/纠纷/风险标签常驻</span></span><span class="verdict explore">Explore</span><span class="lg-ev">DIRECTIONAL</span></a>
</nav>

<div class="recs">
  <article class="rec lead" id="m1">
    <div class="rec-proof">
      <span class="browserbar"><i></i><i></i><i></i><span class="url">merchant-console / dispute</span></span>
      <img src="{html.escape(rec1_refs[0].get('imageUrl',''))}" alt="proof" loading="lazy">
      <span class="rank-badge"><span class="num">1</span> Do next</span>
      <span class="proof-src">Proof · <b>{html.escape(rec1_refs[0].get('company',''))}</b></span>
    </div>
    <div class="rec-body">
      <h3>双栏 Copilot 布局</h3>
      <p class="rec-what">主流客服工作台把<b>会话/聊天</b>放在主区域，<b>AI 建议或客户上下文</b>放在相邻侧栏，而不是跳转到独立页面。你项目的 <code>DisputeView</code> 已符合此骨架。</p>
      <span class="ebadge directional">Directional · 多产品 inbox 形态</span>
      <p class="ev-note">Intercom / Zendesk / Freshdesk 等 inbox 产品普遍采用列表+详情或对话+侧栏结构。</p>
      <div class="skiprow"><b>Skip if</b> 目标用户是买家 C 端单栏聊天，而非商家控制台。</div>
    </div>
  </article>
  <article class="rec" id="m2">
    <div class="rec-proof">
      <span class="browserbar"><i></i><i></i><i></i><span class="url">strategy panel</span></span>
      <img src="{html.escape(rec2_refs[0].get('imageUrl',''))}" alt="proof" loading="lazy">
      <span class="rank-badge"><span class="num">2</span></span>
      <span class="proof-src">Proof · <b>{html.escape(rec2_refs[0].get('company',''))}</b></span>
    </div>
    <div class="rec-body">
      <h3>策略区卡片化 + 话术 Chips</h3>
      <p class="rec-what">AI 输出应拆成<b>短块</b>：结论一句、依据一条、话术 2–3 条可点选填入。避免右侧整页 Markdown 报告。</p>
      <span class="ebadge directional">Directional · copilot/assist 侧栏</span>
      <div class="skiprow"><b>Skip if</b> 商家需要导出完整法务级长报告（应放二级抽屉）。</div>
    </div>
  </article>
  <article class="rec" id="m3">
    <div class="rec-proof">
      <span class="browserbar"><i></i><i></i><i></i><span class="url">case context</span></span>
      <img src="{html.escape(rec3_refs[0].get('imageUrl',''))}" alt="proof" loading="lazy">
      <span class="rank-badge"><span class="num">3</span></span>
      <span class="proof-src">Proof · <b>{html.escape(rec3_refs[0].get('company',''))}</b></span>
    </div>
    <div class="rec-body">
      <h3>案件上下文顶栏/右轨</h3>
      <p class="rec-what">订单金额、纠纷类型、证据状态、情绪风险应用<b>标签+图标</b>常驻展示，分析时仅更新状态，不遮挡输入区。</p>
      <span class="ebadge directional">Directional</span>
      <div class="skiprow"><b>Skip if</b> 单会话极简场景且元数据极少。</div>
    </div>
  </article>
</div>

<h2>建议布局（Mock）</h2>
<figure class="mock desktop"><div class="frame"><div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="url">售后辅助台</span></div><div class="body">
  <div class="row"><div class="box">订单 # · 纠纷类型 · 风险标签</div></div>
  <div class="row"><div class="box tall">左侧：买家/商家对话流 + 图片证据</div><div class="box tall">右侧：案件摘要 / AI 策略 / 话术 chips / 请求分析</div></div>
  <div class="row"><div class="box">身份切换 · 附件 · 输入框</div><div class="box cta">商家发送（最终确认）</div></div>
</div></div><figcaption class="cap">Mock-frame — 对齐本项目辅助模式双栏 + 商家发送权</figcaption></figure>

<h2>关键参考</h2>
<p class="deck-hint">横向滑动查看 · 来源 [Lazyweb]</p>
{deck_figures(key_examples, "web")}

<h2>常见模式</h2>
"""

  for pid, pat in patterns.items():
    n = len(pat["refs"])
    if n == 0:
      continue
    prev = f"seen in {n} refs"
    html_doc += f"""
<div class="pat">
  <div class="pat-h"><h3>{html.escape(pat['name'])}</h3><span class="tag directional">Directional</span><span class="prev">{prev}</span></div>
  <p class="pat-claim">在客服/工单类产品中反复出现的结构，适合电商售后商家台。</p>
  {deck_figures(pat['refs'], 'web')}
</div>
"""

  html_doc += """
<h2>反模式</h2>
<div class="pat">
  <div class="pat-h"><h3>AI 自动代发消息</h3><span class="tag avoid">Avoid</span></div>
  <p class="pat-claim">辅助模式下商家必须保留发送权；参考主流 agent-assist 产品，AI 只建议不代发。</p>
</div>
<div class="pat">
  <div class="pat-h"><h3>模态弹窗打断分析</h3><span class="tag avoid">Avoid</span></div>
  <p class="pat-claim">与项目硬约束冲突：情绪/风险预警应非阻断式（inline alert），分析进度用顶栏或侧栏状态。</p>
</div>

<h2>与本项目对齐</h2>
<ul>
  <li><b>已有</b>：<code>DisputeView</code> 左右分栏、<code>ChatPanel</code> 请求 AI 帮助、<code>StrategyPanel</code> 话术应用。</li>
  <li><b>可加强</b>：右侧策略分层卡片；顶栏案件标签；分析进度非模态；话术以 chips/按钮呈现。</li>
</ul>

<details>
<summary>证据表（如何得出）</summary>
<table>
<tr><th>公司</th><th>平台</th><th>相似度</th><th>匹配</th><th>支持结论</th></tr>
"""
  for it in top:
    html_doc += (
      f"<tr><td>{html.escape(it['company'])}</td><td>{html.escape(it.get('platform',''))}</td>"
      f"<td>{it.get('similarity','')}</td><td>{it.get('matchCount','')}</td>"
      f"<td>{html.escape(it['visionDescription'][:80])}…</td></tr>"
    )

  top_companies = ", ".join(f"{c}({n})" for c, n in companies.most_common(8))
  html_doc += f"""
</table>
<p>高频品牌：{html.escape(top_companies)}</p>
</details>

<h2>Sources</h2>
<ul>
  <li>Lazyweb MCP HTTP API — 8 组 lazyweb_search 查询</li>
  <li>项目规范 — docs/Frontend_spec.md</li>
</ul>

<footer class="lw-foot">Powered by <a href="https://www.lazyweb.com">Lazyweb</a> — turn your agent into a design researcher… for free!</footer>
</main>
</body>
</html>
"""

  REPORT_PATH.write_text(html_doc, encoding="utf-8")
  print("WROTE", REPORT_PATH)
  print("items", total_refs)


if __name__ == "__main__":
  items = load_search_items()
  build_report(items)
