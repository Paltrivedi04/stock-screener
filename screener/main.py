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
    bs = sorted((raw.get("balance_sheet") or {}).get("data") or [],
                key=lambda r: r.get("period_end_date") or "", reverse=True)
    de = None
    if bs and bs[0].get("total_debt") is not None and (bs[0].get("shareholders_equity") or 0) > 0:
        de = bs[0]["total_debt"] / bs[0]["shareholders_equity"]
    # one row per year for the moat score
    by_end = {}
    for r in inc:
        e = r.get("period_end_date")
        if e:
            by_end.setdefault(e, {})["fy"] = r.get("fiscal_year")
            by_end[e].update(revenue=r.get("revenue"), ebitda=r.get("ebitda"),
                             ebit=r.get("ebit"), net_profit=r.get("net_profit"))
    for r in bs:
        e = r.get("period_end_date")
        if e in by_end:
            by_end[e].update(equity=r.get("shareholders_equity"), debt=r.get("total_debt"))
    for r in cfs:
        e = r.get("period_end_date")
        if e in by_end:
            by_end[e]["ocf"] = r.get("operating_cash_flow")
    years = [dict(end=e, **v) for e, v in sorted(by_end.items(), reverse=True)]
    return {"profit": profit, "ocf": ocf, "de": de, "years": years, "v": 2}


def moat_score(years):
    """Data-based moat score from the last few annual reports.
    Five checks, one point each: high ROCE, consistent ROE, pricing power,
    stable margins, and cash generation."""
    rows = [y for y in (years or [])[:C.MOAT_YEARS]
            if y.get("revenue") and y["revenue"] > 0 and y.get("net_profit") is not None]
    n = len(rows)
    if n < 3:
        return {"label": "Too new", "score": None, "years": n,
                "checks": [], "note": f"Only {n} year(s) of data; needs at least 3"}

    def ratio(a, b):
        return a / b * 100 if a is not None and b not in (None, 0) and b > 0 else None

    roce = [ratio(y.get("ebit"), (y.get("equity") or 0) + (y.get("debt") or 0)) for y in rows]
    roe = [ratio(y.get("net_profit"), y.get("equity")) for y in rows]
    margin = [ratio(y.get("ebitda"), y["revenue"]) for y in rows]
    ocf = [y.get("ocf") for y in rows]
    roce_v = [x for x in roce if x is not None]
    roe_v = [x for x in roe if x is not None]
    m_v = [x for x in margin if x is not None]
    ocf_v = [x for x in ocf if x is not None]
    need = min(C.MOAT_ROE_YEARS, n) if n < C.MOAT_YEARS else C.MOAT_ROE_YEARS
    need_ocf = min(C.MOAT_OCF_YEARS, n) if n < C.MOAT_YEARS else C.MOAT_OCF_YEARS

    avg_roce = sum(roce_v) / len(roce_v) if roce_v else None
    roe_good = sum(1 for x in roe_v if x > C.MOAT_ROE_MIN)
    avg_m = sum(m_v) / len(m_v) if m_v else None
    min_m = min(m_v) if m_v else None
    ocf_pos = sum(1 for x in ocf_v if x > 0)
    tot_ocf = sum(ocf_v) if ocf_v else None
    tot_np = sum(y["net_profit"] for y in rows if y.get("ocf") is not None)
    conv = tot_ocf / tot_np if tot_ocf is not None and tot_np > 0 else None

    checks = [
        {"name": "High returns", "detail": f"Average ROCE {avg_roce:.0f}% (needs above {C.MOAT_AVG_ROCE_MIN}%)" if avg_roce is not None else "ROCE not available",
         "ok": avg_roce is not None and avg_roce > C.MOAT_AVG_ROCE_MIN},
        {"name": "Consistent ROE", "detail": f"ROE above {C.MOAT_ROE_MIN}% in {roe_good} of {len(roe_v)} years (needs {need})",
         "ok": roe_good >= need},
        {"name": "Pricing power", "detail": f"Average EBITDA margin {avg_m:.0f}% (needs above {C.MOAT_AVG_MARGIN_MIN}%)" if avg_m is not None else "Margin not available",
         "ok": avg_m is not None and avg_m > C.MOAT_AVG_MARGIN_MIN},
        {"name": "Stable margins", "detail": f"Worst year {min_m:.0f}% vs average {avg_m:.0f}%" if avg_m else "Margin not available",
         "ok": bool(avg_m) and avg_m > 0 and min_m >= C.MOAT_MARGIN_FLOOR * avg_m},
        {"name": "Cash generation", "detail": (f"Cash flow positive in {ocf_pos} of {len(ocf_v)} years, cash-to-profit {conv:.0%}"
                                                if conv is not None else f"Cash flow positive in {ocf_pos} of {len(ocf_v)} years"),
         "ok": ocf_pos >= need_ocf and conv is not None and conv >= C.MOAT_CASH_CONVERSION},
    ]
    score = sum(c["ok"] for c in checks)
    label = "Wide" if score >= 5 else "Narrow" if score >= 3 else "None"
    return {"label": label, "score": score, "years": n,
            "period": f"{rows[-1].get('fy')} to {rows[0].get('fy')}", "checks": checks}


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
    rules_sig = " | ".join(C.rules_text())
    rules_changed = bool(state.get("last_run")) and state.get("rules") != rules_sig
    baseline = not state.get("last_run") or rules_changed
    if baseline:
        state["members"] = {}
        state["missing_members"] = []

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
    inv = InvestSights(key, budget=C.DAILY_REQUEST_BUDGET, min_interval=C.SECONDS_BETWEEN_REQUESTS)
    inv.connect()
    candidates, meta = inv.screen_all(C.stage1_filters(), C.MARKET_CAP_MIN_CR, C.MARKET_CAP_MAX_CR)
    print(f"Stage 1: {len(candidates)} companies")

    # ---- 2b. Companies with blank ROE or ROCE (listed separately) ----
    # Recent IPOs and some smaller companies have these blank in the screener table.
    missing_returns = []
    quota_hit = False
    try:
        base = C.missing_data_filters()
        lo, hi = C.MARKET_CAP_MIN_CR, C.MARKET_CAP_MAX_CR
        wide, _ = inv.screen_all(base, lo, hi)
        prev_missing = set(state.get("missing_members", []))
        cache.setdefault("fundamentals", {})
        for row in wide:
            sym = row["symbol"]
            if row.get("roe") is not None and row.get("roce") is not None:
                continue          # complete data: handled by the main screen
            de = row.get("debt_to_equity")
            if ((row.get("roe") is not None and row["roe"] <= C.ROE_MIN)
                    or (row.get("roce") is not None and row["roce"] <= C.ROCE_MIN)
                    or (de is not None and de >= C.DE_MAX)):
                continue          # already fails a known rule
            # The screener table can be blank where the company page has numbers,
            # so look those up (cached weekly) before calling them missing.
            if True:
                fd = cache["fundamentals"].get(sym)
                if not fresh(fd, C.STATEMENTS_CACHE_DAYS, today):
                    try:
                        raw = inv.fundamentals(sym)
                        prof = raw.get("profitability") or {}
                        fd = {"fetched": today.isoformat(), "roe": prof.get("roe"), "roce": prof.get("roce")}
                        cache["fundamentals"][sym] = fd
                    except QuotaReached:
                        quota_hit = True
                    except Exception as e:  # noqa: BLE001
                        notes.append(f"{sym}: fundamentals error {e}")
                if fd:
                    row = {**row, "roe": row.get("roe") if row.get("roe") is not None else fd.get("roe"),
                           "roce": row.get("roce") if row.get("roce") is not None else fd.get("roce")}
            blanks, fails = [], False
            for label, key, minimum in (("ROE", "roe", C.ROE_MIN), ("ROCE", "roce", C.ROCE_MIN)):
                if row.get(key) is None:
                    blanks.append(label)
                elif row[key] <= minimum:
                    fails = True
            if fails:
                continue          # filled-in numbers fail a rule
            if not blanks:        # now complete and passing: check it like any other company
                if any(c["symbol"] == sym for c in candidates):
                    continue
                candidates.append({**row, "data_note": "ROE/ROCE taken from the company page (blank in the screener table)"})
                continue
            price = row.get("close_price")
            if nse_prices and sym in nse_prices and nse_prices[sym]["close"]:
                price = nse_prices[sym]["close"]
            missing_returns.append({
                "symbol": sym,
                "name": row.get("company_name") or (listed or {}).get(sym, sym),
                "sector": row.get("sector"),
                "market_cap": row.get("market_cap"),
                "price": price,
                "pe": row.get("pe_ratio"),
                "roe": row.get("roe"),
                "roce": row.get("roce"),
                "de": row.get("debt_to_equity"),
                "missing": ", ".join(blanks),
                "new": (not baseline) and sym not in prev_missing,
                "screener_url": screener_url(sym),
            })
        missing_returns.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
        print(f"Missing data: {len(missing_returns)} companies")
    except QuotaReached:
        notes.append("Missing data list skipped: daily request limit reached")
    except Exception as e:  # noqa: BLE001
        notes.append(f"Missing data list error: {e}")

    yahoo = Yahoo()
    shortlist, manual, failed = [], [], {}

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

        # Debt to Equity (known value checked before spending a request)
        if rec["de"] is not None and rec["de"] >= C.DE_MAX:
            failed[sym] = f"Debt to Equity {rec['de']:.2f} (needs below {C.DE_MAX:g})"
            continue

        # Statements (cached weekly)
        st = cache["statements"].get(sym)
        if not fresh(st, C.STATEMENTS_CACHE_DAYS, today) or (st or {}).get("v") != 2:
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

        if rec["de"] is None:
            rec["de"] = st.get("de")
            if rec["de"] is None:
                rec["reason"] = "Debt to Equity not available"
                manual.append(rec)
                continue
            rec["de_note"] = "D/E calculated from the latest balance sheet"
            if rec["de"] >= C.DE_MAX:
                failed[sym] = f"Debt to Equity {rec['de']:.2f} (needs below {C.DE_MAX:g})"
                continue

        # Sales and profit growth, 3-year CAGR (shown for information, not a rule)
        profits = [p for p in st["profit"] if p.get("net_profit") is not None]
        if len(profits) >= 4:
            rec["profit_cagr_3y"] = cagr(profits[3]["net_profit"], profits[0]["net_profit"], 3)
            rec["profit_years"] = f"{profits[3]['fy']} to {profits[0]['fy']}"
        if profits:
            rec["net_profit_cr"] = profits[0]["net_profit"] / 1e7
        revs = [p.get("revenue") for p in st["profit"]]
        if len(revs) >= 4 and revs[0] and revs[3]:
            rec["sales_cagr_3y"] = cagr(revs[3], revs[0], 3)
        rec["moat"] = moat_score(st.get("years"))

        # Operating cash flow: latest year (FY26) must be positive
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
        # show up to 3 years for context; only the rule years decide
        shown = ocf if ocf_source != "InvestSights" else [o for o in st["ocf"][:3] if o.get("ocf") is not None]
        rec["ocf"] = [{"fy": o["fy"], "cr": round(o["ocf"] / 1e7, 1)} for o in shown]
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
            reason = "No longer passes the main screen (market cap, ROE or ROCE)"
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
        "rules_changed": rules_changed,
        "rules": C.rules_text(),
        "price_as_of": price_date or meta.get("price_as_of"),
        "counts": {"stage1": len(candidates), "shortlist": len(shortlist),
                   "manual": len(manual), "failed": len(failed),
                   "missing_returns": len(missing_returns)},
        "shortlist": shortlist,
        "manual": manual,
        "missing_returns": missing_returns,
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
        "rules": rules_sig,
        "members": {r["symbol"]: {"first_seen": r["first_seen"], "name": r["name"]} for r in shortlist},
        "missing_members": [r["symbol"] for r in missing_returns],
    }
    save(STATE_DIR / "state.json", state)
    save(STATE_DIR / "cache.json", cache)

    # ---- 7. Email ----
    site = os.environ.get("SITE_URL", "")
    if baseline or added or removed or any(m.get("new") for m in missing_returns):
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
