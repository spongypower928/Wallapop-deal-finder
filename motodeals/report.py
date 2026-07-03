"""Render ranked deals as a self-contained HTML page: photo thumbnails, key
specs, and a button linking to the live Wallapop listing (where the full photo
gallery lives). Open it in any browser — no server needed."""
from __future__ import annotations

import html
from datetime import datetime


def image_url(raw: dict) -> str | None:
    """First listing photo (medium size) from the raw Wallapop item."""
    imgs = raw.get("images") or []
    if imgs and isinstance(imgs[0], dict):
        urls = imgs[0].get("urls") or {}
        return urls.get("medium") or urls.get("small") or urls.get("big")
    return None


_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>motodeals — {generated}</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
   background:#0f1115;color:#e6e6e6}}
 header{{padding:18px 24px;background:#161a22;border-bottom:1px solid #262b36}}
 header h1{{margin:0;font-size:18px}} header p{{margin:4px 0 0;color:#9aa4b2;font-size:13px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
   gap:16px;padding:24px}}
 .card{{background:#161a22;border:1px solid #262b36;border-radius:12px;overflow:hidden;
   display:flex;flex-direction:column}}
 .thumb{{width:100%;height:180px;object-fit:cover;background:#0b0d11}}
 .body{{padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1}}
 .disc{{position:absolute;margin:8px;padding:3px 8px;border-radius:20px;font-weight:700;
   font-size:13px;background:#1f9d55;color:#fff}}
 .disc.neg{{background:#4b5563}}
 .price{{font-size:20px;font-weight:700}}
 .est{{color:#9aa4b2;font-size:13px}}
 .specs{{font-size:13px;color:#c7cdd6}}
 .flags{{font-size:12px;color:#f0a3a3}}
 .cond-needs_work{{color:#f0a3a3;font-weight:600}}
 a.btn{{margin-top:auto;text-align:center;background:#2563eb;color:#fff;text-decoration:none;
   padding:8px;border-radius:8px;font-size:14px}}
 a.btn:hover{{background:#1d4ed8}}
</style></head><body>
<header><h1>🏍️ motodeals — best deals</h1>
<p>Generated {generated} · ranked by % below estimated fair price · click a card to view photos on Wallapop</p></header>
{sections}
</body></html>
"""

_CARD = """
<div class="card">
  <div style="position:relative">
    <span class="disc {negclass}">{disc:+.0%}</span>
    {img}
  </div>
  <div class="body">
    <div><span class="price">{price:,.0f} €</span>
         <span class="est">est {est:,.0f} €</span></div>
    <div class="specs">{year} · {km} · <span class="cond-{cond}">{cond}</span>{reposts}</div>
    {flags}
    <div class="specs" style="color:#8b93a1">{title}</div>
    <a class="btn" href="{url}" target="_blank" rel="noopener">Ver en Wallapop →</a>
  </div>
</div>"""


def render(sections: list[tuple[str, list[dict]]]) -> str:
    """sections: list of (heading, deals). Each deal dict has keys:
    disc, price, est, year, mileage_km, condition, red_flags, count, title, url, image."""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    out = []
    for heading, deals in sections:
        out.append(f'<h2 style="padding:0 24px;margin:8px 0 0">{html.escape(heading)} '
                   f'<span style="color:#9aa4b2;font-weight:400;font-size:14px">'
                   f'({len(deals)} shown)</span></h2>')
        cards = []
        for d in deals:
            img = (f'<img class="thumb" src="{html.escape(d["image"])}" loading="lazy" '
                   f'alt="">' if d.get("image")
                   else '<div class="thumb"></div>')
            flags = (f'<div class="flags">⚠ {html.escape(", ".join(d["red_flags"]))}</div>'
                     if d.get("red_flags") else "")
            reposts = f' · ×{d["count"]} reposts' if d.get("count", 1) > 1 else ""
            km = f'{d["mileage_km"]:,} km' if d.get("mileage_km") else "? km"
            cards.append(_CARD.format(
                negclass="neg" if d["disc"] < 0 else "",
                disc=d["disc"], price=d["price"], est=d["est"],
                year=d.get("year") or "----", km=km,
                cond=html.escape(d.get("condition") or "?"),
                reposts=reposts, flags=flags,
                title=html.escape((d.get("title") or "")[:70]),
                url=html.escape(d["url"]), img=img))
        out.append('<div class="grid">' + "".join(cards) + "</div>")
    return _PAGE.format(generated=generated, sections="\n".join(out))
