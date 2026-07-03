"""Wallapop scraper.

Strategy: open Wallapop's own search page in a headless browser and capture the
JSON its front-end fetches from api.wallapop.com. The browser performs the
request-signing Wallapop requires, so we never have to replicate it. We then
pull listing objects out of whatever JSON shape comes back.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Iterator

from playwright.sync_api import sync_playwright

from ..config import Location, Search

SEARCH_PAGE = "https://es.wallapop.com/app/search"


def _search_url(keyword: str, search: Search, loc: Location) -> str:
    params = {
        "keywords": keyword,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "distance": loc.distance_km * 1000,  # Wallapop wants metres
        "order_by": "newest",
    }
    if search.min_price is not None:
        params["min_sale_price"] = search.min_price
    if search.max_price is not None:
        params["max_sale_price"] = search.max_price
    return SEARCH_PAGE + "?" + urllib.parse.urlencode(params)


def _walk(obj: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside a JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _as_price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):  # e.g. {"amount": 5000, "currency": "EUR"}
        for k in ("amount", "cash_price", "value"):
            if k in value:
                return _as_price(value[k])
    return None


def _looks_like_listing(d: dict) -> bool:
    has_id = any(k in d for k in ("id", "item_id"))
    has_price = any(k in d for k in ("price", "sale_price"))
    has_title = any(k in d for k in ("title", "name"))
    return has_id and has_price and has_title


def extract_listings(payload: Any) -> list[dict]:
    """Pull listing-shaped dicts out of a Wallapop API JSON response."""
    out = []
    for d in _walk(payload):
        if not _looks_like_listing(d):
            continue
        item_id = str(d.get("id") or d.get("item_id"))
        price = _as_price(d.get("price") if "price" in d else d.get("sale_price"))
        if not item_id or price is None:
            continue
        out.append({
            "item_id": item_id,
            "price": price,
            "title": d.get("title") or d.get("name"),
            "description": d.get("description") or "",
            "slug": d.get("web_slug"),
            "raw": d,
        })
    return out


_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
# A number (optionally dotted like 45.000) immediately followed by a km unit.
_KM_RE = re.compile(r"(\d{1,3}(?:[.\s]?\d{3})?|\d{1,6})\s*(?:km|kms|kilómetros)\b",
                    re.IGNORECASE)

# Plausible bounds so we discard phone numbers / typos.
_KM_MIN, _KM_MAX = 100, 250_000


def _guess_year(text: str) -> int | None:
    from datetime import date
    this_year = date.today().year
    for m in _YEAR_RE.finditer(text or ""):
        y = int(m.group(1))
        if 1990 <= y <= this_year:  # reject impossible/future years
            return y
    return None


def _guess_mileage(text: str) -> int | None:
    for m in _KM_RE.finditer(text or ""):
        km = int(re.sub(r"[.\s]", "", m.group(1)))
        if _KM_MIN <= km <= _KM_MAX:
            return km
    return None


SECTION_ENDPOINT = "api.wallapop.com/api/v3/search/section"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _next_token(data: dict) -> str | None:
    return (data.get("meta") or {}).get("next_page")


def scrape(search: Search, loc: Location, *, headless: bool = True,
           max_pages: int = 30) -> list[dict]:
    """Return normalized listing dicts for one search.

    We load the search page once (to establish the session and capture a real,
    correctly-headered /search/section request), then paginate by replaying that
    endpoint with meta.next_page as the cursor — deep, deterministic, no scrolling.
    """
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(locale="es-ES", user_agent=USER_AGENT)
        page = ctx.new_page()
        cookies_done = False

        for keyword in search.keywords:
            first: dict = {}

            def on_response(resp, _first=first):
                if SECTION_ENDPOINT in resp.url and "url" not in _first:
                    try:
                        _first["data"] = resp.json()
                    except Exception:
                        return
                    _first["url"] = resp.url
                    _first["headers"] = resp.request.all_headers()

            page.on("response", on_response)
            page.goto(_search_url(keyword, search, loc),
                      wait_until="domcontentloaded", timeout=60_000)
            if not cookies_done:
                try:
                    page.click("#onetrust-accept-btn-handler", timeout=4000)
                except Exception:
                    pass
                cookies_done = True
            page.wait_for_timeout(3000)
            page.remove_listener("response", on_response)

            if "data" not in first:
                continue  # keyword yielded no results / blocked

            # Page 1 (already fetched by the browser).
            data = first["data"]
            captured.extend(extract_listings(data))
            token = _next_token(data)

            # Deeper pages: replay the endpoint with the cursor, reusing the
            # browser's request headers (mpid / x-deviceos etc.) and cookies.
            base = first["url"].split("?")[0]
            params = {k: v[0] for k, v in
                      urllib.parse.parse_qs(urllib.parse.urlparse(first["url"]).query).items()}
            headers = {k: v for k, v in first["headers"].items() if not k.startswith(":")}
            pages = 1
            while token and pages < max_pages:
                params["next_page"] = token
                r = ctx.request.get(base + "?" + urllib.parse.urlencode(params),
                                    headers=headers)
                if r.status != 200:
                    break
                try:
                    data = r.json()
                except Exception:
                    break
                new = extract_listings(data)
                if not new:
                    break
                captured.extend(new)
                token = _next_token(data)
                pages += 1
                page.wait_for_timeout(400)  # be polite

        browser.close()

    # Dedup by item id and normalize into our storage schema.
    seen: dict[str, dict] = {}
    for item in captured:
        blob = f"{item.get('title') or ''} {item.get('description') or ''}"
        slug = item.get("slug") or item["item_id"]
        norm = {
            "id": f"wallapop:{item['item_id']}",
            "source": "wallapop",
            "model": search.name,
            "title": item.get("title"),
            "price": item["price"],
            "url": f"https://es.wallapop.com/item/{slug}",
            "location": _extract_location(item["raw"]),
            "year": _guess_year(blob),
            "mileage_km": _guess_mileage(blob),
            "raw": item["raw"],
        }
        seen[norm["id"]] = norm
    return list(seen.values())


def _extract_location(raw: dict) -> str | None:
    loc = raw.get("location") or raw.get("user", {}).get("location")
    if isinstance(loc, dict):
        return loc.get("city") or loc.get("postal_code")
    return None
