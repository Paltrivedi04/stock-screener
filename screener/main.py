"""Daily run: screen the market, compare with yesterday, update the website
and send the email. Started automatically by GitHub every weekday evening."""
import datetime as dt
import json
import os
import random
import sys
import traceback
from pathlib import Path

from . import config as C
from .emailer import send as send_email
from .investsights import InvestSights, QuotaReached
from .nse import NSE, summarize_filings
from .yahoo import Yahoo

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "docs" / "data"
STATE_DIR = ROOT / "data"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ---------------- small helpers ----------------
def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def fresh(entry, days, today):
    try:
        return (today - dt.date.fromisoformat(entry["fetched"])).days < days
    except (KeyError, TypeError, ValueError):
        return False


def cagr(start, end, years):
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def pct_gap(a, b):
    if a in (None, 0) or b in (None, 0):
        return None
    return abs(a - b) / abs(b) * 100


def screener_url(symbol):
    return f"https://www.screener.in/company/{symbol}/consolidated/"


def trim_statements(raw):
    """Keep only what we need from the statements reply."""
    inc = (raw.get("income_statement") or {}).get("data") or []
    cfs = (raw.get("cash_flow") or {}).get("data") or []
    profit = [{"fy": r.get("fiscal_year"), "end": r.get("period_end_date"),
               "net_profit": r.get("net_profit"), "revenue": r.get("revenue")} for r in inc]
    ocf = [{"fy": r.get("fiscal_year"), "end": r.get("period_end_date"),
            "ocf": r.get("operating_cash_flow")} for r in cfs]
    profit.sort(key=lambda r: r["end"] or "", reverse=True)
    ocf.sort(key=lambda r: r["end"] or "", reverse=True)
    return {"profit": profit, "ocf": ocf}


def trim_concall(raw):
    if not raw:
        return None
    d = raw.get("data") or {}
    if not d.get("found", True) and not raw.get("quarter"):
        return {"found": False}
    takeaway = (d.get("key_takeaway") or "").split("\n---")[0].replace("**", "").strip()
    if len(takeaway) > 700:
        takeaway = takeaway[:700].rsplit(". ", 1)[0] + "."
    return {
        "found": True,
        "quarter": raw.get("quarter") or d.get("quarter"),
        "fiscal_year": raw.get("fiscal_year") or d.get("fiscal_year"),
        "call_period_end": raw.get("call_period_end"),
        "transcript_url": d.get("transcript_url"),
        "takeaway": takeaway,
        "tone": ((d.get("tone_assessment") or "").split("\n")[0]
                 .replace("**Overall tone:**", "").replace("**", "").strip())[:160],
        "stale": bool(raw.get("staleness_note")),
    }


# ---------------- main ----------------
def run():
    now = dt.datetime.now(IST)
    today = now.date()
    print(f"Run started {now:%Y-%m-%d %H:%M} IST")

    key = os.environ.get("INVESTSIGHTS_KEY", "").strip()
    if not key:
        sys.exit("INVESTSIGHTS_KEY secret is missing. See the setup guide, step 4.")

    state = load(STATE_DIR / "state.json", {"members": {}, "last_run": None})
    cache = load(STATE_DIR / "cache.json", {"statements": {}, "concalls": {}})
    history = load(SITE_DATA / "history.json", [])
    baseline = not state.get("last_run")

    sources = {}
    notes = []

    # ---- 1. NSE: official list and prices ----
    nse = NSE()
    listed = nse.equity_list()
    price_date, nse_prices = nse.bhavcopy(today)
    sources["NSE"] = {
        "ok": bool(nse_prices),
        "detail": (f"Prices for {price_date}, {len(listed or {}):,} listed companies"
                   if nse_prices else "NSE did not respond today; InvestSights prices used"),
    }

    # ---- 2. InvestSights: stage 1 screen ----
    inv = InvestSights(key, budget=C.DAILY_REQUEST_BUDGET)
    inv.connect()
    candidates, meta = inv.screen_all(C.stage1_filters(), C.MARKET_CAP_MIN_CR, C.MARKET_CAP_MAX_CR)
    print(f"Stage 1: {len(candidates)} companies")

    yahoo = Yahoo()
    shortlist, manual, failed = [], [], {}
    quota_hit = False

    for row in candidates:
        sym = row["symbol"]
        rec = {
            "symbol": sym,
            "name": row.get("company_name") or (listed or {}).get(sym, sym),
            "sector": row.get("sector"),
            "market_cap": row.get("market_cap"),
            "price": row.get("close_price"),
            "price_date": row.get("price_date") or meta.get("price_as_of"),
            "price_source": "InvestSights",
            "pe": row.get("pe_ratio"),
            "roe": row.get("roe"),
            "roce": row.get("roce"),
            "de": row.get("debt_to_equity"),
            "sales_growth_yoy": row.get("revenue_growth_yoy"),
            "return_1m": row.get("return_1m"),
            "screener_url": screener_url(sym),
            "data_note": row.get("data_note") or meta["data_notes"].get(sym),
        }
        if nse_prices and sym in nse_prices and nse_prices[sym]["close"]:
            rec["price"] = nse_prices[sym]["close"]
            rec["price_date"] = price_date
            rec["price_source"] = "NSE"
        if listed is not None and sym not in listed and not sym.isdigit():
            rec["nse_note"] = "Not in NSE's current equity list (may be BSE-only or recently changed)"

        # Debt to Equity
        if rec["de"] is None:
            rec["reason"] = "Debt to Equity not available"
            manual.append(rec)
            continue
        if rec["de"] >= C.DE_MAX:
            failed[sym] = f"Debt to Equity {rec['de']:.2f} (needs below {C.DE_MAX:g})"
            continue

        # Statements (cached weekly)
        st = cache["statements"].get(sym)
        if not fresh(st, C.STATEMENTS_CACHE_DAYS, today):
            try:
                st = {"fetched": today.isoformat(), **trim_statements(inv.statements(sym))}
                cache["statements"][sym] = st
            except QuotaReached:
                quota_hit = True
                st = cache["statements"].get(sym)
            except Exception as e:  # noqa: BLE001
                notes.append(f"{sym}: statements error {e}")
                st = cache["statements"].get(sym)
        if not st:
            rec["reason"] = "Financial statements not available today"
            manual.append(rec)
            continue

        # Profit growth, 3-year CAGR
        profits = [p for p in st["profit"] if p.get("net_profit") is not None]
        if len(profits) < 4:
            rec["reason"] = "Fewer than 4 years of profit data"
            manual.append(rec)
            continue
        end, start = profits[0]["net_profit"], profits[3]["net_profit"]
        rec["profit_cagr_3y"] = cagr(start, end, 3)
        rec["profit_years"] = f"{profits[3]['fy']} to {profits[0]['fy']}"
        rec["net_profit_cr"] = end / 1e7
        revs = [p.get("revenue") for p in st["profit"]]
        if len(revs) >= 4 and revs[0] and revs[3]:
            rec["sales_cagr_3y"] = cagr(revs[3], revs[0], 3)
        if rec["profit_cagr_3y"] is None:
            if end is not None and end <= 0:
                failed[sym] = "Latest yearly profit is negative"
            else:
                rec["reason"] = f"Profit was zero or negative in {profits[3]['fy']}, so 3-year growth can't be measured"
                manual.append(rec)
            continue
        if rec["profit_cagr_3y"] <= C.PROFIT_CAGR_3Y_MIN:
            failed[sym] = f"Profit growth {rec['profit_cagr_3y']:.1f}% (needs above {C.PROFIT_CAGR_3Y_MIN}%)"
            continue

        # Operating cash flow, last N years
        n = C.OCF_POSITIVE_YEARS
        ocf = st["ocf"][:n]
        ocf_vals = [o.get("ocf") for o in ocf]
        ocf_source = "InvestSights"
        if len(ocf_vals) < n or any(v is None for v in ocf_vals):
            ys = yahoo.operating_cash_flows(sym)
            if ys and len(ys) >= n:
                ocf = [{"fy": y, "ocf": v} for y, v in ys[:n]]
                ocf_vals = [v for _, v in ys[:n]]
                ocf_source = "Yahoo (filled gap)"
            else:
                rec["reason"] = "Operating cash flow missing in InvestSights and Yahoo"
                manual.append(rec)
                continue
        rec["ocf"] = [{"fy": o["fy"], "cr": round(o["ocf"] / 1e7, 1)} for o in ocf]
        rec["ocf_source"] = ocf_source
        negatives = [o["fy"] for o in ocf if o["ocf"] <= 0]
        if negatives:
            failed[sym] = f"Operating cash flow negative in {', '.join(map(str, negatives))}"
            continue

        shortlist.append(rec)

    print(f"Shortlist: {len(shortlist)}, check manually: {len(manual)}, failed: {len(failed)}")

    # ---- 3. Concall and presentation details ----
    for rec in shortlist:
        sym = rec["symbol"]
        cc = cache["concalls"].get(sym)
        if not fresh(cc, C.CONCALL_CACHE_DAYS, today) and not quota_hit:
            try:
                cc = {"fetched": today.isoformat(), "data": trim_concall(inv.concall(sym))}
                cache["concalls"][sym] = cc
            except QuotaReached:
                quota_hit = True
            except Exception as e:  # noqa: BLE001
                notes.append(f"{sym}: concall error {e}")
        rec["concall"] = (cc or {}).get("data")
        rec["filings"] = summarize_filings(nse.announcements(sym))

    filings_ok = sum(1 for r in shortlist if r.get("filings") is not None)
    sources["NSE filings"] = {
        "ok": filings_ok > 0 or not shortlist,
        "detail": f"Concall/presentation filings read for {filings_ok} of {len(shortlist)} companies",
    }

    # ---- 4. Compare with yesterday ----
    prev = state.get("members", {})
    now_syms = {r["symbol"] for r in shortlist}
    manual_syms = {r["symbol"] for r in manual}
    added, removed = [], []
    for r in shortlist:
        if r["symbol"] in prev:
            r["first_seen"] = prev[r["symbol"]]["first_seen"]
        else:
            r["first_seen"] = today.isoformat()
            if not baseline:
                added.append({k: r.get(k) for k in ("symbol", "name", "price", "screener_url")})
    for sym, info in prev.items():
        if sym in now_syms:
            continue
        if sym in failed:
            reason = failed[sym]
        elif sym in manual_syms:
            reason = "Moved to 'Check manually': " + next(m["reason"] for m in manual if m["symbol"] == sym)
        else:
            reason = "No longer passes the main screen (market cap, ROE, ROCE, promoter, P/E or sales growth)"
        removed.append({"symbol": sym, "name": info.get("name", sym), "reason": reason,
                        "first_seen": info.get("first_seen"), "screener_url": screener_url(sym),
                        "price": None})

    # ---- 5. Cross-check a sample ----
    rng = random.Random(today.isoformat())
    pool = shortlist + manual
    sample = rng.sample(pool, min(C.CROSSCHECK_SAMPLE_SIZE, len(pool)))
    checks, compared, matched = [], 0, 0
    for rec in sample:
        y = yahoo.snapshot(rec["symbol"])
        row = {"symbol": rec["symbol"], "name": rec["name"], "items": []}
        def add(label, ours, theirs, src):
            nonlocal compared, matched
            gap = pct_gap(ours, theirs)
            if gap is None:
                row["items"].append({"metric": label, "ours": ours, "theirs": theirs, "source": src, "status": "n/a"})
                return
            ok = gap <= C.MISMATCH_TOLERANCE_PCT
            compared += 1
            matched += ok
            row["items"].append({"metric": label, "ours": ours, "theirs": theirs, "source": src,
                                 "gap_pct": round(gap, 1), "status": "match" if ok else "mismatch"})
        if nse_prices and rec["symbol"] in nse_prices:
            # compare InvestSights' own price with NSE (rec price may already be NSE)
            src_row = next((c for c in candidates if c["symbol"] == rec["symbol"]), {})
            add("Price", src_row.get("close_price"), nse_prices[rec["symbol"]]["close"], "NSE")
        if y:
            add("Market cap (Cr)", rec.get("market_cap"), y.get("market_cap_cr"), "Yahoo")
            add("P/E", rec.get("pe"), y.get("pe"), "Yahoo")
            if y.get("currency") in (None, "INR"):
                add("ROE %", rec.get("roe"), y.get("roe"), "Yahoo")
        row["flag"] = any(i["status"] == "mismatch" for i in row["items"])
        checks.append(row)
    for rec in pool:
        flagged = next((c for c in checks if c["symbol"] == rec["symbol"] and c["flag"]), None)
        rec["mismatch"] = bool(flagged)
    sources["Yahoo"] = {
        "ok": yahoo.calls == 0 or len(yahoo.errors) < yahoo.calls,
        "detail": f"{yahoo.calls} lookups, {len(yahoo.errors)} failed",
    }
    sources["InvestSights"] = {
        "ok": True,
        "detail": f"{inv.calls} requests used today (free plan allows 100)"
                  + (" - daily limit reached, some details reused from cache" if quota_hit else ""),
    }
    health = round(matched / compared * 100) if compared else None
    data_checks = {
        "sample_size": len(sample),
        "compared": compared,
        "matched": matched,
        "health_pct": health,
        "health_text": (f"Data check: {matched} of {compared} sampled numbers matched within "
                        f"{C.MISMATCH_TOLERANCE_PCT}%." if compared else "Data check: no comparisons possible today."),
        "rows": checks,
    }

    # ---- 6. Save everything ----
    shortlist.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
    latest = {
        "run_date": today.isoformat(),
        "run_time_ist": now.strftime("%Y-%m-%d %H:%M"),
        "baseline": baseline,
        "rules": C.rules_text(),
        "price_as_of": price_date or meta.get("price_as_of"),
        "counts": {"stage1": len(candidates), "shortlist": len(shortlist),
                   "manual": len(manual), "failed": len(failed)},
        "shortlist": shortlist,
        "manual": manual,
        "near_misses": [{"symbol": s, "reason": r, "screener_url": screener_url(s),
                         "name": next((c.get("company_name") for c in candidates if c["symbol"] == s), s)}
                        for s, r in sorted(failed.items())],
        "changes": {"added": added, "removed": removed},
        "data_checks": data_checks,
        "sources": sources,
        "notes": notes[:30] + nse.errors[:10] + yahoo.errors[:10],
    }
    save(SITE_DATA / "latest.json", latest)

    history = [h for h in history if h.get("date") != today.isoformat()]
    history.append({
        "date": today.isoformat(),
        "count": len(shortlist),
        "added": [{"symbol": a["symbol"], "name": a["name"]} for a in added],
        "removed": [{"symbol": r["symbol"], "name": r["name"], "reason": r["reason"]} for r in removed],
        "baseline": baseline,
    })
    save(SITE_DATA / "history.json", history[-400:])

    state = {
        "last_run": today.isoformat(),
        "members": {r["symbol"]: {"first_seen": r["first_seen"], "name": r["name"]} for r in shortlist},
    }
    save(STATE_DIR / "state.json", state)
    save(STATE_DIR / "cache.json", cache)

    # ---- 7. Email ----
    site = os.environ.get("SITE_URL", "")
    if baseline or added or removed:
        try:
            send_email(latest, site)
        except Exception as e:  # noqa: BLE001
            print(f"Email failed: {e}")
    else:
        print("No changes today, no email sent.")
    print(f"Done. Added {len(added)}, removed {len(removed)}.")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
