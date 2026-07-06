"""Command-line entry point.

Usage:
    python -m motodeals scrape            # scrape every model in config.toml
    python -m motodeals scrape --show     # run a visible browser (debugging)
    python -m motodeals list              # dump what's in the database
"""
from __future__ import annotations

import argparse
import json

from . import config, db, extract as extract_mod, pricing, report
from .scrapers import wallapop


def cmd_scrape(args: argparse.Namespace) -> None:
    cfg = config.load()
    conn = db.connect()
    for search in cfg.searches:
        print(f"\n== {search.name} ({search.keywords!r}) ==")
        listings = wallapop.scrape(search, cfg.location, headless=not args.show)
        new = 0
        for lst in listings:
            if db.upsert(conn, lst):
                new += 1
        conn.commit()
        print(f"  {len(listings)} listings ({new} new)")
    conn.close()


def cmd_extract(args: argparse.Namespace) -> None:
    conn = db.connect()
    where = "" if args.all else "WHERE extracted_at IS NULL"
    limit = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(
        f"SELECT id, model, title, raw FROM listings {where} ORDER BY last_seen DESC {limit}"
    ).fetchall()
    # Skip listings that don't pass their model's relevance filter — no point
    # spending LLM time on bikes that can never appear as deals.
    if not args.include_irrelevant:
        searches = {s.name: s for s in config.load().searches}
        rows = [r for r in rows
                if r["model"] in searches and searches[r["model"]].matches_title(r["title"])]
    print(f"Extracting {len(rows)} listing(s) with model {args.model!r} ...")
    ok = 0
    for i, r in enumerate(rows, 1):
        raw = json.loads(r["raw"])
        text = f"{r['title'] or ''}\n{raw.get('description', '')}"
        try:
            data = extract_mod.extract(text, model=args.model, host=args.host)
        except extract_mod.ExtractionError as e:
            print(f"  ! {e}")
            break  # server/model problem affects all rows; stop early
        db.update_extraction(conn, r["id"], data)
        conn.commit()
        ok += 1
        km = f"{data['mileage']}km" if data["mileage"] else "?km"
        flags = ", ".join(data["red_flags"]) or "-"
        print(f"  [{i}/{len(rows)}] {km:>9} {data['condition']:>10} "
              f"itv={data['has_itv']}  flags: {flags}")
    print(f"\nExtracted {ok}/{len(rows)}.")
    conn.close()


def _score_search(conn, search, args):
    """Return (model, ranked_deals) for one search. ranked_deals is a list of
    dicts (deal-shaped, deduped, sorted by discount desc) honoring the filters."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM listings WHERE model = ? AND price IS NOT NULL",
        (search.name,))]
    rows = [r for r in rows if search.matches_title(r["title"])]  # drop spam mismatches
    priced = [r for r in rows if r["year"] and r["mileage_km"]]
    model = pricing.fit(pricing.dedup_samples(priced))

    # Collapse dealer reposts (identical year/mileage/price) into one entry.
    by_key: dict[tuple, dict] = {}
    for r in priced:
        key = (r["year"], r["mileage_km"], round(r["price"] / 50) * 50)
        by_key.setdefault(key, {"row": r, "count": 0})["count"] += 1

    deals = []
    for entry in by_key.values():
        r = entry["row"]
        if args.year_min and r["year"] < args.year_min:
            continue
        if args.year_max and r["year"] > args.year_max:
            continue
        est = model.predict(pricing.CURRENT_YEAR - r["year"], r["mileage_km"])
        disc = (est - r["price"]) / est if est > 0 else 0.0
        if not args.all and disc < args.min_discount:
            continue
        if abs(disc) < 0.005:
            disc = 0.0
        deals.append({
            "disc": disc, "est": est, "price": r["price"], "year": r["year"],
            "mileage_km": r["mileage_km"], "condition": r["condition"],
            "red_flags": json.loads(r["red_flags"]) if r["red_flags"] else [],
            "count": entry["count"], "title": r["title"], "url": r["url"],
            "image": report.image_url(json.loads(r["raw"])),
        })
    deals.sort(key=lambda d: d["disc"], reverse=True)
    return model, deals, len(rows), len(priced)


def cmd_deals(args: argparse.Namespace) -> None:
    cfg = config.load()
    conn = db.connect()
    html_sections = []
    for search in cfg.searches:
        model, deals, n_rows, n_priced = _score_search(conn, search, args)
        print(f"\n== {search.name} ==  {n_rows} listings ({n_priced} with year+km) "
              f"| fair-price model: {model.kind}, n={model.n}, R²={model.r2:.2f}")
        for d in deals:
            warn = " ⚠needs_work" if d["condition"] == "needs_work" else ""
            if d["red_flags"]:
                warn += " ⚠" + ",".join(d["red_flags"])
            tag = "DEAL" if d["disc"] >= args.min_discount else "    "
            dup = f" (×{d['count']} reposts)" if d["count"] > 1 else ""
            print(f"  {tag} {d['disc']:+5.0%}  {d['price']:>6.0f}€ (est {d['est']:>6.0f}€)  "
                  f"{d['year']}  {d['mileage_km']:>6}km  {(d['condition'] or '?'):<10}{warn}{dup}")
            print(f"        {(d['title'] or '')[:55]}  {d['url']}")
        if not deals:
            print(f"  (no listings ≥ {args.min_discount:.0%} under estimate; "
                  f"use --all to see everything)")
        html_sections.append((f"{search.name} — {model.kind} model, "
                              f"n={model.n}, R²={model.r2:.2f}", deals))

    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(report.render(html_sections))
        total = sum(len(d) for _, d in html_sections)
        print(f"\nWrote {total} deals to {args.html} — open it in a browser.")
    conn.close()


def cmd_check(args: argparse.Namespace) -> None:
    """Data-quality sweep: report (and with --fix, correct) implausible rows."""
    from datetime import date
    yr = date.today().year
    conn = db.connect()

    new_km = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE condition='new' AND mileage_km>100"
    ).fetchone()[0]
    yr_eq_km = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE mileage_km = year"
    ).fetchone()[0]
    # Implausible km-per-year ⇒ the (regex) YEAR is wrong, since mileage is now
    # LLM-derived. A used bike almost never exceeds ~25k km/yr; anything above
    # that is a misparsed year (e.g. a "2026" with 66,000 km).
    KMY_MAX = 25_000
    bad_year = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE year IS NOT NULL AND mileage_km IS NOT NULL "
        "AND CAST(mileage_km AS REAL) / MAX(? - year, 1) > ?", (yr, KMY_MAX)
    ).fetchone()[0]
    # softer: mileage looks like a year but isn't an exact match (report only)
    yearlike = conn.execute(
        "SELECT year, mileage_km, title FROM listings "
        "WHERE mileage_km BETWEEN 1990 AND ? AND mileage_km <> year", (yr,)
    ).fetchall()

    print(f"'new' but >100km (→ used):          {new_km}")
    print(f"mileage == year (misparse → null):   {yr_eq_km}")
    print(f"impossible km/year (year → null):    {bad_year}")
    print(f"mileage looks like a year (review):  {len(yearlike)}")
    for r in yearlike[:10]:
        print(f"    year={r['year']} km={r['mileage_km']}  {r['title'][:50]}")

    if args.fix:
        c1 = conn.execute(
            "UPDATE listings SET condition='used' "
            "WHERE condition='new' AND mileage_km>100").rowcount
        c2 = conn.execute(
            "UPDATE listings SET mileage_km=NULL WHERE mileage_km = year").rowcount
        c3 = conn.execute(
            "UPDATE listings SET year=NULL WHERE year IS NOT NULL AND mileage_km IS NOT NULL "
            "AND CAST(mileage_km AS REAL) / MAX(? - year, 1) > ?", (yr, KMY_MAX)).rowcount
        conn.commit()
        print(f"\nfixed: {c1} condition new→used, {c2} mileage nulled, {c3} year nulled")
    else:
        print("\n(run with --fix to apply corrections)")
    conn.close()


def cmd_plot(args: argparse.Namespace) -> None:
    from . import plots
    import os
    cfg = config.load()
    conn = db.connect()
    os.makedirs(args.out_dir, exist_ok=True)
    for search in cfg.searches:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM listings WHERE model=? AND price IS NOT NULL "
            "AND year IS NOT NULL AND mileage_km IS NOT NULL", (search.name,))]
        rows = [r for r in rows if search.matches_title(r["title"])]
        if not rows:
            print(f"{search.name}: no priced listings yet")
            continue
        model = pricing.fit(pricing.dedup_samples(rows))
        path = os.path.join(args.out_dir, f"{search.name}.png")
        plots.make(search.name, rows, model, path)
        print(f"{search.name}: wrote {path} ({len(rows)} listings, R²={model.r2:.2f})")
    conn.close()


def cmd_list(args: argparse.Namespace) -> None:
    conn = db.connect()
    rows = conn.execute(
        "SELECT model, price, year, mileage_km, condition, red_flags, title, url "
        "FROM listings ORDER BY model, price"
    ).fetchall()
    for r in rows:
        yr = r["year"] or "----"
        km = f"{r['mileage_km']}km" if r["mileage_km"] else "?km"
        cond = (r["condition"] or "?")[:10]
        nflags = len(json.loads(r["red_flags"])) if r["red_flags"] else 0
        flag = f"⚠{nflags}" if nflags else "  "
        print(f"{r['model']:>16}  {r['price']:>7.0f}€  {yr}  {km:>9}  "
              f"{cond:>10} {flag}  {r['title']}")
    print(f"\n{len(rows)} listings total")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="motodeals")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="scrape all configured models")
    p_scrape.add_argument("--show", action="store_true", help="show the browser window")
    p_scrape.set_defaults(func=cmd_scrape)

    p_extract = sub.add_parser("extract", help="run local LLM extraction on listings")
    p_extract.add_argument("--model", default=extract_mod.DEFAULT_MODEL,
                           help=f"Ollama model (default: {extract_mod.DEFAULT_MODEL})")
    p_extract.add_argument("--host", default=extract_mod.DEFAULT_HOST)
    p_extract.add_argument("--all", action="store_true",
                           help="re-extract everything, not just un-extracted rows")
    p_extract.add_argument("--limit", type=int, default=0, help="cap number of rows")
    p_extract.add_argument("--include-irrelevant", action="store_true",
                           help="also extract listings that fail the title filter")
    p_extract.set_defaults(func=cmd_extract)

    p_deals = sub.add_parser("deals", help="rank listings by %% below fair price")
    p_deals.add_argument("--min-discount", type=float, default=0.10,
                         help="threshold to flag a DEAL (default 0.10 = 10%%)")
    p_deals.add_argument("--all", action="store_true",
                         help="show every priced listing, not just deals")
    p_deals.add_argument("--year-min", type=int, help="only show listings from this year on")
    p_deals.add_argument("--year-max", type=int, help="only show listings up to this year")
    p_deals.add_argument("--html", metavar="PATH",
                         help="also write a browsable HTML report (photos + links)")
    p_deals.set_defaults(func=cmd_deals)

    p_check = sub.add_parser("check", help="data-quality sweep (--fix to correct)")
    p_check.add_argument("--fix", action="store_true", help="apply corrections")
    p_check.set_defaults(func=cmd_check)

    p_plot = sub.add_parser("plot", help="save data + regression diagnostic plots")
    p_plot.add_argument("--out-dir", default="plots", help="output directory")
    p_plot.set_defaults(func=cmd_plot)

    p_list = sub.add_parser("list", help="print stored listings")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
