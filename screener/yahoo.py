"""Yahoo Finance: fills missing cash flow data and gives a second opinion
on a daily sample. Used lightly to stay within Yahoo's limits."""
import time

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


class Yahoo:
    def __init__(self, pause=1.5):
        self.pause = pause
        self.errors = []
        self.calls = 0

    def _ticker(self, symbol):
        if yf is None:
            raise RuntimeError("yfinance not installed")
        time.sleep(self.pause)
        self.calls += 1
        return yf.Ticker(f"{symbol}.NS")

    def operating_cash_flows(self, symbol):
        """Annual operating cash flow, newest first: [(year, value), ...]"""
        try:
            t = self._ticker(symbol)
            cf = t.cashflow
            if cf is None or cf.empty:
                return None
            row = None
            for name in ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                         "Total Cash From Operating Activities"):
                if name in cf.index:
                    row = cf.loc[name]
                    break
            if row is None:
                return None
            values = []
            for col, v in row.items():
                if v is None or v != v:  # NaN
                    continue
                values.append((str(getattr(col, "year", col)), float(v)))
            values.sort(key=lambda x: x[0], reverse=True)
            return values or None
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"Yahoo cash flow {symbol}: {e}")
            return None

    def snapshot(self, symbol):
        """Key numbers for cross-checking. Market cap in Rs Crore."""
        try:
            info = self._ticker(symbol).info or {}
            mcap = info.get("marketCap")
            roe = info.get("returnOnEquity")
            return {
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "market_cap_cr": mcap / 1e7 if mcap else None,
                "pe": info.get("trailingPE"),
                "roe": roe * 100 if roe is not None else None,
                "currency": info.get("financialCurrency"),
            }
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"Yahoo snapshot {symbol}: {e}")
            return None
