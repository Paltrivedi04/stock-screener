"""Official NSE data: company list, daily closing prices and filings.
NSE sometimes blocks cloud servers. Every function here fails softly and
returns None, so the daily run carries on with the other sources."""
import csv
import datetime as dt
import io
import re
import zipfile

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.primed = False
        self.errors = []

    def _prime(self):
        if self.primed:
            return
        try:
            self.s.get("https://www.nseindia.com/", timeout=20)
            self.s.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", timeout=20)
        except requests.RequestException as e:
            self.errors.append(f"NSE home page: {e}")
        self.primed = True

    # ---------- master list ----------
    def equity_list(self):
        url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        try:
            r = self.s.get(url, timeout=30)
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text))
            out = {}
            for row in reader:
                row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                sym = row.get("SYMBOL")
                if sym:
                    out[sym] = row.get("NAME OF COMPANY", sym)
            return out or None
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"NSE company list: {e}")
            return None

    # ---------- daily prices ----------
    def bhavcopy(self, today=None, look_back=7):
        """Latest available end-of-day prices. Returns (date_str, {symbol: {...}})."""
        today = today or dt.date.today()
        for i in range(look_back):
            d = today - dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            url = (
                "https://nsearchives.nseindia.com/content/cm/"
                f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
            )
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code != 200 or not r.content.startswith(b"PK"):
                    continue
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    name = z.namelist()[0]
                    text = z.read(name).decode("utf-8", "ignore")
                prices = {}
                for row in csv.DictReader(io.StringIO(text)):
                    if row.get("SctySrs", "").strip() not in ("EQ", "BE", "BZ", "SM", "ST"):
                        continue
                    sym = row.get("TckrSymb", "").strip()
                    try:
                        prices[sym] = {
                            "close": float(row.get("ClsPric") or 0),
                            "prev_close": float(row.get("PrvsClsgPric") or 0),
                            "volume": float(row.get("TtlTradgVol") or 0),
                            "traded_value": float(row.get("TtlTrfVal") or 0),
                        }
                    except ValueError:
                        continue
                if prices:
                    return d.isoformat(), prices
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"NSE bhavcopy {d}: {e}")
        return None, None

    # ---------- filings ----------
    def announcements(self, symbol, days=400):
        self._prime()
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        url = (
            "https://www.nseindia.com/api/corporate-announcements?index=equities"
            f"&symbol={requests.utils.quote(symbol)}"
            f"&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
        )
        try:
            r = self.s.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            return data if isinstance(data, list) else []
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"NSE announcements {symbol}: {e}")
            return None


# ---------- reading the filings ----------
def _date(item):
    for key in ("sort_date", "an_dt", "exchdisstime", "dt"):
        v = item.get(key)
        if not v:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(str(v).strip(), fmt)
            except ValueError:
                continue
    return None


def summarize_filings(items):
    """Finds the latest concall transcript/recording, the latest investor
    presentation, a recent call intimation and how regular calls are."""
    if items is None:
        return None
    out = {
        "latest_transcript": None,
        "latest_presentation": None,
        "upcoming_call": None,
        "calls_last_12m": 0,
        "regular": None,
    }
    call_quarters = set()
    now = dt.datetime.now()
    intimations = []
    for it in items:
        desc = (it.get("desc") or "").lower()
        text = (it.get("attchmntText") or "").lower()
        when = _date(it)
        link = it.get("attchmntFile") or ""
        both = f"{desc} {text}"
        entry = {"date": when.date().isoformat() if when else None, "link": link,
                 "title": (it.get("attchmntText") or it.get("desc") or "")[:180]}

        is_transcript = bool(re.search(r"transcript|audio recording|audio/video recording|recording of", both))
        is_presentation = "investor presentation" in both or (
            "presentation" in both and ("investor" in both or "analyst" in both or "earnings" in both)
        )
        is_intimation = ("con. call" in desc or "conference call" in both or "earnings call" in both
                         or "analysts/institutional investor meet" in desc) and not is_transcript

        if is_transcript and when:
            if not out["latest_transcript"] or when.date().isoformat() > out["latest_transcript"]["date"]:
                out["latest_transcript"] = entry
            if (now - when).days <= 365:
                call_quarters.add((when.year, (when.month - 1) // 3))
        if is_presentation and when:
            if not out["latest_presentation"] or when.date().isoformat() > out["latest_presentation"]["date"]:
                out["latest_presentation"] = entry
        if is_intimation and when and (now - when).days <= 45:
            intimations.append(entry)

    out["calls_last_12m"] = len(call_quarters)
    out["regular"] = len(call_quarters) >= 3
    if intimations:
        intimations.sort(key=lambda e: e["date"] or "", reverse=True)
        latest = intimations[0]
        last_tr = out["latest_transcript"]["date"] if out["latest_transcript"] else ""
        if (latest["date"] or "") > last_tr:
            out["upcoming_call"] = latest
    return out
