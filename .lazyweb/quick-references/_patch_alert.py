# -*- coding: utf-8 -*-
"""补全 inline 预警模式的 Lazyweb 参考图。"""
import html
import json
import os
import pathlib
import re
import urllib.request

REPORT = pathlib.Path(__file__).resolve().parent / "aftersales-cs-ui-components-2026-06-15" / "report.html"


def fetch_alert_refs():
    token = open(os.path.expanduser("~/.lazyweb/lazyweb_mcp_token"), encoding="utf-8").read().strip()
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "lazyweb_search",
                "arguments": {
                    "query": "warning banner dashboard alert",
                    "platform": "desktop",
                    "limit": 20,
                    "skill": "quick-references",
                },
            },
        }
    ).encode()
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
    raw = urllib.request.urlopen(req, timeout=120).read().decode()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        obj = json.loads(line)
        if "result" not in obj:
            continue
        data = json.loads(obj["result"]["content"][0]["text"])
        return [
            r
            for r in data.get("results", [])
            if any(
                k in (r.get("visionDescription") or "").lower()
                for k in ("alert", "banner", "warning", "notification")
            )
        ]
    return []


def deck(refs):
    figs = []
    for r in refs[:3]:
        co = html.escape(r.get("company") or "Unknown")
        img = html.escape(r.get("imageUrl") or r.get("image_url") or "")
        cap = html.escape((r.get("visionDescription") or "")[:160])
        figs.append(
            f'<figure class="shot-web"><img src="{img}" alt="{co}" loading="lazy">'
            f'<figcaption class="cap"><span class="src">[Lazyweb]</span> <b>{co}</b> &mdash; {cap}</figcaption></figure>'
        )
    nav = (
        '<div class="deck-nav">'
        '<button type="button" aria-label="Previous" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:-((f?f.offsetWidth:300)+12),behavior:\'smooth\'})">&#9664;</button>'
        '<button type="button" aria-label="Next" onclick="var d=this.closest(\'.deck-nav\').previousElementSibling,f=d.querySelector(\'figure\');d.scrollBy({left:(f?f.offsetWidth:300)+12,behavior:\'smooth\'})">&#9654;</button>'
        "</div>"
    )
    return f'<div class="deckwrap"><div class="deck web">{"".join(figs)}</div>{nav}</div>'


def main():
    refs = fetch_alert_refs()
    if not refs:
        print("no refs")
        return
    text = REPORT.read_text(encoding="utf-8")
    text = text.replace('<span class="prev">0 matches</span>', f'<span class="prev">seen in {min(3,len(refs))} refs</span>', 1)
    text = re.sub(
        r'(<div class="pat">\s*<div class="pat-h"><h3>非阻断式 inline 预警</h3>.*?<p class="pat-claim">.*?</p>\s*)<p class="deck-hint">[^<]+</p>',
        r"\1" + deck(refs),
        text,
        count=1,
        flags=re.S,
    )
    REPORT.write_text(text, encoding="utf-8")
    print("patched", len(refs))


if __name__ == "__main__":
    main()
