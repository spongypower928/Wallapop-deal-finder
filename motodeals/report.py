"""Render ranked deals as a self-contained HTML page: photo thumbnails, key
specs, and a button linking to the live Wallapop listing (where the full photo
gallery lives). Open it in any browser — no server needed."""
from __future__ import annotations

import html
import json
from datetime import datetime


def image_url(raw: dict) -> str | None:
    """First listing photo (medium size) from the raw Wallapop item."""
    imgs = raw.get("images") or []
    if imgs and isinstance(imgs[0], dict):
        urls = imgs[0].get("urls") or {}
        return urls.get("medium") or urls.get("small") or urls.get("big")
    return None


def latlon(raw: dict) -> tuple[float, float] | None:
    """Approximate (privacy-fuzzed) listing coordinates, if present."""
    loc = raw.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>motodeals — {generated}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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
 #map{{height:460px;margin:0}}
 .legend{{padding:8px 24px;background:#161a22;color:#9aa4b2;font-size:13px;
   border-bottom:1px solid #262b36}}
 .legend span{{display:inline-block;padding:2px 8px;border-radius:10px;margin:0 2px;
   color:#08130b;font-weight:600}}
 .leaflet-popup-content{{font:13px system-ui;margin:8px}}
</style></head><body>
<header><h1>🏍️ motodeals — best deals</h1>
<p>Generated {generated} · ranked by % below estimated fair price · click a card to view photos on Wallapop</p></header>
{map}
{sections}
</body></html>
"""

# Sequential green ramp (light→dark = bigger discount) + marker radius as a
# SECOND encoding, so deal quality is never signalled by colour alone.
_MAP = """
<div class="legend">Map — colour &amp; size = % under fair price:
 <span style="background:#74c69d">&lt;20%</span>
 <span style="background:#40a86a">20–30%</span>
 <span style="background:#1f7a44;color:#fff">30–40%</span>
 <span style="background:#0b5e2a;color:#fff">≥40%</span>
 &nbsp; 🏠 = search centre &nbsp;·&nbsp; click a pin for details</div>
<div id="map"></div>
<script>
const DEALS = {markers};
const CENTER = {center};
const map = L.map('map');
// CARTO basemap (OSM data, no API key, allows keyless local/file:// use —
// avoids OpenStreetMap's tile policy that rejects requests without a Referer).
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom: 19, subdomains: 'abcd',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}}).addTo(map);
function style(d) {{
  if (d >= 0.40) return {{c: '#0b5e2a', r: 15}};
  if (d >= 0.30) return {{c: '#1f7a44', r: 12}};
  if (d >= 0.20) return {{c: '#40a86a', r: 9}};
  return {{c: '#74c69d', r: 7}};
}}
const pts = [];
DEALS.forEach(function (x) {{
  const s = style(x.disc);
  const m = L.circleMarker([x.lat, x.lon], {{radius: s.r, fillColor: s.c,
     color: '#fff', weight: 2, fillOpacity: 0.9}});
  m.bindPopup(
    (x.img ? '<img src="' + x.img + '" style="width:150px;height:90px;object-fit:cover;border-radius:6px"><br>' : '') +
    '<b>' + Math.round(x.disc * 100) + '% under</b> · ' + x.price.toLocaleString() + ' €<br>' +
    x.title + '<br>' + (x.km ? x.km.toLocaleString() + ' km · ' : '') + (x.year || '') +
    '<br><a href="' + x.url + '" target="_blank" rel="noopener">Ver en Wallapop →</a>');
  m.addTo(map);
  pts.push([x.lat, x.lon]);
}});
if (CENTER) {{
  L.marker(CENTER, {{icon: L.divIcon({{html: '🏠', className: '',
     iconSize: [24, 24], iconAnchor: [12, 12]}})}}).addTo(map).bindPopup('Centro de búsqueda');
  pts.push(CENTER);
}}
if (pts.length) map.fitBounds(pts, {{padding: [30, 30]}});
else map.setView(CENTER || [40.4, -3.7], 6);
</script>"""

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


def _map_html(sections, center) -> str:
    """Build the Leaflet map block from every deal that has coordinates."""
    markers = []
    for _heading, deals in sections:
        for d in deals:
            if not d.get("lat") or not d.get("lon"):
                continue
            markers.append({
                "lat": d["lat"], "lon": d["lon"], "disc": round(d["disc"], 3),
                "price": d["price"], "year": d.get("year"),
                "km": d.get("mileage_km"), "img": d.get("image"),
                "title": (d.get("title") or "")[:70].replace("<", "").replace(">", ""),
                "url": d["url"],
            })
    if not markers:
        return ""
    return _MAP.format(markers=json.dumps(markers),
                       center=json.dumps(list(center)) if center else "null")


def render(sections: list[tuple[str, list[dict]]], center=None) -> str:
    """sections: list of (heading, deals). Each deal dict has keys: disc, price,
    est, year, mileage_km, condition, red_flags, count, title, url, image, lat, lon.
    center: (lat, lon) of the search origin, shown as a home marker."""
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
    return _PAGE.format(generated=generated, map=_map_html(sections, center),
                        sections="\n".join(out))
